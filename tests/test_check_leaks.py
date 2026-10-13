"""The leak guard: no tracked file may contain a term whose digest is on the forbidden list."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from dev.tools.check_leaks import DIGESTS_FILE, REPO_ROOT, find_leaks, load_digests, main

# Invented for the test and never on the real list, so the test itself is no leak.
TERM = "examplecorp"
DIGEST = hashlib.sha256(TERM.encode()).hexdigest()


class TestFindLeaks(unittest.TestCase):
    def test_a_forbidden_word_is_found_whatever_its_case(self) -> None:
        self.assertEqual(find_leaks("Data from ExampleCorp only.", {DIGEST}), [1])

    def test_a_forbidden_word_inside_a_domain_or_path_is_found(self) -> None:
        text = "first line\nsee https://www.examplecorp.com/listing/1\n"

        self.assertEqual(find_leaks(text, {DIGEST}), [2])

    def test_a_hyphenated_identifier_is_matched_as_a_whole(self) -> None:
        bucket = "prod-examplecorp-lake"
        digest = hashlib.sha256(bucket.encode()).hexdigest()

        self.assertEqual(find_leaks(f"bucket = {bucket}", {digest}), [1])

    def test_clean_text_has_no_leak(self) -> None:
        self.assertEqual(find_leaks("Valencia listings, synthetic only.", {DIGEST}), [])

    def test_a_word_merely_containing_the_term_is_not_a_leak(self) -> None:
        self.assertEqual(find_leaks("notexamplecorpish", {DIGEST}), [])


class TestDigestFile(unittest.TestCase):
    def test_comments_and_blank_lines_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "digests.txt"
            path.write_text(f"# a comment\n\n{DIGEST}\n", encoding="utf-8")

            self.assertEqual(load_digests(path), {DIGEST})

    def test_a_malformed_line_is_rejected_rather_than_silently_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "digests.txt"
            path.write_text("not-a-digest\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                load_digests(path)


class TestMain(unittest.TestCase):
    def _tree(self, root: str, text: str) -> tuple[Path, Path]:
        repo = Path(root)
        (repo / "notes.md").write_text(text, encoding="utf-8")
        digests = repo / "digests.txt"
        digests.write_text(DIGEST + "\n", encoding="utf-8")
        return repo, digests

    def test_a_leak_fails_and_names_the_file_and_line_but_not_the_term(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo, digests = self._tree(root, "ok\nExampleCorp\n")
            report: list[str] = []

            status = main(repo, digests, files=["notes.md"], out=report.append)

        self.assertEqual(status, 1)
        self.assertIn("notes.md:2", "\n".join(report))
        self.assertNotIn(TERM, "\n".join(report).lower())

    def test_a_clean_tree_passes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo, digests = self._tree(root, "nothing to see\n")

            self.assertEqual(main(repo, digests, files=["notes.md"], out=lambda _: None), 0)


class TestThisRepository(unittest.TestCase):
    def test_every_tracked_file_is_clean(self) -> None:
        report: list[str] = []

        status = main(REPO_ROOT, DIGESTS_FILE, out=report.append)

        self.assertEqual(status, 0, "\n".join(report))

    def test_the_real_list_is_not_empty(self) -> None:
        self.assertTrue(load_digests(DIGESTS_FILE))
