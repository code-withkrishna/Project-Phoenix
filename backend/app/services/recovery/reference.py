"""Deterministic compact reference ID generator."""

from uuid import UUID


def generate_reference_id(case_id: UUID | str, sequence: int = 1) -> str:
    """Generate a deterministic, compact reference ID <= 40 characters.

    Format: PHX_<short_case_id>_<sequence:02d>
    Example: PHX_7F4A21C9_01

    Constraints:
    - <= 40 characters (Razorpay reference_id max length)
    - Deterministic
    - ASCII-safe
    - Unique per recovery attempt
    """
    clean_id = str(case_id).replace("-", "").upper()
    short_case_id = clean_id[:8]
    ref_id = f"PHX_{short_case_id}_{sequence:02d}"
    if len(ref_id) > 40:
        raise ValueError(f"Generated reference ID exceeds 40 characters: '{ref_id}'")
    return ref_id
