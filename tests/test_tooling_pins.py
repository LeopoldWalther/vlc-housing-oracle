"""The linter CI runs must be the linter the project pins.

Ruff's default rule set grows between releases, so two different versions enforce two different
contracts. If the workflow and requirements-dev.txt drift apart, CI silently checks something other
than what was agreed - and the failure mode is a green pull request that a later run rejects.
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_DEV = REPO_ROOT / "requirements-dev.txt"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def pinned_ruff_version(text: str) -> str | None:
    """Return the exact ruff version pinned anywhere in ``text``, or None."""
    match = re.search(r"ruff==([0-9]+\.[0-9]+\.[0-9]+)", text)
    return match.group(1) if match else None


class TestToolingPins(unittest.TestCase):
    def test_requirements_dev_pins_ruff_exactly(self) -> None:
        self.assertTrue(REQUIREMENTS_DEV.is_file(), f"{REQUIREMENTS_DEV} is missing")

        self.assertIsNotNone(
            pinned_ruff_version(REQUIREMENTS_DEV.read_text(encoding="utf-8")),
            "requirements-dev.txt must pin ruff to an exact version, not a range",
        )

    def test_the_workflow_installs_the_pinned_ruff(self) -> None:
        self.assertTrue(WORKFLOW.is_file(), f"{WORKFLOW} is missing")

        workflow = WORKFLOW.read_text(encoding="utf-8")
        pinned = pinned_ruff_version(REQUIREMENTS_DEV.read_text(encoding="utf-8"))

        # The workflow may install from the file or name the version; either is fine, as long as a
        # version named in the workflow is the pinned one.
        named = pinned_ruff_version(workflow)
        if named is not None:
            self.assertEqual(pinned, named, "the workflow pins a different ruff version")
        else:
            self.assertIn(
                "requirements-dev.txt", workflow, "the workflow must install requirements-dev.txt"
            )

    def test_the_workflow_runs_every_command_the_plans_require(self) -> None:
        self.assertTrue(WORKFLOW.is_file(), f"{WORKFLOW} is missing")

        workflow = WORKFLOW.read_text(encoding="utf-8")
        for command in (
            "unittest discover -s tests -t .",
            "dev/tools/validate_workflow.py",
            "ruff check",
            "ruff format --check",
        ):
            self.assertIn(command, workflow, f"the workflow does not run: {command}")

    def test_every_job_runs_on_a_pinned_runner_image(self) -> None:
        # A moving image changes the toolchain under an unchanged commit: on 2026-10-19
        # ubuntu-latest became Ubuntu 26.04, which has no build of the pinned Python, and every
        # pull request turned red.
        self.assertTrue(WORKFLOW.is_file(), f"{WORKFLOW} is missing")

        runners = re.findall(
            r"^\s*runs-on:\s*(\S+)\s*$", WORKFLOW.read_text(encoding="utf-8"), re.M
        )

        self.assertTrue(runners, "the workflow declares no runs-on")
        for runner in runners:
            with self.subTest(runner=runner):
                self.assertNotIn("latest", runner, "pin the runner image, e.g. ubuntu-24.04")


if __name__ == "__main__":
    unittest.main()
