"""Landing v3 commits: parsed strictly, pending by set difference, and the runs that touch a day."""

import unittest
from datetime import UTC, date, datetime

from src.contracts import landing_v3_sha256
from src.silver.commits import days_of, parse_commit, pending, runs_for
from src.silver.landing import LandingContractError

TABLES = ("walk", "observation", "listing_detail", "listing_text")


def raw_commit(run_id: str, started: str, days: dict[str, list[str]] | None = None) -> dict:
    days = days if days is not None else {table: ["2026-10-20"] for table in TABLES}
    return {
        "contract": "landing",
        "version": "v3",
        "schema_sha256": landing_v3_sha256(),
        "run_id": run_id,
        "run_started_at": started,
        "outcome": "ok",
        "is_replay": False,
        "row_counts": dict.fromkeys(TABLES, 1),
        "observed_dates": days,
        "written_at": "2026-10-20T05:50:12Z",
    }


class TestParseCommit(unittest.TestCase):
    def test_a_conforming_commit_is_parsed(self) -> None:
        commit = parse_commit(raw_commit("20261020T030000Z", "2026-10-20T03:00:00Z"))

        self.assertEqual("20261020T030000Z", commit.run_id)
        self.assertEqual(datetime(2026, 10, 20, 3, 0, tzinfo=UTC), commit.run_started_at)
        self.assertEqual({date(2026, 10, 20)}, commit.observed_dates["observation"])
        self.assertEqual(1, commit.row_counts["walk"])
        self.assertFalse(commit.is_replay)

    def test_a_missing_field_is_refused_naming_the_run(self) -> None:
        raw = raw_commit("20261020T030000Z", "2026-10-20T03:00:00Z")
        del raw["observed_dates"]

        with self.assertRaisesRegex(LandingContractError, "20261020T030000Z.*observed_dates"):
            parse_commit(raw)

    def test_a_wrong_version_or_digest_is_refused(self) -> None:
        for field, value in (
            ("version", "v2"),
            ("contract", "bronze"),
            ("schema_sha256", "0" * 64),
        ):
            raw = raw_commit("20261020T030000Z", "2026-10-20T03:00:00Z")
            raw[field] = value
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(LandingContractError, "20261020T030000Z"),
            ):
                parse_commit(raw)

    def test_a_table_missing_from_the_counts_is_refused(self) -> None:
        raw = raw_commit("20261020T030000Z", "2026-10-20T03:00:00Z")
        del raw["row_counts"]["listing_text"]

        with self.assertRaisesRegex(LandingContractError, "listing_text"):
            parse_commit(raw)


class TestPending(unittest.TestCase):
    def setUp(self) -> None:
        self.live = parse_commit(raw_commit("20261020T030000Z", "2026-10-20T03:00:00Z"))
        self.earlier = parse_commit(raw_commit("20261019T210000Z", "2026-10-19T21:00:00Z"))
        # An import's id sorts after every timestamp id, but its start is older than both.
        self.imported = parse_commit(raw_commit("import-20261001", "2026-10-01T00:00:00Z"))

    def test_an_empty_ingest_log_makes_every_commit_pending(self) -> None:
        self.assertEqual(
            ["import-20261001", "20261019T210000Z", "20261020T030000Z"],
            [c.run_id for c in pending([self.live, self.imported, self.earlier], set())],
        )

    def test_ingested_runs_are_not_pending(self) -> None:
        result = pending([self.live, self.imported, self.earlier], {"20261019T210000Z"})

        self.assertEqual(["import-20261001", "20261020T030000Z"], [c.run_id for c in result])

    def test_everything_ingested_means_nothing_pending(self) -> None:
        everything = {"import-20261001", "20261019T210000Z", "20261020T030000Z"}

        self.assertEqual([], pending([self.live, self.imported, self.earlier], everything))


class TestDaysAndRuns(unittest.TestCase):
    def test_days_are_the_union_per_table(self) -> None:
        first = parse_commit(
            raw_commit(
                "20261020T030000Z",
                "2026-10-20T03:00:00Z",
                {
                    "walk": ["2026-10-20"],
                    "observation": ["2026-10-20", "2026-10-21"],
                    "listing_detail": [],
                    "listing_text": [],
                },
            )
        )
        second = parse_commit(
            raw_commit(
                "20261021T030000Z",
                "2026-10-21T03:00:00Z",
                {
                    "walk": ["2026-10-21"],
                    "observation": ["2026-10-21"],
                    "listing_detail": ["2026-10-21"],
                    "listing_text": ["2026-10-21"],
                },
            )
        )

        days = days_of([first, second])

        self.assertEqual({date(2026, 10, 20), date(2026, 10, 21)}, days["observation"])
        self.assertEqual({date(2026, 10, 21)}, days["listing_detail"])
        self.assertEqual(set(TABLES), set(days))

    def test_every_known_run_touching_a_named_day_is_returned(self) -> None:
        days = {table: {date(2026, 10, 20)} for table in TABLES}
        known = {
            "20261020T030000Z": {table: {date(2026, 10, 20)} for table in TABLES},
            "20261020T060000Z": {"observation": {date(2026, 10, 20)}},
            "20261019T210000Z": {"observation": {date(2026, 10, 19)}},
        }

        runs = runs_for(days, known)

        self.assertEqual({"20261020T030000Z", "20261020T060000Z"}, runs["observation"])
        self.assertEqual({"20261020T030000Z"}, runs["walk"])


if __name__ == "__main__":
    unittest.main()
