"""Tests for `tests/synthetic.py` — the provider identities §8a decided must be manufactured.

Small, and load-bearing out of proportion to its size. The decision was that no real clinician's
name, NPI, address or telephone number enters this repo; these assert that the substitute is
*structurally real* — because a fixture whose identifiers fail validation could never exercise the
happy path, which was the reason "just use invalid numbers" was rejected.

`test_the_scanners_own_validator_accepts_them` is the one that matters: it checks the generator
against `scripts/scan_sensitive.py`'s `npi_luhn` rather than against a second copy of the algorithm,
so the two independent implementations have to agree.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import scan_sensitive  # noqa: E402
from synthetic import (  # noqa: E402
    synthetic_npi,
    synthetic_nppes_response,
    synthetic_phone,
    synthetic_provider,
    synthetic_taxonomy,
)


def test_the_scanners_own_validator_accepts_them() -> None:
    """Generated here, validated there. Two implementations that must agree."""
    npi_luhn = scan_sensitive.VALIDATORS["npi_luhn"]
    assert all(npi_luhn(synthetic_npi(seed)) for seed in range(25))


def test_they_are_distinct_and_deterministic() -> None:
    """Distinct so a multi-provider fixture is not one provider repeated; deterministic so a
    fixture is stable across runs and a diff stays readable."""
    ids = [synthetic_npi(seed) for seed in range(25)]
    assert len(set(ids)) == len(ids)
    assert ids == [synthetic_npi(seed) for seed in range(25)]


def test_phones_come_from_the_block_reserved_for_fiction() -> None:
    """555-01xx cannot ring a real subscriber, which is what makes it committable."""
    assert all(synthetic_phone(seed).startswith("555-555-01") for seed in range(20))


def test_the_provider_record_keeps_upstreams_field_names() -> None:
    """The NPI field is `number`, as NPPES spells it (§10a). A fixture that renamed it to `npi`
    would be testing our vocabulary against itself instead of the upstream's."""
    record = synthetic_provider(1)
    assert "number" in record and "npi" not in record
    assert {"basic", "addresses", "taxonomies"} <= set(record)


def test_a_provider_can_hold_several_taxonomies_with_one_primary() -> None:
    """§10a.2's trap, made expressible: the wrapper must pick the primary, not the first."""
    record = synthetic_provider(
        2,
        taxonomies=[
            synthetic_taxonomy("Internal Medicine", primary=False),
            synthetic_taxonomy("Cardiology", primary=True),
        ],
    )
    primary = [t for t in record["taxonomies"] if t["primary"]]
    assert len(primary) == 1
    assert primary[0]["desc"] == "Cardiology"
    assert record["taxonomies"][0]["primary"] is False, (
        "the non-primary taxonomy must come first, or the test cannot catch a [0] read"
    )


@pytest.mark.parametrize("count", [0, 1, 3])
def test_the_envelope_reports_its_own_count(count: int) -> None:
    """`result_count: 0` is NPPES's "no such NPI" — an answer, not an outage (§10a.1)."""
    response = synthetic_nppes_response(*[synthetic_provider(i) for i in range(count)])
    assert response["result_count"] == count
    assert len(response["results"]) == count
