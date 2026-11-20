"""The text labeller on the Claude API, tested against a fake client: no request leaves the test."""

import json
import unittest
from types import SimpleNamespace

from src.enrichment.claude_api import (
    MAX_OUTPUT_TOKENS,
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


if __name__ == "__main__":
    unittest.main()
