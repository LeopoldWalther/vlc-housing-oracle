"""The text labeller on the Claude API, tested against a fake client: no request leaves the test."""

import json
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from src.enrichment.batches import BatchAnswer, BatchState
from src.enrichment.claude_api import (
    MAX_OUTPUT_TOKENS,
    ClaudeApiBatches,
    ClaudeApiTextLabeler,
    label_result,
    message_request,
)
from src.enrichment.labeler import LabelResult, TextLabel
from src.enrichment.prompt import ANSWER_SCHEMA, SYSTEM_PROMPT

VALID = {"renovation_state": "renovated", "is_auction": False}


def thinking() -> SimpleNamespace:
    return SimpleNamespace(type="thinking", thinking="", signature="sig")


def text(content: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=content)


def message(*blocks: SimpleNamespace, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=list(blocks),
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=1200, output_tokens=340),
    )


class FakeMessages:
    def __init__(self, response: SimpleNamespace) -> None:
        self.response = response
        self.calls: list[dict] = []

    def create(self, **keywords) -> SimpleNamespace:
        self.calls.append(keywords)
        return self.response


class MessageRequestTest(unittest.TestCase):
    def test_the_request_carries_the_prompt_the_description_and_the_answer_schema(self) -> None:
        request = message_request("model-x", "Piso reformado.")

        self.assertEqual(request["model"], "model-x")
        self.assertEqual(request["max_tokens"], MAX_OUTPUT_TOKENS)
        self.assertEqual(MAX_OUTPUT_TOKENS, 4096)
        self.assertEqual(request["system"], SYSTEM_PROMPT)
        self.assertEqual(request["messages"], [{"role": "user", "content": "Piso reformado."}])
        self.assertEqual(
            request["output_config"],
            {"format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
        )

    def test_the_request_sets_no_sampling_thinking_or_tool(self) -> None:
        request = message_request("model-x", "Piso.")

        for key in ("temperature", "top_p", "top_k", "thinking", "tools", "tool_choice"):
            with self.subTest(key=key):
                self.assertNotIn(key, request)
        self.assertEqual(set(request["output_config"]), {"format"})


class LabelResultTest(unittest.TestCase):
    def test_a_valid_answer_after_thinking_is_a_label_with_tokens(self) -> None:
        result = label_result(message(thinking(), text(json.dumps(VALID))))

        self.assertEqual(result, LabelResult(TextLabel("renovated", False), 1200, 340))

    def test_a_response_without_a_valid_answer_has_no_label_but_its_tokens(self) -> None:
        responses = {
            "refusal": message(text(json.dumps(VALID)), stop_reason="refusal"),
            "output limit": message(thinking(), stop_reason="max_tokens"),
            "no text block": message(thinking()),
            "two text blocks": message(text(json.dumps(VALID)), text(json.dumps(VALID))),
            "not json": message(text("renovated")),
            "outside the schema": message(text(json.dumps({"renovation_state": "ruined"}))),
        }
        for case, response in responses.items():
            with self.subTest(case=case):
                self.assertEqual(label_result(response), LabelResult(None, 1200, 340))


class ClaudeApiTextLabelerTest(unittest.TestCase):
    def test_the_labeller_sends_exactly_the_built_request(self) -> None:
        messages = FakeMessages(message(text(json.dumps(VALID))))
        labeler = ClaudeApiTextLabeler(SimpleNamespace(messages=messages), "model-x")

        result = labeler.label("Piso reformado.")

        self.assertEqual(messages.calls, [message_request("model-x", "Piso reformado.")])
        self.assertEqual(result.label, TextLabel("renovated", False))


class FakeBatches:
    def __init__(self, batch: SimpleNamespace | None = None, entries=()) -> None:
        self.batch = batch
        self.entries = list(entries)
        self.created: list[list[dict]] = []
        self.asked: list[tuple[str, str]] = []

    def create(self, *, requests: list[dict]) -> SimpleNamespace:
        self.created.append(requests)
        return SimpleNamespace(id="batch-1")

    def retrieve(self, batch_id: str) -> SimpleNamespace:
        self.asked.append(("retrieve", batch_id))
        return self.batch

    def results(self, batch_id: str):
        self.asked.append(("results", batch_id))
        return iter(self.entries)


def batches_client(fake: FakeBatches) -> SimpleNamespace:
    return SimpleNamespace(messages=SimpleNamespace(batches=fake))


def entry(custom_id: str, kind: str, response: SimpleNamespace | None = None) -> SimpleNamespace:
    return SimpleNamespace(custom_id=custom_id, result=SimpleNamespace(type=kind, message=response))


class ClaudeApiBatchesTest(unittest.TestCase):
    def test_submit_sends_one_request_per_text_keyed_by_its_hash(self) -> None:
        fake = FakeBatches()
        batches = ClaudeApiBatches(batches_client(fake), "model-x")

        batch_id = batches.submit([("a" * 64, "Piso uno."), ("b" * 64, "Piso dos.")])

        self.assertEqual(batch_id, "batch-1")
        self.assertEqual(
            fake.created,
            [
                [
                    {"custom_id": "a" * 64, "params": message_request("model-x", "Piso uno.")},
                    {"custom_id": "b" * 64, "params": message_request("model-x", "Piso dos.")},
                ]
            ],
        )

    def test_submit_of_no_text_is_refused_without_a_call(self) -> None:
        fake = FakeBatches()

        with self.assertRaises(ValueError):
            ClaudeApiBatches(batches_client(fake), "model-x").submit([])
        self.assertEqual(fake.created, [])

    def test_the_state_of_a_batch(self) -> None:
        ended = datetime(2026, 11, 21, 3, 4, 5, tzinfo=UTC)
        cases = {
            "in progress": (
                SimpleNamespace(processing_status="in_progress", ended_at=None, results_url=None),
                BatchState(ended=False, ended_at=None, readable=False),
            ),
            "ended": (
                SimpleNamespace(processing_status="ended", ended_at=ended, results_url="https://r"),
                BatchState(ended=True, ended_at=ended, readable=True),
            ),
            "ended, results gone": (
                SimpleNamespace(processing_status="ended", ended_at=ended, results_url=None),
                BatchState(ended=True, ended_at=ended, readable=False),
            ),
        }
        for case, (batch, expected) in cases.items():
            with self.subTest(case=case):
                fake = FakeBatches(batch)
                state = ClaudeApiBatches(batches_client(fake), "model-x").state("batch-7")

                self.assertEqual(state, expected)
                self.assertEqual(fake.asked, [("retrieve", "batch-7")])

    def test_the_answers_of_a_batch_by_hash(self) -> None:
        entries = [
            entry("c" * 64, "errored"),
            entry("a" * 64, "succeeded", message(thinking(), text(json.dumps(VALID)))),
            entry("b" * 64, "succeeded", message(text("{}"), stop_reason="refusal")),
            entry("d" * 64, "expired"),
            entry("e" * 64, "canceled"),
            entry("f" * 64, "succeeded", message(text(json.dumps({"is_auction": True})))),
        ]
        fake = FakeBatches(entries=entries)

        answers = list(ClaudeApiBatches(batches_client(fake), "model-x").answers("batch-7"))

        self.assertEqual(
            answers,
            [
                BatchAnswer("c" * 64, "errored", None),
                BatchAnswer(
                    "a" * 64, "succeeded", LabelResult(TextLabel("renovated", False), 1200, 340)
                ),
                BatchAnswer("b" * 64, "succeeded", LabelResult(None, 1200, 340)),
                BatchAnswer("d" * 64, "expired", None),
                BatchAnswer("e" * 64, "canceled", None),
                BatchAnswer("f" * 64, "succeeded", LabelResult(None, 1200, 340)),
            ],
        )
        self.assertEqual(fake.asked, [("results", "batch-7")])


if __name__ == "__main__":
    unittest.main()
