"""The Bedrock labeller: its Converse request, its answer and its retries, against a stub."""

import unittest

import boto3
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from src.enrichment.bedrock import MAX_OUTPUT_TOKENS, BedrockTextLabeler
from src.enrichment.labeler import LabelResult, TextLabel
from src.enrichment.prompt import ANSWER_SCHEMA, ANSWER_TOOL_NAME, SYSTEM_PROMPT

MODEL_ID = "global.example.model-v1"
DESCRIPTION = "Piso para reformar en una calle inventada."


def client():
    return boto3.client(
        "bedrock-runtime",
        region_name="eu-west-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )


def expected_request() -> dict:
    return {
        "modelId": MODEL_ID,
        "system": [{"text": SYSTEM_PROMPT}],
        "messages": [{"role": "user", "content": [{"text": DESCRIPTION}]}],
        "toolConfig": {
            "tools": [
                {
                    "toolSpec": {
                        "name": ANSWER_TOOL_NAME,
                        "description": "Record the two labels of the description.",
                        "inputSchema": {"json": ANSWER_SCHEMA},
                    }
                }
            ],
            "toolChoice": {"tool": {"name": ANSWER_TOOL_NAME}},
        },
        "inferenceConfig": {"maxTokens": MAX_OUTPUT_TOKENS},
    }


def response(content: list[dict], input_tokens: int = 900, output_tokens: int = 40) -> dict:
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": "tool_use",
        "usage": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "totalTokens": input_tokens + output_tokens,
        },
        "metrics": {"latencyMs": 300},
    }


def tool_use(answer: object) -> list[dict]:
    return [{"toolUse": {"toolUseId": "t1", "name": ANSWER_TOOL_NAME, "input": answer}}]


class BedrockTextLabelerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = client()
        self.stub = self.enterContext(Stubber(self.client))
        self.sleeps: list[float] = []
        self.labeler = BedrockTextLabeler(self.client, MODEL_ID, sleep=self.sleeps.append)

    def throttle(self) -> None:
        self.stub.add_client_error(
            "converse",
            service_error_code="ThrottlingException",
            http_status_code=429,
            expected_params=expected_request(),
        )

    def test_a_valid_answer_is_a_label_with_its_tokens(self) -> None:
        self.assertEqual(MAX_OUTPUT_TOKENS, 200)
        self.stub.add_response(
            "converse",
            response(tool_use({"renovation_state": "needs_renovation", "is_auction": False})),
            expected_request(),
        )

        result = self.labeler.label(DESCRIPTION)

        self.assertEqual(result, LabelResult(TextLabel("needs_renovation", False), 900, 40))
        self.stub.assert_no_pending_responses()

    def test_an_answer_outside_the_schema_is_no_label(self) -> None:
        self.stub.add_response(
            "converse",
            response(tool_use({"renovation_state": "ruined", "is_auction": False}), 800, 30),
            expected_request(),
        )

        self.assertEqual(self.labeler.label(DESCRIPTION), LabelResult(None, 800, 30))

    def test_a_response_without_a_tool_use_is_no_label(self) -> None:
        self.stub.add_response(
            "converse", response([{"text": "original"}], 800, 5), expected_request()
        )

        self.assertEqual(self.labeler.label(DESCRIPTION), LabelResult(None, 800, 5))

    def test_throttling_is_retried_after_one_and_two_seconds(self) -> None:
        self.throttle()
        self.throttle()
        self.stub.add_response(
            "converse",
            response(tool_use({"renovation_state": "unknown", "is_auction": True})),
            expected_request(),
        )

        result = self.labeler.label(DESCRIPTION)

        self.assertEqual(result.label, TextLabel("unknown", True))
        self.assertEqual(self.sleeps, [1, 2])

    def test_a_fourth_throttling_is_raised(self) -> None:
        for _ in range(4):
            self.throttle()

        with self.assertRaises(ClientError) as raised:
            self.labeler.label(DESCRIPTION)

        self.assertEqual(raised.exception.response["Error"]["Code"], "ThrottlingException")
        self.assertEqual(self.sleeps, [1, 2, 4])

    def test_another_error_is_raised_at_once(self) -> None:
        self.stub.add_client_error(
            "converse",
            service_error_code="AccessDeniedException",
            http_status_code=403,
            expected_params=expected_request(),
        )

        with self.assertRaises(ClientError):
            self.labeler.label(DESCRIPTION)

        self.assertEqual(self.sleeps, [])


if __name__ == "__main__":
    unittest.main()
