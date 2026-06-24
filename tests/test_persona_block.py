"""``persona_block`` renders a persona for FRESH re-injection on every draft call.
The LANGUAGE directive sits near the top of the block so a local Gemma-class model
(which drifts back to English over a long generation) is told, in strong terms,
which language to write in and to never switch.
"""

from __future__ import annotations

from newsroom.personas import Persona
from newsroom.pipeline.context import persona_block


def _persona(**over) -> Persona:
    base = dict(
        display_name="Mara Vance",
        beat="politics",
        who_i_am="You ARE Mara Vance, a wire-service political reporter.",
        style="Plain declaratives, attribute every claim.",
    )
    base.update(over)
    return Persona(**base)


def test_block_states_the_default_language_directive():
    block = persona_block(_persona())
    assert "You ALWAYS write in español neutro. Never switch languages." in block


def test_block_uses_the_personas_explicit_language():
    block = persona_block(_persona(language="English"))
    assert "You ALWAYS write in English. Never switch languages." in block
    assert "español neutro" not in block


def test_language_directive_sits_near_the_top_of_the_block():
    # Right after the "You ARE <name>" line, before the rest of the identity, so the
    # instruction is not buried under a long voice description.
    lines = persona_block(_persona()).splitlines()
    assert lines[0].startswith("You ARE Mara Vance")
    assert lines[1] == "You ALWAYS write in español neutro. Never switch languages."
