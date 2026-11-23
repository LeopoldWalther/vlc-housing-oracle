"""Landing v3 is read per run, and every named file is checked before Silver reads a row of it."""

import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from src.silver.landing import LandingContractError, read_defect, register_landing_v3
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


def http_error(status_code: int | None) -> duckdb.HTTPException:
    """An HTTPException as DuckDB raises it; one built by hand carries no status code."""
    error = duckdb.HTTPException("HTTP error on 'secret-object'")
    if status_code is not None:
        error.status_code = status_code
    return error


class FailingConnection:
    """A connection whose every query raises the given error, as a failed read would."""

    def __init__(self, error: duckdb.Error) -> None:
        self.error = error

    def sql(self, query: str) -> None:
        raise self.error


def card(run_id: str, listing_id: str = "syn-1", **overrides: object) -> dict[str, object]:
    return key(run_id, listing_id) | {"is_empty_card": False} | overrides


class TestRegisterLandingV3(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.connection = duckdb.connect()
        self.addCleanup(self.connection.close)

    def write(self, rows_by_table: dict) -> None:
        write_landing_v3(self.root, rows_by_table)

    def register(
        self,
        runs_by_table: dict | None = None,
        counts: dict | None = None,
        dates: dict | None = None,
    ) -> None:
        written = {path.stem for path in (self.root / "_commits").glob("*.json")}
        runs = runs_by_table or dict.fromkeys(TABLES, written)
        register_landing_v3(
            self.connection, str(self.root), runs, STARTED, counts or {}, expected_dates=dates
        )

    def register_run_a(self, observation_days: set[date]) -> None:
        """Register run A as pending: one observation row, its commit naming the given days."""
        self.register(
            counts={RUN_A: {"walk": 0, "observation": 1, "listing_detail": 0, "listing_text": 0}},
            dates={RUN_A: dict.fromkeys(TABLES, set()) | {"observation": observation_days}},
        )

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

    def test_a_file_holding_a_day_its_commit_does_not_name_is_refused(self) -> None:
        self.write({"observation": [card(RUN_A)]})

        with self.assertRaisesRegex(
            LandingContractError,
            f"observation: run {RUN_A} holds rows of 2026-10-01 that its commit does not name",
        ):
            self.register_run_a(set())

    def test_a_commit_naming_a_day_its_file_does_not_hold_is_refused(self) -> None:
        self.write({"observation": [card(RUN_A)]})

        with self.assertRaisesRegex(
            LandingContractError,
            f"observation: run {RUN_A} commit names 2026-10-02 that its file does not hold",
        ):
            self.register_run_a({date(2026, 10, 1), date(2026, 10, 2)})

    def test_a_null_day_is_a_day_the_commit_does_not_name(self) -> None:
        self.write({"observation": [card(RUN_A, observed_date=None)]})

        with self.assertRaisesRegex(LandingContractError, f"run {RUN_A} holds rows of null"):
            self.register_run_a(set())

    def test_a_file_whose_days_equal_its_commit_is_registered(self) -> None:
        self.write({"observation": [card(RUN_A)]})

        self.register_run_a({date(2026, 10, 1)})

        self.assertEqual(1, self.count("landing_observation"))

    def test_a_pending_run_is_checked_also_for_a_table_it_is_not_read_for(self) -> None:
        self.write({"observation": [card(RUN_A)]})
        next(self.root.glob(f"walk/*/run={RUN_A}/part-0.parquet")).unlink()

        with self.assertRaisesRegex(LandingContractError, f"walk: run {RUN_A} has no file"):
            self.register(
                runs_by_table={"observation": {RUN_A}},
                counts={
                    RUN_A: {"walk": 0, "observation": 1, "listing_detail": 0, "listing_text": 0}
                },
            )

    def test_a_pending_run_not_read_for_a_table_stays_out_of_its_view(self) -> None:
        self.write({"observation": [card(RUN_A)], "listing_detail": [key(RUN_B)]})

        self.register(
            runs_by_table={"observation": {RUN_A}, "listing_detail": {RUN_B}},
            counts={RUN_A: {"walk": 0, "observation": 1, "listing_detail": 0, "listing_text": 0}},
        )

        self.assertEqual(1, self.count("landing_observation"))
        self.assertEqual(
            [(RUN_B,)], self.connection.sql("SELECT run_id FROM landing_listing_detail").fetchall()
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


class TestReadDefect(unittest.TestCase):
    def test_an_invalid_input_is_not_a_parquet_file(self) -> None:
        self.assertEqual(
            "is not a Parquet file", read_defect(duckdb.InvalidInputException("bad magic"))
        )

    def test_an_http_404_is_a_missing_file(self) -> None:
        self.assertEqual("has no file", read_defect(http_error(404)))

    def test_other_read_errors_are_no_defect_of_the_file(self) -> None:
        for error in (
            duckdb.IOException("boom"),
            http_error(None),
            http_error(403),
            duckdb.ConnectionException("boom"),
        ):
            with self.subTest(error=type(error).__name__):
                self.assertIsNone(read_defect(error))


class TestReadErrorsOfLandingFiles(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write_landing_v3(self.root, {"observation": [card(RUN_A)]})
        self.counts = {RUN_A: {"walk": 0, "observation": 1, "listing_detail": 0, "listing_text": 0}}

    def register(self, connection: object, location: str | None = None) -> None:
        register_landing_v3(
            connection,
            location or str(self.root),
            dict.fromkeys(TABLES, {RUN_A}),
            STARTED,
            self.counts,
        )

    def test_a_file_of_garbage_bytes_is_not_a_parquet_file(self) -> None:
        path = next(self.root.glob(f"observation/*/run={RUN_A}/part-0.parquet"))
        path.write_bytes(b"secret garbage, not parquet")
        connection = duckdb.connect()
        self.addCleanup(connection.close)

        with self.assertRaises(LandingContractError) as refused:
            self.register(connection)

        self.assertEqual(
            f"landing table observation: run {RUN_A} is not a Parquet file", str(refused.exception)
        )

    def test_a_failed_read_propagates_as_itself(self) -> None:
        with self.assertRaisesRegex(duckdb.IOException, "^boom$"):
            self.register(FailingConnection(duckdb.IOException("boom")))

    def test_a_missing_remote_object_is_a_missing_file(self) -> None:
        with self.assertRaises(LandingContractError) as refused:
            self.register(FailingConnection(http_error(404)), "s3://landing-bucket/v3")

        self.assertEqual(f"landing table walk: run {RUN_A} has no file", str(refused.exception))

    def test_another_http_error_propagates_as_itself(self) -> None:
        with self.assertRaises(duckdb.HTTPException):
            self.register(FailingConnection(http_error(None)), "s3://landing-bucket/v3")


if __name__ == "__main__":
    unittest.main()
