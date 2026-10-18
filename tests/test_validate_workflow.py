"""Tests for workflow status aggregation."""

import unittest

from dev.tools.validate_workflow import _expected_marker, _status_matches


class TestExpectedMarker(unittest.TestCase):
    """Verify task states map to the documented feature status."""

    def test_empty_or_not_started_tasks_are_planned(self) -> None:
        self.assertEqual(_expected_marker([]), "🔵")
        self.assertEqual(_expected_marker(["not_started", "not_started"]), "🔵")

    def test_started_or_partially_done_tasks_are_in_progress(self) -> None:
        self.assertEqual(_expected_marker(["in_progress", "not_started"]), "🟡")
        self.assertEqual(_expected_marker(["done", "not_started"]), "🟡")

    def test_all_done_tasks_are_complete(self) -> None:
        self.assertEqual(_expected_marker(["done", "done"]), "🟢")


class TestSupersededFeatures(unittest.TestCase):
    """A superseded feature keeps its plan and technical plan as a record, with tasks never started.

    Its status is declared, not derived: whatever its tasks say, ⚪ is consistent, and a derived
    status other than the expected one still is not.
    """

    def test_a_superseded_feature_is_consistent_whatever_its_tasks_say(self) -> None:
        self.assertTrue(_status_matches("⚪", ["not_started", "not_started"]))
        self.assertTrue(_status_matches("⚪", ["done", "not_started"]))

    def test_a_derived_status_must_still_match_the_tasks(self) -> None:
        self.assertTrue(_status_matches("🔵", ["not_started"]))
        self.assertFalse(_status_matches("🟢", ["not_started"]))


if __name__ == "__main__":
    unittest.main()
