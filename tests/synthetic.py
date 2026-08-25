"""Synthetic provider identities — the only provider data this repo is allowed to hold.

Every other lane's fixtures are *recorded* from the live API, because the response is public data
and the recording is the point (docs/structured-api-tools.md §8). Provider-shaped responses are the
exception, and the reason is not licensing but people: NPPES and the Marketplace provider endpoints
return **real clinicians** — names, NPIs, street addresses, telephone numbers. §8a decided that none
of that enters this repo, so provider fixtures are built here instead of recorded.

**Nothing in this module is a literal.** Identifiers are *computed*, which is what keeps
`scripts/scan_sensitive.py`'s `pii:npi` count at a true zero rather than an allowlisted one. That
distinction is the whole value of the tripwire: if the fixture directory were allowlisted for
`pii:npi`, the scanner would stop catching the one mistake §8a exists to prevent — someone
committing a *real* recorded provider response. The tripwire stays armed by never handing it
anything to forgive.

**Shape fidelity is the risk this trades for.** A synthetic response is only useful if it is shaped
like the real one, and a hand-written builder can drift from an upstream nobody re-checks. So the
shapes below are transcribed from responses observed on 2026-08-22 and recorded in
docs/structured-api-tools.md §10a, and `tests/test_live_clients.py` asserts the field families the
wrappers actually depend on rather than trusting the transcription wholesale.

**Do not add a literal NPI to this file, or to any other tracked file.** A Luhn-valid ten-digit run
beside the word "NPI" is exactly what the scanner catches, and prose is not exempt — the same
convention the glossary states for SSNs: *write the pattern, never a specimen.*
"""

from typing import Any

#: The constant NPPES prepends before the Luhn check. An NPI's tenth digit validates over
#: `80840` + the first nine digits, which is why a random ten-digit run is almost never a real NPI
#: and why a synthetic one has to be *constructed* rather than typed.
NPI_LUHN_PREFIX = "80840"

#: The NANP block reserved for fiction (555-0100 through 555-0199). Numbers here cannot ring a real
#: subscriber, which is what makes them safe to commit — and it is why the allowlist entry covering
#: them is scoped by *pattern* rather than by path: a path-scoped exemption would forgive a real
#: number that happened to land in the same file.
FICTIONAL_EXCHANGE = "555"


def _luhn_check_digit(digits: str) -> int:
    """The Luhn check digit for `digits`, doubling from the rightmost position.

    Written out rather than pulled from `scripts/scan_sensitive.py`: the scanner *validates* and
    this *generates*, and having the two agree by independent implementation is worth more here
    than sharing one. `test_synthetic.py` asserts they agree.
    """
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 0:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return (10 - total % 10) % 10


def synthetic_npi(seed: int) -> str:
    """A structurally valid, deliberately fictional NPI.

    Valid because the wrappers validate: a fixture whose check digit failed could never exercise
    the happy path, so "use an invalid number and the scanner stays quiet" is not available to us
    (§8a). Deterministic in `seed` so a fixture is stable across runs and a diff stays readable.

    The first nine digits start from `1`, which is the range NPPES actually issues to individuals
    and organizations — a number that looked wrong in the first digit would be a poor stand-in for
    one that has to survive real validation.
    """
    body = f"{1_000_000_00 + seed * 7919:09d}"[:9]
    return body + str(_luhn_check_digit(NPI_LUHN_PREFIX + body))


def synthetic_phone(seed: int) -> str:
    """A telephone number from the NANP block reserved for fiction."""
    return f"{FICTIONAL_EXCHANGE}-{FICTIONAL_EXCHANGE}-{100 + seed % 100:04d}"


def synthetic_person(seed: int) -> tuple[str, str]:
    """A first and last name that are obviously placeholders when read in a diff."""
    first = ("AVERY", "BLAIR", "CASEY", "DEVON", "EMERY")[seed % 5]
    last = ("EXAMPLEFIRST", "EXAMPLESECOND", "EXAMPLETHIRD")[seed % 3]
    return first, last


def synthetic_address(seed: int, *, purpose: str = "LOCATION") -> dict[str, Any]:
    """One NPPES-shaped address. `purpose` is `LOCATION` or `MAILING`, as upstream sends them."""
    address: dict[str, Any] = {
        "address_1": f"{100 + seed} EXAMPLE ST",
        "address_purpose": purpose,
        "address_type": "DOM",
        "city": "SPRINGFIELD",
        "country_code": "US",
        "country_name": "United States",
        "postal_code": f"{27360 + seed % 10}",
        "state": "NC",
    }
    if purpose == "LOCATION":
        address["telephone_number"] = synthetic_phone(seed)
    return address


def synthetic_taxonomy(
    desc: str, *, primary: bool, code: str = "363LF0000X", state: str = "NC"
) -> dict[str, Any]:
    """One NPPES taxonomy entry — the specialty record §10a.2 warns about.

    A provider may hold several and **exactly one is primary**, which is why this takes `primary`
    explicitly: a builder that defaulted it would make the multi-taxonomy test impossible to write
    by accident.
    """
    return {
        "code": code,
        "taxonomy_group": "",
        "desc": desc,
        "state": state,
        "license": "PLACEHOLDER",
        "primary": primary,
    }


def synthetic_provider(
    seed: int = 1,
    *,
    taxonomies: list[dict[str, Any]] | None = None,
    enumeration_type: str = "NPI-1",
) -> dict[str, Any]:
    """One NPPES `results[]` entry, shaped as observed on 2026-08-22 (§10a).

    Note the identifier field is **`number`**, not `npi` — that is upstream's spelling, and §10a.3
    records that it is also the accident that keeps an NPPES-shaped fixture from tripping the
    scanner's label filter. The wrappers rename it; the fixture must not.
    """
    first, last = synthetic_person(seed)
    return {
        "number": synthetic_npi(seed),
        "enumeration_type": enumeration_type,
        "basic": {
            "first_name": first,
            "last_name": last,
            "credential": "NP",
            "sole_proprietor": "NO",
            "gender": "F",
            "enumeration_date": "2013-06-25",
            "last_updated": "2025-12-24",
            "certification_date": "2025-12-24",
            "status": "A",
        },
        "addresses": [
            synthetic_address(seed, purpose="MAILING"),
            synthetic_address(seed, purpose="LOCATION"),
        ],
        "taxonomies": taxonomies
        if taxonomies is not None
        else [synthetic_taxonomy("Family Nurse Practitioner", primary=True)],
        "identifiers": [],
        "other_names": [],
        "practiceLocations": [],
        "endpoints": [],
        "created_epoch": "1372118400000",
        "last_updated_epoch": "1766534400000",
    }


def synthetic_nppes_response(*providers: dict[str, Any]) -> dict[str, Any]:
    """The NPPES envelope. `result_count: 0` with an empty list is upstream's "no such NPI"."""
    return {"result_count": len(providers), "results": list(providers)}


__all__ = [
    "FICTIONAL_EXCHANGE",
    "NPI_LUHN_PREFIX",
    "synthetic_address",
    "synthetic_nppes_response",
    "synthetic_npi",
    "synthetic_person",
    "synthetic_phone",
    "synthetic_provider",
    "synthetic_taxonomy",
]
