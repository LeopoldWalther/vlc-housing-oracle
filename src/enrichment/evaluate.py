"""Evaluate a text labeller against a labelled evaluation set, raw and weighted by stratum.

    python -m src.enrichment.evaluate --evaluation-set <csv> --model-id <id>
        --provider {bedrock,claude-api} [--results <csv>]

The evaluation set is private: it is read by path, and nothing but counts, measures and, with
``--results``, row numbers and labels leave this command.
"""

import argparse
import csv
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from src.enrichment.labeler import RENOVATION_STATES, TextLabel, TextLabeler

COLUMNS = (
    "number",
    "stratum",
    "stratum_population",
    "description",
    "renovation_state",
    "is_auction",
)

# The values whose precision and recall decide whether the model is good enough.
DECISIVE_VALUES = ("renovated", "needs_renovation", "is_auction")

BOOLEANS = {"true": True, "false": False}

PROVIDERS = ("bedrock", "claude-api")


class EvaluationSetError(ValueError):
    """The evaluation set breaks its format; the message names a row and a column, never a value."""


@dataclass(frozen=True)
class LabelledText:
    number: str
    weight: float
    description: str
    expected: TextLabel


def read_evaluation_set(path: Path) -> list[LabelledText]:
    """The labelled texts of the file, each weighted by its stratum's population per sampled text."""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in COLUMNS if column not in (reader.fieldnames or ())]
        if missing:
            raise EvaluationSetError(f"header: missing column {', '.join(missing)}")
        rows = list(reader)
    for position, row in enumerate(rows, start=1):
        if row["renovation_state"] not in RENOVATION_STATES:
            raise EvaluationSetError(f"row {position}: unknown value in column renovation_state")
        if row["is_auction"] not in BOOLEANS:
            raise EvaluationSetError(f"row {position}: column is_auction is not true or false")
        if not row["stratum_population"].isdigit():
            raise EvaluationSetError(f"row {position}: column stratum_population is not a count")
    sampled = Counter(row["stratum"] for row in rows)
    return [
        LabelledText(
            number=row["number"],
            weight=int(row["stratum_population"]) / sampled[row["stratum"]],
            description=row["description"],
            expected=TextLabel(row["renovation_state"], BOOLEANS[row["is_auction"]]),
        )
        for row in rows
    ]


def has_value(label: TextLabel | None, value: str) -> bool:
    """Whether a label carries a decisive value; no label carries none."""
    if label is None:
        return False
    if value == "is_auction":
        return label.is_auction
    return label.renovation_state == value


def measures(texts: Sequence[LabelledText], answers: Sequence[TextLabel | None], value: str) -> str:
    """The report line of one decisive value."""
    expected = [has_value(text.expected, value) for text in texts]
    model = [has_value(answer, value) for answer in answers]
    weights = [text.weight for text in texts]
    both = [e and m for e, m in zip(expected, model, strict=True)]

    def ratio(numerator: Sequence[bool], denominator: Sequence[bool], weighted: bool) -> str:
        def total(flags: Sequence[bool]) -> float:
            return sum(w if weighted else 1 for w, f in zip(weights, flags, strict=True) if f)

        below = total(denominator)
        return "n/a" if below == 0 else f"{total(numerator) / below:.2f}"

    return (
        f"labelled {sum(expected)}, model {sum(model)}, both {sum(both)}, "
        f"precision {ratio(both, model, False)}, recall {ratio(both, expected, False)}, "
        f"weighted precision {ratio(both, model, True)}, "
        f"weighted recall {ratio(both, expected, True)}"
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    labeler: TextLabeler | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Label every text once and print the report; exit 2 when the evaluation set is refused."""
    parser = argparse.ArgumentParser(
        prog="python -m src.enrichment.evaluate",
        description="Evaluate a text labeller against a labelled evaluation set.",
    )
    parser.add_argument("--evaluation-set", required=True, type=Path)
    parser.add_argument(
        "--model-id",
        required=True,
        help="a Claude API model id, or a Bedrock model or inference profile id",
    )
    parser.add_argument("--provider", required=True, choices=PROVIDERS)
    parser.add_argument("--results", type=Path, help="write row numbers and labels to this CSV")
    arguments = parser.parse_args(argv)
    try:
        texts = read_evaluation_set(arguments.evaluation_set)
    except EvaluationSetError as error:
        print(f"evaluation set refused: {error}", file=sys.stderr)
        return 2
    if labeler is None:
        labeler = _labeler(arguments.provider, arguments.model_id)

    started = clock()
    results = [labeler.label(text.description) for text in texts]
    seconds = clock() - started
    answers = [result.label for result in results]

    agreement = sum(
        answer is not None and answer.renovation_state == text.expected.renovation_state
        for text, answer in zip(texts, answers, strict=True)
    )
    print(f"texts: {len(texts)}")
    print(f"invalid answers: {sum(answer is None for answer in answers)}")
    print(f"input tokens: {sum(result.input_tokens for result in results)}")
    print(f"output tokens: {sum(result.output_tokens for result in results)}")
    print(f"seconds per text: {seconds / len(texts) if texts else 0:.2f}")
    print(f"renovation_state agreement: {agreement / len(texts) if texts else 0:.2f}")
    for value in DECISIVE_VALUES:
        print(f"{value}: {measures(texts, answers, value)}")
    if arguments.results:
        _write_results(arguments.results, texts, answers)
    return 0


def _labeler(provider: str, model_id: str) -> TextLabeler:
    """The labeller of a provider; the Claude API client reads its key from the environment."""
    if provider == "bedrock":
        import boto3

        from src.enrichment.bedrock import BedrockTextLabeler

        return BedrockTextLabeler(boto3.client("bedrock-runtime"), model_id)
    import anthropic

    from src.enrichment.claude_api import ClaudeApiTextLabeler

    return ClaudeApiTextLabeler(anthropic.Anthropic(max_retries=5), model_id)


def _write_results(
    path: Path, texts: Sequence[LabelledText], answers: Sequence[TextLabel | None]
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "number",
                "renovation_state",
                "is_auction",
                "model_renovation_state",
                "model_is_auction",
            ]
        )
        for text, answer in zip(texts, answers, strict=True):
            writer.writerow(
                [
                    text.number,
                    text.expected.renovation_state,
                    str(text.expected.is_auction).lower(),
                    "" if answer is None else answer.renovation_state,
                    "" if answer is None else str(answer.is_auction).lower(),
                ]
            )


if __name__ == "__main__":
    sys.exit(main())
