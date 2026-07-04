"""The newsroom section enum is a closed, exact vocabulary."""

from __future__ import annotations

from newsroom.contracts.sections import SECTION_ENUM, is_valid_section


def test_enum_is_exact():
    assert SECTION_ENUM == ("tech", "world", "politics", "economics")


def test_valid_sections_pass():
    for section in SECTION_ENUM:
        assert is_valid_section(section)


def test_invalid_sections_rejected():
    for section in ("sports", "entertainment", "", "Tech", "world ", "TECH"):
        assert not is_valid_section(section)
