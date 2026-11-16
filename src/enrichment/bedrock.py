"""The text labeller on Amazon Bedrock: one Converse request per description, with a forced tool."""

import time
from collections.abc import Callable

from botocore.exceptions import ClientError

from src.enrichment.labeler import LabelResult, label_from_answer
from src.enrichment.prompt import ANSWER_SCHEMA, ANSWER_TOOL_NAME, SYSTEM_PROMPT

# The answer is two short fields; the bound keeps the request from reserving the model's maximum.
MAX_OUTPUT_TOKENS = 200

# Seconds to wait before the second, third and fourth attempt of a throttled request.
THROTTLE_WAITS = (1, 2, 4)


class BedrockTextLabeler:
    """A ``TextLabeler`` that asks a Bedrock model through the Converse API.

    No sampling parameter is set: the model's defaults are part of ``LABEL_VERSION``.
    """

    def __init__(self, client, model_id: str, *, sleep: Callable[[float], None] = time.sleep):
        self._client = client
        self._model_id = model_id
        self._sleep = sleep

    def label(self, description: str) -> LabelResult:
        response = self._converse(converse_request(self._model_id, description))
        usage = response["usage"]
        answers = [
            block["toolUse"]["input"]
            for block in response["output"]["message"]["content"]
            if "toolUse" in block and block["toolUse"]["name"] == ANSWER_TOOL_NAME
        ]
        label = label_from_answer(answers[0]) if len(answers) == 1 else None
        return LabelResult(label, usage["inputTokens"], usage["outputTokens"])

    def _converse(self, request: dict) -> dict:
        for wait in (*THROTTLE_WAITS, None):
            try:
                return self._client.converse(**request)
            except ClientError as error:
                if wait is None or error.response["Error"]["Code"] != "ThrottlingException":
                    raise
                self._sleep(wait)
        raise AssertionError("unreachable")


def converse_request(model_id: str, description: str) -> dict:
    """The Converse request that labels one description."""
    return {
        "modelId": model_id,
        "system": [{"text": SYSTEM_PROMPT}],
        "messages": [{"role": "user", "content": [{"text": description}]}],
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
