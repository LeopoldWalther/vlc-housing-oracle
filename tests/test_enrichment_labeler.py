"""The text label, the validation of a model's answer, and the prompt."""

import unittest

from src.enrichment.labeler import RENOVATION_STATES, TextLabel, label_from_answer
from src.enrichment.prompt import ANSWER_SCHEMA, ANSWER_TOOL_NAME, LABEL_VERSION, SYSTEM_PROMPT


class LabelFromAnswerTest(unittest.TestCase):
    def test_each_state_with_a_boolean_is_a_label(self) -> None:
        for state in RENOVATION_STATES:
            for auction in (True, False):
                with self.subTest(state=state, auction=auction):
                    self.assertEqual(
                        label_from_answer({"renovation_state": state, "is_auction": auction}),
                        TextLabel(state, auction),
                    )

    def test_an_answer_outside_the_schema_is_none(self) -> None:
        answers = (
            {"renovation_state": "ruined", "is_auction": False},
            {"renovation_state": "original"},
            {"is_auction": True},
            {"renovation_state": "original", "is_auction": False, "reason": "x"},
            {"renovation_state": "original", "is_auction": 0},
            {"renovation_state": "original", "is_auction": 1},
            {"renovation_state": "original", "is_auction": "true"},
            {"renovation_state": None, "is_auction": False},
            ["original", False],
            "original",
            None,
        )
        for answer in answers:
            with self.subTest(answer=answer):
                self.assertIsNone(label_from_answer(answer))

    def test_a_label_is_immutable(self) -> None:
        label = TextLabel("original", False)
        with self.assertRaises(AttributeError):
            label.is_auction = True  # type: ignore[misc]


class PromptTest(unittest.TestCase):
    def test_the_states_are_the_five_values_in_decision_order(self) -> None:
        self.assertEqual(
            RENOVATION_STATES,
            ("new_build", "renovated", "original", "needs_renovation", "unknown"),
        )

    def test_the_schema_requires_both_properties_and_nothing_else(self) -> None:
        self.assertEqual(ANSWER_SCHEMA["type"], "object")
        self.assertEqual(set(ANSWER_SCHEMA["required"]), {"renovation_state", "is_auction"})
        self.assertIs(ANSWER_SCHEMA["additionalProperties"], False)
        self.assertEqual(
            tuple(ANSWER_SCHEMA["properties"]["renovation_state"]["enum"]), RENOVATION_STATES
        )
        self.assertEqual(ANSWER_SCHEMA["properties"]["is_auction"]["type"], "boolean")

    def test_the_prompt_names_every_state_and_the_tool(self) -> None:
        for state in RENOVATION_STATES:
            with self.subTest(state=state):
                self.assertIn(state, SYSTEM_PROMPT)
        self.assertIn(ANSWER_TOOL_NAME, SYSTEM_PROMPT)
        for keyword in ("subasta", "puja", "cesión de remate", "para reformar", "buen estado"):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, SYSTEM_PROMPT)

    def test_the_label_version_is_tl1(self) -> None:
        self.assertEqual(LABEL_VERSION, "tl1")


if __name__ == "__main__":
    unittest.main()
