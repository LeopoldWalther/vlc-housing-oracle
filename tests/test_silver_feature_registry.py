"""The registry of feature wordings: its normalisation, its digests and its targets."""

import hashlib
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog

from src.contracts import landing_v3_columns
from src.silver.feature_patterns import (
    FURNISHING_VALUES,
    KNOWN_PATTERN_DIGESTS,
    KNOWN_PATTERNS,
    LABEL_TARGETS,
    UNREAD,
    register_feature_registry,
    registry_rows,
)
from src.silver.rebuild import listing_quality, local_commits, process_landing
from src.synthetic.landing import generate_landing_v3
from tests.test_silver_listing_quality import listing


def wording(connection: duckdb.DuckDBPyConnection, feature: str) -> str:
    return connection.execute("SELECT feature_wording(?)", [feature]).fetchone()[0]


class FeatureWordingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = duckdb.connect()
        register_feature_registry(self.connection)

    def test_features_that_differ_only_in_a_number_share_a_wording(self) -> None:
        self.assertEqual(
            wording(self.connection, "80 m² construidos, 72 m² útiles"),
            wording(self.connection, "125 m² construidos, 110 m² útiles"),
        )
        self.assertEqual(
            wording(self.connection, "80 m² construidos, 72 m² útiles"),
            "# m² construidos, # m² útiles",
        )

    def test_a_decimal_number_is_one_number(self) -> None:
        self.assertEqual(
            wording(self.connection, "Consumo: 12,5 kWh/m² año"), "consumo: # kwh/m² año"
        )
        self.assertEqual(
            wording(self.connection, "Consumo: 1.250,75 kWh/m² año"), "consumo: # kwh/m² año"
        )

    def test_a_wording_is_trimmed_and_lower_cased(self) -> None:
        self.assertEqual(wording(self.connection, "  Con Ascensor "), "con ascensor")

    def test_every_registry_key_is_its_own_wording(self) -> None:
        keys = pa.table({"pattern": list(KNOWN_PATTERNS)})
        self.connection.register("registry_keys", keys)
        changed = self.connection.sql(
            "SELECT pattern FROM registry_keys WHERE feature_wording(pattern) <> pattern"
        ).fetchall()
        self.assertEqual(changed, [])


class RegistryRowsTest(unittest.TestCase):
    def test_the_registry_has_one_hundred_distinct_digests(self) -> None:
        rows = registry_rows().to_pylist()
        self.assertEqual((len(KNOWN_PATTERNS), len(KNOWN_PATTERN_DIGESTS)), (99, 1))
        self.assertEqual(len(rows), 100)
        self.assertEqual(len({row["pattern_sha256"] for row in rows}), 100)
        self.assertEqual(
            list(registry_rows().schema.names), ["pattern_sha256", "target", "label_value"]
        )

    def test_a_digest_is_the_sha256_of_the_wording(self) -> None:
        digests = {row["pattern_sha256"]: row["target"] for row in registry_rows().to_pylist()}
        for pattern, target in KNOWN_PATTERNS.items():
            self.assertEqual(digests[hashlib.sha256(pattern.encode("utf-8")).hexdigest()], target)
        for digest, target in KNOWN_PATTERN_DIGESTS.items():
            self.assertEqual(digests[digest], target)

    def test_every_target_is_a_detail_column_a_label_or_unread(self) -> None:
        detail_columns = {name for name, _ in landing_v3_columns()["listing_detail"]}
        allowed = detail_columns | set(LABEL_TARGETS) | {UNREAD}
        targets = {row["target"] for row in registry_rows().to_pylist()}
        self.assertEqual(targets - allowed, set())

    def test_exactly_the_furnishing_wordings_carry_a_label_value(self) -> None:
        self.assertEqual(
            {pattern for pattern, target in KNOWN_PATTERNS.items() if target == "furnishing"},
            set(FURNISHING_VALUES),
        )
        self.assertEqual(
            set(FURNISHING_VALUES.values()), {"furnished", "kitchen_only", "unfurnished"}
        )
        valued = {
            row["pattern_sha256"]: row["label_value"]
            for row in registry_rows().to_pylist()
            if row["label_value"] is not None
        }
        self.assertEqual(
            valued,
            {
                hashlib.sha256(pattern.encode("utf-8")).hexdigest(): value
                for pattern, value in FURNISHING_VALUES.items()
            },
        )

    def test_duckdb_hashes_a_non_ascii_wording_like_the_registry(self) -> None:
        connection = duckdb.connect()
        register_feature_registry(connection)
        pattern = "orientación sur"
        self.assertIn(pattern, KNOWN_PATTERNS)
        found = connection.execute(
            "SELECT target FROM feature_registry WHERE pattern_sha256 = sha256(?)", [pattern]
        ).fetchall()
        self.assertEqual(found, [(KNOWN_PATTERNS[pattern],)])


class RegistryInTheSessionTest(unittest.TestCase):
    def assert_registered(self, connection: duckdb.DuckDBPyConnection) -> None:
        self.assertEqual(connection.sql("SELECT count(*) FROM feature_registry").fetchone()[0], 100)

    def test_a_run_over_the_synthetic_landing_registers_the_registry(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        landing = root / "landing" / "v3"
        landing.mkdir(parents=True)
        generate_landing_v3(landing, seed=1)
        catalog = SqlCatalog("test", uri=f"sqlite:///{root}/catalog.db", warehouse=f"file://{root}")
        connection = self.enterContext(duckdb.connect())

        process_landing(
            connection,
            catalog,
            landing=str(landing),
            mode="backfill",
            list_commits=lambda: local_commits(landing),
            now=lambda: datetime(2026, 10, 8, 6, 0, tzinfo=UTC),
        )

        self.assert_registered(connection)

    def test_a_rule_function_registers_the_registry(self) -> None:
        connection = self.enterContext(duckdb.connect())
        listing_quality(connection, listing())
        self.assert_registered(connection)


if __name__ == "__main__":
    unittest.main()
