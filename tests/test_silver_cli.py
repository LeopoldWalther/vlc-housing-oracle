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

from src.silver.rebuild import DAY_TABLES, main
from src.synthetic.landing import generate_landing_v3

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
        output = io.StringIO()
        with (
            mock.patch("src.silver.rebuild.load_catalog", return_value=catalog),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = main(list(arguments), **clients)
        return code, output.getvalue()

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

        self.assertEqual(1, completed.returncode)
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
            [("PendingRuns", 0)],
            [(d["MetricName"], d["Value"]) for d in metric["MetricData"]],
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

        self.assertEqual(1, code)
        self.assertEqual(self.runs, cloudwatch.published[0]["MetricData"][0]["Value"])


if __name__ == "__main__":
    unittest.main()
