"""The text labeller on the Claude API: one request per description, answered as a structured output.

This is the only module that knows the ``anthropic`` SDK. The client reads its key from the
environment variable ``ANTHROPIC_API_KEY``; no key is ever an argument here.
"""

import json

from src.enrichment.labeler import LabelResult, label_from_answer
from src.enrichment.prompt import ANSWER_SCHEMA, SYSTEM_PROMPT

# The candidate models think before they answer, and the thinking counts toward this limit; a
# response cut off by it carries no answer and its text would be sent again by every run.
MAX_OUTPUT_TOKENS = 4096


def message_request(model_id: str, description: str) -> dict:
    """The request that labels one description, sent directly or inside a message batch.

    No sampling parameter, thinking setting or tool is set: the candidate models reject the first
    and a forced tool, and their defaults are part of ``LABEL_VERSION``.
    """
    return {
        "model": model_id,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": description}],
        "output_config": {"format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
    }


def label_result(message) -> LabelResult:
    """The label of a response, None unless it ended its turn with exactly one valid answer."""
    usage = message.usage
    texts = [block.text for block in message.content if block.type == "text"]
    label = None
    if message.stop_reason == "end_turn" and len(texts) == 1:
        try:
            label = label_from_answer(json.loads(texts[0]))
        except json.JSONDecodeError:
            label = None
    return LabelResult(label, usage.input_tokens, usage.output_tokens)


class ClaudeApiTextLabeler:
    """A ``TextLabeler`` that asks a Claude model through the Messages API, one text at a time."""

    def __init__(self, client, model_id: str) -> None:
        self._client = client
        self._model_id = model_id

    def label(self, description: str) -> LabelResult:
        return label_result(
            self._client.messages.create(**message_request(self._model_id, description))
        )
