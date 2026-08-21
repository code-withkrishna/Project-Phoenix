"""Unit tests for deterministic compact reference ID generator."""

import uuid

import pytest

from app.services.recovery.reference import generate_reference_id


def test_reference_id_format_and_length() -> None:
    """Verify reference ID format is PHX_<short_case_id>_<seq> and length <= 40."""
    case_id = uuid.UUID("7f4a21c9-1234-5678-9abc-def012345678")
    ref_1 = generate_reference_id(case_id, sequence=1)
    assert ref_1 == "PHX_7F4A21C9_01"
    assert len(ref_1) <= 40

    ref_2 = generate_reference_id(case_id, sequence=2)
    assert ref_2 == "PHX_7F4A21C9_02"
    assert len(ref_2) <= 40


def test_reference_id_is_deterministic() -> None:
    """Same input always produces exact same reference ID."""
    case_id = uuid.uuid4()
    ref_a = generate_reference_id(case_id, sequence=1)
    ref_b = generate_reference_id(case_id, sequence=1)
    assert ref_a == ref_b


def test_reference_id_string_input() -> None:
    """Handles string case_id correctly."""
    ref = generate_reference_id("01J8F9X2Q9Z8K3V01N5A7B8C9D", sequence=1)
    assert ref.startswith("PHX_01J8F9X2_01")
    assert len(ref) <= 40
