"""The prompt and the answer schema of one label version.

``LABEL_VERSION`` names the prompt, the schema and the model together: a change of any of them is
a new version, never an edit of this one.
"""

from src.enrichment.labeler import RENOVATION_STATES

LABEL_VERSION = "tl3"

ANSWER_TOOL_NAME = "record_text_label"

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "renovation_state": {
            "type": "string",
            "enum": list(RENOVATION_STATES),
            "description": "What the description says about the home as a whole.",
        },
        "is_auction": {
            "type": "boolean",
            "description": "Whether the home is acquired through an auction or the assignment "
            "of an auction award.",
        },
    },
    "required": ["renovation_state", "is_auction"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You read one description of a home for sale, written in Spanish, and answer with two labels, \
renovation_state and is_auction, and nothing else. Judge only what the description says, not \
what you would guess from the price or the location. Text about the agency, financing or the \
neighbourhood says nothing about the home. [phone] and [name] mark redacted text.

renovation_state is what the description says about the home as a whole, decided in this order; \
the first that applies wins:
1. needs_renovation: it needs works or is sold to be renovated ("para reformar", "a reformar", \
"necesita reforma", "para actualizar");
2. new_build: new construction, never lived in;
3. renovated: the home as a whole was renovated, whenever that was ("reformado", \
"reforma integral", "totalmente reformado", "recién reformado", "a estrenar tras reforma");
4. original: habitable as it is, with no whole renovation stated; "buen estado" and \
"para entrar a vivir" belong here, and so does a home of which only parts were renewed;
5. unknown: the description says nothing about the state.
needs_renovation applies only when the description states a need for works or sells the home to \
be renovated. A habitable home for which updating is offered as an option is original.
renovated needs a renovation of the home itself. When the description says the renovation was \
partial ("parcialmente reformado"), or calls only some rooms or elements renewed ("cocina \
reformada", "baños reformados", a renewed living room, new windows or floors), the home is \
original, however complete or recent those works are. A description that calls the home \
renovated and then lists what was renewed is renovated.

is_auction is true when the home is acquired through an auction or the assignment of an auction \
award ("subasta", "puja", "cesión de remate"), and false otherwise, also when the text says it is \
not an auction. An agency's mention of auctions of other properties is not an auction.
"""
