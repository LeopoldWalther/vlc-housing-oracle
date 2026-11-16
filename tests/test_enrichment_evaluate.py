"""The evaluation command: a labeller against labelled texts, raw and weighted by stratum."""

import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

from src.enrichment.evaluate import main
from src.enrichment.labeler import LabelResult, TextLabel

COLUMNS = (
    "number",
    "stratum",
    "stratum_population",
    "description",
    "renovation_state",
    "is_auction",
)


class FakeLabeler:
    def __init__(self, answers: dict[str, TextLabel | None]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def label(self, description: str) -> LabelResult:
        self.asked.append(description)
        return LabelResult(self.answers[description], 100, 10)


def ticking_clock():
    ticks = iter(range(0, 1000, 2))
    return lambda: float(next(ticks))


class EvaluateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def write_set(self, rows: list[tuple], columns: tuple[str, ...] = COLUMNS) -> Path:
        path = self.directory / "evaluation.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows(rows)
        return path

    def run_main(self, path: Path, labeler: FakeLabeler, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(
                ["--evaluation-set", str(path), "--model-id", "fake", *extra],
                labeler=labeler,
                clock=ticking_clock(),
            )
        return code, out.getvalue(), err.getvalue()

    def lines(self, output: str) -> dict[str, str]:
        return dict(line.split(": ", 1) for line in output.strip().splitlines())

    def test_a_labeller_that_agrees_everywhere_scores_one(self) -> None:
        rows = [
            (1, "A", 10, "texto uno", "renovated", "false"),
            (2, "A", 10, "texto dos", "needs_renovation", "true"),
            (3, "B", 5, "texto tres", "original", "false"),
        ]
        labeler = FakeLabeler(
            {
                "texto uno": TextLabel("renovated", False),
                "texto dos": TextLabel("needs_renovation", True),
                "texto tres": TextLabel("original", False),
            }
        )

        code, output, _ = self.run_main(self.write_set(rows), labeler)

        self.assertEqual(code, 0)
        report = self.lines(output)
        self.assertEqual(report["texts"], "3")
        self.assertEqual(report["invalid answers"], "0")
        self.assertEqual(report["input tokens"], "300")
        self.assertEqual(report["output tokens"], "30")
        self.assertEqual(report["seconds per text"], "0.67")
        self.assertEqual(report["renovation_state agreement"], "1.00")
        for value in ("renovated", "needs_renovation", "is_auction"):
            with self.subTest(value=value):
                self.assertEqual(
                    report[value],
                    "labelled 1, model 1, both 1, precision 1.00, recall 1.00, "
                    "weighted precision 1.00, weighted recall 1.00",
                )
        self.assertEqual(labeler.asked, ["texto uno", "texto dos", "texto tres"])

    def test_a_false_positive_in_a_heavy_stratum_lowers_weighted_precision(self) -> None:
        # Stratum A: population 2 over 2 texts, weight 1 each. Stratum B: population 100 over 1
        # text, weight 100. Weighted precision = 2 / (2 + 100).
        rows = [
            (1, "A", 2, "uno", "renovated", "false"),
            (2, "A", 2, "dos", "renovated", "false"),
            (3, "B", 100, "tres", "original", "false"),
        ]
        labeler = FakeLabeler(
            {text: TextLabel("renovated", False) for text in ("uno", "dos", "tres")}
        )

        _, output, _ = self.run_main(self.write_set(rows), labeler)

        self.assertEqual(
            self.lines(output)["renovated"],
            "labelled 2, model 3, both 2, precision 0.67, recall 1.00, "
            "weighted precision 0.02, weighted recall 1.00",
        )
        self.assertEqual(self.lines(output)["renovation_state agreement"], "0.67")

    def test_a_measure_without_a_denominator_is_not_available(self) -> None:
        rows = [(1, "A", 1, "uno", "renovated", "false")]
        labeler = FakeLabeler({"uno": TextLabel("original", True)})

        _, output, _ = self.run_main(self.write_set(rows), labeler)

        report = self.lines(output)
        self.assertEqual(
            report["renovated"],
            "labelled 1, model 0, both 0, precision n/a, recall 0.00, "
            "weighted precision n/a, weighted recall 0.00",
        )
        self.assertEqual(
            report["is_auction"],
            "labelled 0, model 1, both 0, precision 0.00, recall n/a, "
            "weighted precision 0.00, weighted recall n/a",
        )

    def test_an_invalid_answer_is_counted_and_lowers_recall(self) -> None:
        rows = [
            (1, "A", 2, "uno", "needs_renovation", "true"),
            (2, "A", 2, "dos", "needs_renovation", "true"),
        ]
        labeler = FakeLabeler({"uno": TextLabel("needs_renovation", True), "dos": None})

        _, output, _ = self.run_main(self.write_set(rows), labeler)

        report = self.lines(output)
        self.assertEqual(report["invalid answers"], "1")
        self.assertEqual(report["renovation_state agreement"], "0.50")
        self.assertTrue(report["needs_renovation"].startswith("labelled 2, model 1, both 1,"))
        self.assertIn("recall 0.50", report["is_auction"])

    def test_no_description_is_printed_and_the_results_hold_labels_only(self) -> None:
        rows = [(1, "A", 1, "una descripción inventada", "unknown", "false")]
        labeler = FakeLabeler({"una descripción inventada": None})
        results = self.directory / "results.csv"

        _, output, errors = self.run_main(self.write_set(rows), labeler, "--results", str(results))

        self.assertNotIn("descripción", output + errors)
        with results.open(encoding="utf-8") as handle:
            written = list(csv.reader(handle))
        self.assertEqual(
            written,
            [
                [
                    "number",
                    "renovation_state",
                    "is_auction",
                    "model_renovation_state",
                    "model_is_auction",
                ],
                ["1", "unknown", "false", "", ""],
            ],
        )

    def test_a_missing_column_is_refused_naming_the_row_and_column(self) -> None:
        path = self.write_set([(1, "A", 1, "uno", "original")], COLUMNS[:-1])

        code, output, errors = self.run_main(path, FakeLabeler({}))

        self.assertEqual(code, 2)
        self.assertIn("is_auction", errors)
        self.assertNotIn("uno", output + errors)

    def test_an_unknown_label_is_refused_naming_the_row_and_column(self) -> None:
        for row, column in (
            ((1, "A", 1, "uno secreto", "ruined", "false"), "renovation_state"),
            ((1, "A", 1, "uno secreto", "original", "maybe"), "is_auction"),
        ):
            with self.subTest(column=column):
                labeler = FakeLabeler({})
                code, output, errors = self.run_main(self.write_set([row]), labeler)

                self.assertEqual(code, 2)
                self.assertIn("row 1", errors)
                self.assertIn(column, errors)
                self.assertNotIn("secreto", output + errors)
                self.assertEqual(labeler.asked, [])


if __name__ == "__main__":
    unittest.main()
