"""The operator, or the Fargate task, runs Silver from the command line against landing v3."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pyiceberg.catalog.sql import SqlCatalog

from src.reference.census_sections import convert
from src.silver.landing import LandingContractError
from src.silver.rebuild import DAY_TABLES, main
from src.synthetic.landing import generate_landing_v3
from src.synthetic.reference import PROVINCE, SYNTHETIC_CRS, write_census_archive

REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeS3:
    """Serves a local landing v3 tree's commits as if it were s3://syn-bucket/landing/v3/."""

    def __init__(self, landing: Path) -> None:
        self.landing = landing
        self.read: list[str] = []

    def get_paginator(self, name: str):
        assert name == "list_objects_v2"
        landing = self.landing

        class Paginator:
            def paginate(self, Bucket: str, Prefix: str):  # noqa: N803 - boto3's spelling
                assert (Bucket, Prefix) == ("syn-bucket", "landing/v3/_commits/")
                keys = [f"{Prefix}{p.name}" for p in sorted((landing / "_commits").glob("*.json"))]
                yield {"Contents": [{"Key": key} for key in keys]}

        return Paginator()

    def get_object(self, Bucket: str, Key: str):  # noqa: N803 - boto3's spelling
        self.read.append(Key)
        body = (self.landing / "_commits" / Key.rsplit("/", 1)[1]).read_bytes()
        return {"Body": io.BytesIO(body)}


class FakeCloudWatch:
    def __init__(self) -> None:
        self.published: list[dict] = []

    def put_metric_data(self, **kwargs) -> None:
        self.published.append(kwargs)


class TestSilverCli(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.landing = self.root / "landing" / "v3"
        self.landing.mkdir(parents=True)
        generate_landing_v3(self.landing, seed=1)
        self.runs = len(list((self.landing / "_commits").glob("*.json")))
        # PYICEBERG_HOME keeps a developer's own ~/.pyiceberg.yaml out of the test.
        self.environment = {
            name: value for name, value in os.environ.items() if not name.startswith("PYICEBERG_")
        } | {
            "PYICEBERG_HOME": str(self.root),
            "PYICEBERG_CATALOG__TEST__URI": f"sqlite:///{self.root}/catalog.db",
            "PYICEBERG_CATALOG__TEST__WAREHOUSE": f"file://{self.root}",
        }

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "src.silver.rebuild", *arguments],
            cwd=REPO_ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def run_main(self, *arguments: str, **clients) -> tuple[int, str]:
        # PyIceberg reads its environment once, at import; in-process runs get the same SQLite
        # catalog the subprocess runs configure through PYICEBERG_CATALOG__TEST__*.
        catalog = SqlCatalog(
            "test", uri=f"sqlite:///{self.root}/catalog.db", warehouse=f"file://{self.root}"
        )
        output, errors = io.StringIO(), io.StringIO()
        with (
            mock.patch("src.silver.rebuild.load_catalog", return_value=catalog),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            code = main(list(arguments), **clients)
        self.errors = errors.getvalue()
        return code, output.getvalue()

    def published(self, cloudwatch: FakeCloudWatch) -> list[list[tuple[str, int]]]:
        return [
            [(d["MetricName"], d["Value"]) for d in call["MetricData"]]
            for call in cloudwatch.published
        ]

    def ingest_all_but(self, held_back: int) -> None:
        """Process every commit except the last ``held_back``, which stay pending."""
        commits = sorted((self.landing / "_commits").glob("*.json"))[-held_back:]
        aside = self.root / "aside"
        aside.mkdir()
        for commit in commits:
            commit.rename(aside / commit.name)
        self.run_main("--landing", str(self.landing), "--catalog", "test")
        for commit in commits:
            (aside / commit.name).rename(commit)

    def census_sections(self) -> str:
        """A converted synthetic vintage directory."""
        write_census_archive(self.root / "sections.zip")
        directory = convert(
            self.root / "sections.zip",
            province=PROVINCE,
            crs=SYNTHETIC_CRS,
            vintage="2026-01-01",
            source="https://example.org/sections.zip",
            licence="invented",
            retrieved="2026-03-20",
            out=self.root / "reference",
        )
        return str(directory)

    def walk_file_with_rows(self) -> Path:
        for commit in sorted((self.landing / "_commits").glob("*.json")):
            if json.loads(commit.read_text())["row_counts"]["walk"]:
                return next(self.landing.glob(f"walk/*/run={commit.stem}/part-0.parquet"))
        raise AssertionError("the synthetic landing has no walk rows")

    def test_an_incremental_run_prints_pending_runs_days_and_snapshots(self) -> None:
        completed = self.run_cli("--landing", str(self.landing), "--catalog", "test")

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn(f"pending: {self.runs}", completed.stdout)
        for table in DAY_TABLES:
            with self.subTest(table=table):
                self.assertRegex(completed.stdout, rf"(?m)^{table}: \d+ days, snapshot \d+$")
        self.assertRegex(completed.stdout, r"(?m)^listing: snapshot \d+$")
        self.assertRegex(completed.stdout, r"(?m)^listing_quality: snapshot \d+$")
        self.assertRegex(completed.stdout, r"(?m)^listing_event: snapshot \d+$")
        self.assertRegex(completed.stdout, r"(?m)^listing_property_group: snapshot \d+$")
        self.assertRegex(completed.stdout, r"(?m)^listing_price: snapshot \d+$")
        self.assertRegex(completed.stdout, r"(?m)^feature_pattern: snapshot \d+$")
        self.assertRegex(completed.stdout, r"(?m)^listing_feature_label: snapshot \d+$")
        self.assertRegex(completed.stdout, r"(?m)^unknown feature patterns: 1$")

    def test_a_second_run_finds_nothing_pending(self) -> None:
        self.run_cli("--landing", str(self.landing), "--catalog", "test")

        completed = self.run_cli("--landing", str(self.landing), "--catalog", "test")

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("nothing pending", completed.stdout)

    def test_a_backfill_processes_every_run_again(self) -> None:
        self.run_cli("--landing", str(self.landing), "--catalog", "test")

        completed = self.run_cli(
            "--landing", str(self.landing), "--catalog", "test", "--mode", "backfill"
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn(f"pending: {self.runs}", completed.stdout)
        self.assertRegex(completed.stdout, r"(?m)^listing_quality: snapshot \d+$")
        self.assertRegex(completed.stdout, r"(?m)^listing_event: snapshot \d+$")
        self.assertRegex(completed.stdout, r"(?m)^listing_property_group: snapshot \d+$")
        self.assertRegex(completed.stdout, r"(?m)^listing_price: snapshot \d+$")
        self.assertRegex(completed.stdout, r"(?m)^feature_pattern: snapshot \d+$")
        self.assertRegex(completed.stdout, r"(?m)^listing_feature_label: snapshot \d+$")
        self.assertRegex(completed.stdout, r"(?m)^unknown feature patterns: 1$")

    def test_a_missing_landing_directory_is_a_usage_error(self) -> None:
        completed = self.run_cli("--landing", str(self.root / "absent"), "--catalog", "test")

        self.assertEqual(2, completed.returncode)
        self.assertIn("absent", completed.stderr)

    def test_an_unknown_mode_is_a_usage_error(self) -> None:
        completed = self.run_cli(
            "--landing", str(self.landing), "--catalog", "test", "--mode", "rebuild"
        )

        self.assertEqual(2, completed.returncode)

    def test_a_non_conforming_landing_fails_naming_the_table(self) -> None:
        self.walk_file_with_rows().unlink()

        completed = self.run_cli("--landing", str(self.landing), "--catalog", "test")

        self.assertEqual(3, completed.returncode)
        self.assertIn("walk", completed.stderr)

    def test_s3_commits_are_listed_and_only_pending_ones_read(self) -> None:
        self.run_cli("--landing", str(self.landing), "--catalog", "test")
        s3 = FakeS3(self.landing)

        code, output = self.run_main(
            "--landing", "s3://syn-bucket/landing/v3/", "--catalog", "test", s3_client=s3
        )

        self.assertEqual(0, code)
        self.assertIn("nothing pending", output)
        self.assertEqual([], s3.read)

    def test_metrics_publish_the_pending_runs_after_the_run(self) -> None:
        cloudwatch = FakeCloudWatch()

        code, _ = self.run_main(
            "--landing",
            str(self.landing),
            "--catalog",
            "test",
            "--metrics",
            cloudwatch_client=cloudwatch,
        )

        self.assertEqual(0, code)
        metric = cloudwatch.published[0]
        self.assertEqual("vlc-housing-silver", metric["Namespace"])
        self.assertEqual(
            [("PendingRuns", 0, "Count"), ("UnknownFeaturePatterns", 1, "Count")],
            [(d["MetricName"], d["Value"], d["Unit"]) for d in metric["MetricData"]],
        )
        self.assertEqual(1, len(cloudwatch.published))

    def test_a_run_with_nothing_pending_publishes_the_pending_runs_alone(self) -> None:
        arguments = ("--landing", str(self.landing), "--catalog", "test", "--metrics")
        self.run_main(*arguments, cloudwatch_client=FakeCloudWatch())
        cloudwatch = FakeCloudWatch()

        code, output = self.run_main(*arguments, cloudwatch_client=cloudwatch)

        self.assertEqual(0, code)
        self.assertNotIn("unknown feature patterns", output)
        self.assertEqual(
            [[("PendingRuns", 0)]],
            [
                [(d["MetricName"], d["Value"]) for d in call["MetricData"]]
                for call in cloudwatch.published
            ],
        )

    def test_a_refused_run_publishes_its_pending_runs(self) -> None:
        self.walk_file_with_rows().unlink()
        cloudwatch = FakeCloudWatch()

        code, _ = self.run_main(
            "--landing",
            str(self.landing),
            "--catalog",
            "test",
            "--metrics",
            cloudwatch_client=cloudwatch,
        )

        self.assertEqual(3, code)
        self.assertEqual(self.runs, cloudwatch.published[0]["MetricData"][0]["Value"])

    def test_a_run_given_census_sections_prints_the_location_snapshot_after_the_labels(
        self,
    ) -> None:
        code, output = self.run_main(
            "--landing",
            str(self.landing),
            "--catalog",
            "test",
            "--census-sections",
            self.census_sections(),
        )

        self.assertEqual(0, code)
        lines = output.splitlines()
        located = [line for line in lines if line.startswith("listing_location:")]
        self.assertEqual(1, len(located))
        self.assertRegex(located[0], r"^listing_location: snapshot \d+$")
        position = lines.index(located[0])
        self.assertRegex(lines[position - 1], r"^listing_feature_label: snapshot \d+$")
        self.assertRegex(lines[position + 1], r"^unknown feature patterns: \d+$")

    def test_a_run_without_census_sections_prints_no_location_line(self) -> None:
        code, output = self.run_main("--landing", str(self.landing), "--catalog", "test")

        self.assertEqual(0, code)
        self.assertNotIn("listing_location", output)

    def test_a_run_given_text_labels_prints_the_table_snapshot_after_the_labels(self) -> None:
        labels = self.root / "labels"
        labels.mkdir()

        code, output = self.run_main(
            "--landing", str(self.landing), "--catalog", "test", "--text-labels", str(labels)
        )

        self.assertEqual(0, code)
        lines = output.splitlines()
        written = [line for line in lines if line.startswith("listing_text_label:")]
        self.assertEqual(1, len(written))
        self.assertRegex(written[0], r"^listing_text_label: snapshot \d+$")
        position = lines.index(written[0])
        self.assertRegex(lines[position - 1], r"^listing_feature_label: snapshot \d+$")
        self.assertRegex(lines[position + 1], r"^unknown feature patterns: \d+$")

    def test_a_run_without_text_labels_prints_no_text_label_line(self) -> None:
        code, output = self.run_main("--landing", str(self.landing), "--catalog", "test")

        self.assertEqual(0, code)
        self.assertNotIn("listing_text_label", output)

    def test_a_missing_census_sections_directory_exits_3_naming_the_path(self) -> None:
        missing = str(self.root / "reference" / "no-such-vintage")

        completed = self.run_cli(
            "--landing", str(self.landing), "--catalog", "test", "--census-sections", missing
        )

        self.assertEqual(3, completed.returncode, completed.stderr)
        self.assertIn(missing, completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_a_run_refused_for_its_census_sections_publishes_its_pending_runs(self) -> None:
        cloudwatch = FakeCloudWatch()

        code, _ = self.run_main(
            "--landing",
            str(self.landing),
            "--catalog",
            "test",
            "--census-sections",
            str(self.root / "reference" / "no-such-vintage"),
            "--metrics",
            cloudwatch_client=cloudwatch,
        )

        self.assertEqual(3, code)
        self.assertEqual(
            [[("PendingRuns", self.runs)]],
            [
                [(d["MetricName"], d["Value"]) for d in call["MetricData"]]
                for call in cloudwatch.published
            ],
        )

    def test_a_commit_of_another_landing_version_exits_3_publishing_nothing(self) -> None:
        commit = sorted((self.landing / "_commits").glob("*.json"))[0]
        commit.write_text(json.dumps(json.loads(commit.read_text()) | {"version": "v4"}))
        cloudwatch = FakeCloudWatch()

        code, _ = self.run_main(
            "--landing",
            str(self.landing),
            "--catalog",
            "test",
            "--metrics",
            cloudwatch_client=cloudwatch,
        )

        self.assertEqual(3, code)
        self.assertIn("is not landing v3", self.errors)
        self.assertNotIn("Traceback", self.errors)
        self.assertEqual([], cloudwatch.published)

    def test_another_rule_version_exits_3_publishing_the_pending_runs(self) -> None:
        for held_back in (2, 0):
            with self.subTest(pending=held_back):
                self.setUp()
                if held_back:
                    self.ingest_all_but(held_back)
                else:
                    self.run_main("--landing", str(self.landing), "--catalog", "test")
                cloudwatch = FakeCloudWatch()

                with mock.patch("src.silver.rebuild.TRANSFORMATION_VERSION", "silver-v99"):
                    code, output = self.run_main(
                        "--landing",
                        str(self.landing),
                        "--catalog",
                        "test",
                        "--metrics",
                        cloudwatch_client=cloudwatch,
                    )

                self.assertEqual(3, code)
                self.assertIn("backfill", self.errors)
                self.assertNotIn("nothing pending", output)
                self.assertEqual([[("PendingRuns", held_back)]], self.published(cloudwatch))

    def test_a_backfill_is_not_refused_for_another_rule_version(self) -> None:
        self.run_main("--landing", str(self.landing), "--catalog", "test")

        with mock.patch("src.silver.rebuild.TRANSFORMATION_VERSION", "silver-v99"):
            code, _ = self.run_main(
                "--landing", str(self.landing), "--catalog", "test", "--mode", "backfill"
            )

        self.assertEqual(0, code, self.errors)

    def test_a_refusal_names_the_type_of_its_cause_and_none_of_its_text(self) -> None:
        def refuse(*args, **kwargs):
            raise LandingContractError("landing table walk: run r1 is refused") from ValueError(
                "secret-cause-text"
            )

        with mock.patch("src.silver.rebuild.process_landing", side_effect=refuse):
            code, _ = self.run_main("--landing", str(self.landing), "--catalog", "test")

        self.assertEqual(3, code)
        self.assertEqual(
            "Silver run refused: landing table walk: run r1 is refused [cause: builtins.ValueError]\n",
            self.errors,
        )

    def test_a_failed_read_exits_1_with_a_traceback(self) -> None:
        script = (
            "import sys; from unittest import mock; import duckdb; "
            "from src.silver.rebuild import main; "
            "boom = duckdb.IOException('boom'); "
            "patched = mock.patch('src.silver.rebuild.process_landing', side_effect=boom); "
            "patched.start(); "
            "sys.exit(main(sys.argv[1:]))"
        )

        completed = subprocess.run(
            [sys.executable, "-c", script, "--landing", str(self.landing), "--catalog", "test"],
            cwd=REPO_ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(1, completed.returncode, completed.stderr)
        self.assertIn("Traceback", completed.stderr)
        self.assertIn("IOException: boom", completed.stderr)
        self.assertNotIn("Silver run refused", completed.stderr)


if __name__ == "__main__":
    unittest.main()
