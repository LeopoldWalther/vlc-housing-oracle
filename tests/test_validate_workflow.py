"""Tests for workflow status aggregation."""

import unittest

from dev.tools.validate_workflow import _expected_marker


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


if __name__ == "__main__":
    unittest.main()
