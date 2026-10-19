"""Landing v3 is read per run, and every named file is checked before Silver reads a row of it."""

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from src.silver.landing import LandingContractError, register_landing_v3
from src.synthetic.landing import write_landing_v3

RUN_A, RUN_B = "20261001T060000Z", "20261001T120000Z"
STARTED = {
    RUN_A: datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    RUN_B: datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
}
TABLES = ("walk", "observation", "listing_detail", "listing_text")


def key(run_id: str, listing_id: str = "syn-1") -> dict[str, object]:
    observed_at = STARTED[run_id].replace(minute=30)
    return {
        "source": "syn",
        "run_id": run_id,
        "area": "alba-sale",
        "source_listing_id": listing_id,
        "observed_at": observed_at,
        "observed_date": observed_at.date(),
    }


def card(run_id: str, listing_id: str = "syn-1", **overrides: object) -> dict[str, object]:
    return key(run_id, listing_id) | {"is_empty_card": False} | overrides


class TestRegisterLandingV3(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.connection = duckdb.connect()
        self.addCleanup(self.connection.close)

    def write(self, rows_by_table: dict) -> None:
        write_landing_v3(self.root, rows_by_table)

    def register(self, runs_by_table: dict | None = None, counts: dict | None = None) -> None:
        written = {path.stem for path in (self.root / "_commits").glob("*.json")}
        runs = runs_by_table or dict.fromkeys(TABLES, written)
        register_landing_v3(self.connection, str(self.root), runs, STARTED, counts or {})

    def count(self, view: str) -> int:
        return self.connection.sql(f"SELECT count(*) FROM {view}").fetchone()[0]

    def test_exactly_the_named_runs_are_registered(self) -> None:
        self.write(
            {
                "observation": [card(RUN_A), card(RUN_B)],
                "listing_detail": [key(RUN_A)],
                "listing_text": [key(RUN_A)],
            }
        )

        self.register(dict.fromkeys(TABLES, {RUN_B}))

        self.assertEqual(1, self.count("landing_observation"))
        self.assertEqual(0, self.count("landing_listing_detail"))
        self.assertEqual(
            {RUN_A, RUN_B},
            {r[0] for r in self.connection.sql("SELECT run_id FROM landing_runs").fetchall()},
        )

    def test_a_table_without_named_runs_is_an_empty_typed_view(self) -> None:
        self.write({"observation": [card(RUN_A)]})

        self.register(
            {"walk": set(), "observation": {RUN_A}, "listing_detail": set(), "listing_text": set()}
        )

        self.assertEqual(0, self.count("landing_walk"))
        self.assertIn(
            "run_id", [c[0] for c in self.connection.sql("DESCRIBE landing_walk").fetchall()]
        )

    def test_a_row_count_that_disagrees_with_the_commit_is_refused(self) -> None:
        self.write({"observation": [card(RUN_A)]})

        with self.assertRaisesRegex(LandingContractError, f"observation.*{RUN_A}.*2.*1"):
            self.register(
                counts={
                    RUN_A: {"walk": 0, "observation": 2, "listing_detail": 0, "listing_text": 0}
                }
            )

    def test_a_null_key_is_refused(self) -> None:
        self.write({"observation": [card(RUN_A, is_empty_card=None)]})

        with self.assertRaisesRegex(LandingContractError, "is_empty_card"):
            self.register()

    def test_a_duplicate_key_within_a_run_is_refused_but_across_runs_allowed(self) -> None:
        self.write({"observation": [card(RUN_A), card(RUN_B)]})
        self.register()
        self.assertEqual(2, self.count("landing_observation"))

        other = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write_landing_v3(other, {"observation": [card(RUN_A), card(RUN_A, price_eur=1)]})
        with self.assertRaisesRegex(LandingContractError, "observation.*more than once"):
            register_landing_v3(
                self.connection, str(other), dict.fromkeys(TABLES, {RUN_A}), STARTED, {}
            )

    def test_an_unpaired_detail_is_refused(self) -> None:
        self.write({"listing_detail": [key(RUN_A)]})

        with self.assertRaisesRegex(LandingContractError, "listing_detail.*listing_text"):
            self.register()

    def test_listing_text_may_be_read_over_other_runs(self) -> None:
        self.write(
            {
                "listing_detail": [key(RUN_A), key(RUN_B, "syn-2")],
                "listing_text": [key(RUN_A), key(RUN_B, "syn-2")],
            }
        )

        self.register(
            {
                "walk": {RUN_B},
                "observation": {RUN_B},
                "listing_detail": {RUN_B},
                "listing_text": {RUN_A},
            }
        )

        self.assertEqual(
            ["syn-1"],
            [
                r[0]
                for r in self.connection.sql(
                    "SELECT source_listing_id FROM landing_listing_text"
                ).fetchall()
            ],
        )

    def test_a_missing_file_is_refused_naming_table_and_run(self) -> None:
        self.write({"observation": [card(RUN_A)]})
        next(self.root.glob(f"walk/*/run={RUN_A}/part-0.parquet")).unlink()

        with self.assertRaisesRegex(LandingContractError, f"walk.*{RUN_A}"):
            self.register()

    def test_no_message_contains_a_row_value(self) -> None:
        self.write(
            {"observation": [card(RUN_A, "syn-secret-9"), card(RUN_A, "syn-secret-9", price_eur=1)]}
        )

        with self.assertRaises(LandingContractError) as raised:
            self.register()

        self.assertNotIn("syn-secret-9", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
