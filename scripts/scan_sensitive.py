#!/usr/bin/env python3
"""Scan everything public-bound for secrets, PII/PHI, and licence-restricted content.

This repo is **public**. The "Public-repo data guardrail" in CLAUDE.md forbids committing
three unrelated things, and until now each was policed differently or not at all:

  1. credentials  - API keys, tokens, private keys. Checked only by hand, ad hoc.
  2. PII / PHI    - not checked at all.
  3. licence-restricted content - AMA/ADA CPT/CDT code tables, LCD and Billing/Coding
                    Article data. Checked by `scan_licensing()` in
                    download_medicare_ncd.py, but that function only ever sees the
                    medicare_ncd records it is normalizing. It cannot see healthcare_gov,
                    medicare_pubs, or whatever a future source adds.

This script is the one check that covers all three across everything that is or would
become public: tracked files, staged files, and untracked-but-not-ignored files. Gitignored
paths are invisible by construction, which is exactly right - that is what keeps `.env` and
the 45 MB of medicare_pubs PDFs out of scope.

THREE SEVERITIES, NOT TWO
-------------------------
docs/medicare_ncd_data.md records the lesson this design turns on: a scan that fails the
build on legitimate narrative mentions of CPT "would just get switched off". So:

  BLOCKING     -> exit 1. Do not commit. No judgement call available.
  ADVISORY     -> reported with context and compared against a recorded baseline in
                  sensitive_baseline.toml. Investigate a *jump*, not a nonzero. Exit 2 on
                  drift above baseline.
  ALLOWLISTED  -> suppressed, but counted and named, so suppression stays visible rather
                  than silent. Every allowlist entry carries a reason.

The advisory tier is not a softer blocking tier. It exists because this corpus legitimately
contains code mentions ("changed CPT code", "add HCPCS code G0465") and CMS institutional
e-mail addresses, and because from Phase 3 the NPPES data will legitimately contain real
provider names and NPIs that are FOIA-disclosable (see docs/glossary.md). Making any of
those blocking would guarantee this script gets disabled the week that data lands.

SELF-TEST, AND WHY THE CANARIES ARE CONCATENATED
------------------------------------------------
A security scanner's worst failure is a silently dead pattern - it reports "clean" forever.
`--self-test` asserts every detector fires against a synthetic canary, and that a set of
*anti*-canaries do not fire. The canaries are built by string concatenation ("AKIA" + ...)
so the literal never appears in this file; otherwise the scanner would flag its own source
and we would need a self-exclusion, which is a hole someone could hide a real key in.

The anti-canaries encode a real false positive from the manual sweep this script replaces:
a bare `sk-[A-Za-z0-9_-]{20,}` pattern matched inside the ordinary URL slug
"ask-about-preventive-services". The fix was a tighter pattern - vendor-anchored prefixes
plus a lookbehind - not an allowlist entry. Allowlisting a regex bug hides the next true
positive that shares its shape.

Usage (stdlib only - no dependency, nothing to install):
  uv run python scripts/scan_sensitive.py              # everything public-bound
  uv run python scripts/scan_sensitive.py --staged     # only what is staged
  uv run python scripts/scan_sensitive.py --unstaged   # only unstaged + untracked work
  uv run python scripts/scan_sensitive.py --staged --unstaged   # everything uncommitted
  uv run python scripts/scan_sensitive.py --self-test  # prove the detectors still fire
  uv run python scripts/scan_sensitive.py --json       # machine-readable findings
  uv run python scripts/scan_sensitive.py --deep-history   # every blob ever committed

Note that the advisory baseline counts the *whole repo*, so it is only compared in a
full-repo scan. Under --staged, --unstaged or --paths the advisory hits are listed but not
compared, and cannot produce exit 2 - otherwise every scoped scan would report each marker
as "below baseline" and advise lowering it. Blocking hits work identically in every scope.

Exit codes: 0 clean, 1 blocking hit, 2 advisory count above baseline (full-repo scans only).
See .claude/skills/scan-sensitive/SKILL.md for how to triage what this reports.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = Path(__file__).resolve().parent / "sensitive_baseline.toml"

# Corpus payload only. Deliberately NOT all of data/: data/README.md discusses CPT, CDT and
# the AMA by name, and docs/glossary.md defines them - prose *about* the guardrail must not
# trip the guardrail. The risk being detected is a code table inside ingested data.
CORPUS_GLOBS = ("data/raw/*", "data/processed/*")

# Filenames that are blocking on their name alone, no content match needed.
SECRET_FILENAME_GLOBS = (
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*id_rsa",
    "*id_dsa",
    "*id_ecdsa",
    "*id_ed25519",
    ".env",
    ".env.*",
    "*/.env",
    "*/.env.*",
    "*credentials*",
    "*.jks",
    "*.keystore",
)
# ...except these, which are meant to be committed.
SECRET_FILENAME_EXCEPTIONS = ("*.env.example", ".env.example", "*.env.template")

# Values that look like a secret assignment but are placeholders. Checked against the
# captured value, not the whole line.
PLACEHOLDER_RE = re.compile(
    r"^(?:\$\{|\$[A-Z_]|<|\{\{|%\(|os\.environ|getenv)"
    r"|(?:your|my|some|the)[-_ ]?(?:key|token|secret|password)"
    r"|^(?:x{4,}|\*{4,}|\.{3,}|changeme|example|placeholder|redacted|dummy|fake|test)",
    re.I,
)


@dataclass(frozen=True)
class Marker:
    """One detector. `key` is stable - the baseline and allowlist reference it by name."""

    key: str
    label: str
    pattern: str
    severity: str  # "blocking" | "advisory"
    layer: str  # "cred" | "pii" | "licensing"
    canary: str | None = None
    path_globs: tuple[str, ...] = ()  # empty = every file
    ignore_case: bool = False
    validator: str | None = None  # name of an extra check the raw regex cannot express

    def compiled(self) -> re.Pattern[str]:
        return re.compile(self.pattern, re.I if self.ignore_case else 0)

    def applies_to(self, rel_path: str) -> bool:
        if not self.path_globs:
            return True
        return any(fnmatch.fnmatch(rel_path, g) for g in self.path_globs)


# --------------------------------------------------------------------------- #
# Layer 1 - credentials. All blocking: there is no benign reason for a live key
# to be in a public repo, so no judgement call is offered.
# --------------------------------------------------------------------------- #
CREDENTIAL_MARKERS = [
    Marker(
        key="cred:aws-access-key",
        label="AWS access key id",
        pattern=r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])",
        severity="blocking",
        layer="cred",
        canary="AKIA" + "IOSFODNN7EXAMPLE",
    ),
    Marker(
        key="cred:github-token",
        label="GitHub token",
        pattern=r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{22,})",
        severity="blocking",
        layer="cred",
        canary="ghp_" + "A" * 36,
    ),
    Marker(
        key="cred:slack-token",
        label="Slack token",
        pattern=r"(?<![A-Za-z0-9_])xox[baprs]-[A-Za-z0-9-]{10,}",
        severity="blocking",
        layer="cred",
        canary="xoxb-" + "123456789012-abcdefghij",
    ),
    Marker(
        key="cred:google-api-key",
        label="Google API key",
        pattern=r"(?<![A-Za-z0-9_])AIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])",
        severity="blocking",
        layer="cred",
        canary="AIza" + "S" * 35,
    ),
    Marker(
        key="cred:google-oauth",
        label="Google OAuth access token",
        pattern=r"(?<![A-Za-z0-9_])ya29\.[0-9A-Za-z_-]{20,}",
        severity="blocking",
        layer="cred",
        canary="ya29." + "a" * 25,
    ),
    Marker(
        # Vendor-anchored on purpose. A bare `sk-` prefix is what produced the
        # "ask-about-preventive-services" false positive - see the module docstring.
        key="cred:vendor-secret-key",
        label="Anthropic / OpenAI / Stripe secret key",
        pattern=(
            r"(?<![A-Za-z0-9_-])(?:sk-ant-|sk-proj-|sk-live-|sk_live_|rk_live_|pk_live_)"
            r"[A-Za-z0-9_-]{16,}"
        ),
        severity="blocking",
        layer="cred",
        canary="sk-ant-" + "api03-" + "A" * 20,
    ),
    Marker(
        key="cred:private-key-block",
        label="PEM private key block",
        pattern=r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----",
        severity="blocking",
        layer="cred",
        canary="-----BEGIN " + "RSA PRIVATE KEY-----",
    ),
    Marker(
        # Requiring the payload segment to also start with eyJ (base64 of '{"') cuts the
        # false-positive rate hard versus matching any three dot-separated base64 runs.
        key="cred:jwt",
        label="JSON Web Token",
        pattern=(
            r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}"
            r"\.[A-Za-z0-9_-]{8,}"
        ),
        severity="blocking",
        layer="cred",
        canary="eyJ" + "hbGciOiJIUzI1NiJ9." + "eyJ" + "zdWIiOiIxMjM0NSJ9." + "dBjftJeZ4CVP9mB92K",
    ),
    Marker(
        # The lookbehind is load-bearing: it stops `next_token` (a pagination cursor in
        # download_medicare_ncd.py and in report.json) from reading as `token`.
        key="cred:assignment",
        label="secret-shaped assignment",
        pattern=(
            r"(?<![A-Za-z0-9_])"
            r"(?:api[_-]?key|apikey|secret|password|passwd|access[_-]?key|private[_-]?key|"
            r"client[_-]?secret|auth[_-]?token|access[_-]?token|bearer|token)"
            r"[\"']?\s*[:=]\s*[\"']([^\"'\s]{12,})[\"']"
        ),
        severity="blocking",
        layer="cred",
        ignore_case=True,
        validator="not_placeholder",
        canary='api_key = "' + "A1b2C3d4E5f6G7h8" + '"',
    ),
    Marker(
        key="cred:url-credentials",
        label="credentials embedded in a URL",
        pattern=r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/:@\"']{1,64}:[^\s/@\"']{1,64}@",
        severity="blocking",
        layer="cred",
        canary="https://" + "user:hunter2@example.com",
    ),
]

# --------------------------------------------------------------------------- #
# Layer 2 - PII / PHI. Only an SSN is blocking. Everything else is advisory
# because this repo legitimately carries CMS institutional contact addresses, and
# Phase 3's NPPES data legitimately names real practitioners (FOIA-disclosable).
#
# Deliberately absent: a date-of-birth detector. In a corpus whose whole point is
# effective dates, transmittal dates and revision histories, a date-shaped matcher
# is pure noise - it would report thousands of hits and teach everyone to ignore
# the PII layer. DOB in this repo would arrive attached to a name, and the name is
# what the email/phone/NPI markers catch.
# --------------------------------------------------------------------------- #
PII_MARKERS = [
    Marker(
        key="pii:ssn",
        label="US Social Security number",
        pattern=r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])",
        severity="blocking",
        layer="pii",
        canary="123-45-" + "6789",
    ),
    Marker(
        key="pii:email",
        label="e-mail address",
        pattern=r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        severity="advisory",
        layer="pii",
        canary="person" + "@example.com",
    ),
    Marker(
        key="pii:phone",
        label="US phone number",
        pattern=r"(?<![\d-])(?:\+?1[ .-])?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\d-])",
        severity="advisory",
        layer="pii",
        canary="555" + "-867-5309",
    ),
    Marker(
        # Two filters, because either alone is useless. Luhn (over the NPI's 80840 prefix)
        # rejects ~90% of arbitrary digit runs, but 10% survive by coincidence: the first
        # draft of this marker reported digits inside a UUID, a vendor PDF filename, and an
        # IRS publink anchor. So the identifier must also be *labelled* NPI, which is how
        # it appears in NPPES data and in any prose that means it.
        key="pii:npi",
        label="NPI (National Provider Identifier)",
        pattern=r"\bNPIs?\b[^\n]{0,40}?(?<!\d)(\d{10})(?!\d)",
        severity="advisory",
        layer="pii",
        ignore_case=True,
        validator="npi_luhn",
        canary="NPI: " + "1234567893",
    ),
]

# --------------------------------------------------------------------------- #
# Layer 3 - licence-restricted content. The first two dicts are lifted verbatim
# from download_medicare_ncd.py:112-125.
#
# SOURCE OF TRUTH: scripts/download_medicare_ncd.py. `scripts/` is not a package
# and that module is import-heavy (requests, bs4), so these are copied rather than
# imported. If you change one, change the other.
#
# Note how precise the AMA marker is - it requires "copyright" or "rights" within
# 40 characters. That precision is load-bearing and verified: NCD 210.2 says a
# guideline "was developed by the Centers for Medicare & Medicaid Services and the
# American Medical Association", which is a co-authorship credit, not a copyright
# notice. A bare name match would block the repo on it.
# --------------------------------------------------------------------------- #
LICENSING_MARKERS = [
    Marker(
        key="licensing:copyright-symbol",
        label="copyright symbol",
        pattern=r"©",
        severity="blocking",
        layer="licensing",
        path_globs=CORPUS_GLOBS,
        canary="© 2026",
    ),
    Marker(
        key="licensing:all-rights-reserved",
        label="all rights reserved",
        pattern=r"all rights reserved",
        severity="blocking",
        layer="licensing",
        path_globs=CORPUS_GLOBS,
        ignore_case=True,
        canary="All Rights " + "Reserved",
    ),
    Marker(
        key="licensing:ama-notice",
        label="AMA copyright notice",
        pattern=r"American Medical Association.{0,40}(?:copyright|rights)",
        severity="blocking",
        layer="licensing",
        path_globs=CORPUS_GLOBS,
        ignore_case=True,
        canary="American Medical Association" + " holds the copyright",
    ),
    Marker(
        key="licensing:ada-dental",
        label="ADA (dental)",
        pattern=r"American Dental Association",
        severity="blocking",
        layer="licensing",
        path_globs=CORPUS_GLOBS,
        ignore_case=True,
        canary="American Dental " + "Association",
    ),
    Marker(
        key="licensing:cdt",
        label="CDT (ADA dental code set)",
        pattern=r"\bCDT\b",
        severity="blocking",
        layer="licensing",
        path_globs=CORPUS_GLOBS,
        canary="CDT" + " code",
    ),
    Marker(
        # Evidence that something fetched a licence-gated endpoint. Nothing from behind
        # the AMA/ADA/AHA token may ever reach this repo.
        key="licensing:gated-endpoint",
        label="licence-gated MCD endpoint in vendored data",
        pattern=r"/data/lcd|/data/article|license-agreement",
        severity="blocking",
        layer="licensing",
        path_globs=CORPUS_GLOBS,
        ignore_case=True,
        canary="/v1/data/" + "lcd/",
    ),
    Marker(
        key="licensing:cpt",
        label="CPT mentioned in prose",
        pattern=r"\bCPT\b",
        severity="advisory",
        layer="licensing",
        path_globs=CORPUS_GLOBS,
        canary="corrected CPT" + " codes",
    ),
    Marker(
        key="licensing:hcpcs",
        label="HCPCS mentioned in prose",
        pattern=r"\bHCPCS\b",
        severity="advisory",
        layer="licensing",
        path_globs=CORPUS_GLOBS,
        canary="add HCPCS" + " code",
    ),
    Marker(
        key="licensing:hcpcs-shaped",
        label="HCPCS Level II-shaped token",
        pattern=r"\b[A-Z]\d{4}\b",
        severity="advisory",
        layer="licensing",
        path_globs=CORPUS_GLOBS,
        canary="G0" + "465",
    ),
]

ALL_MARKERS = CREDENTIAL_MARKERS + PII_MARKERS + LICENSING_MARKERS

# Text that must NOT match the named marker. Each one encodes a real false positive or a
# near-miss worth defending; a regression here means a pattern got loosened.
ANTI_CANARIES = [
    ("ask-about-preventive-services", "cred:vendor-secret-key"),
    ("ask-when-choosing-a-plan-spanish", "cred:vendor-secret-key"),
    ('"next_token": "abcdefghijklmnopqrs"', "cred:assignment"),
    ('api_key = "${' + 'MY_SECRET}"', "cred:assignment"),
    ('api_key = "your-key-here"', "cred:assignment"),
    ("https://api.coverage.cms.gov/v1/data/ncd/", "cred:url-credentials"),
    ("developed by CMS and the American Medical Association.", "licensing:ama-notice"),
    ("effective 2026-07-31 per transmittal", "pii:ssn"),
    ("NPI: " + "1234567890", "pii:npi"),  # labelled, but fails the NPI Luhn check
    # Luhn-valid by coincidence, but unlabelled — a vendor PDF filename and an IRS anchor
    # that the first draft of the NPI marker reported as provider identifiers.
    ("Viant-Privacy-Policy-AMERICAS-1094727173-v11.pdf", "pii:npi"),
    ("https://www.irs.gov/publications/p969#en_US_2016_publink1000204176", "pii:npi"),
]


@dataclass
class Finding:
    marker: Marker
    path: str
    line: int
    matched: str
    context: str
    allowlisted_by: str | None = None


@dataclass
class Allow:
    marker: str
    match: str | None
    match_regex: str | None
    path: str | None
    reason: str

    def covers(self, finding: Finding) -> bool:
        if self.marker not in ("*", finding.marker.key):
            return False
        if self.match is not None and not fnmatch.fnmatch(finding.matched, self.match):
            return False
        if self.match_regex is not None and not re.search(self.match_regex, finding.matched):
            return False
        if self.path is not None and not fnmatch.fnmatch(finding.path, self.path):
            return False
        # A bare marker name is not a valid entry — that would mute a whole detector.
        return any(x is not None for x in (self.match, self.match_regex, self.path))


@dataclass
class Baseline:
    allows: list[Allow] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Validators - checks a regex cannot express
# --------------------------------------------------------------------------- #
def npi_luhn(value: str) -> bool:
    """True if `value` is a structurally valid NPI.

    An NPI is 10 digits validated by Luhn over the 80840 prefix plus the first 9 digits.
    Without this, the marker is "any 10-digit number" and reports timestamps and row ids.
    """
    digits = [int(c) for c in "80840" + value[:9]]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (total + int(value[9])) % 10 == 0


def not_placeholder(value: str) -> bool:
    return not PLACEHOLDER_RE.search(value)


VALIDATORS = {"npi_luhn": npi_luhn, "not_placeholder": not_placeholder}


# --------------------------------------------------------------------------- #
# Redaction - a scanner that reprints secrets into a shareable log is a leak amplifier
# --------------------------------------------------------------------------- #
def redact(marker: Marker, value: str) -> str:
    if marker.layer == "cred":
        return f"{value[:4]}…[{len(value)} chars, redacted]"
    if marker.key == "pii:email":
        local, _, domain = value.partition("@")
        return f"{local[:1]}***@{domain}" if domain else "[redacted]"
    if marker.layer == "pii":
        return f"[redacted {marker.key}, {len(value)} chars]"
    return value[:120]


# --------------------------------------------------------------------------- #
# Target selection
# --------------------------------------------------------------------------- #
def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout if result.returncode == 0 else ""


def target_files(staged: bool, unstaged: bool, paths: list[str] | None) -> list[Path]:
    """Select what to scan. Three scopes, matching how you actually work:

      (neither flag)  everything that is or would become public - tracked plus
                      untracked-but-not-ignored. The pre-publish gate.
      --staged        only what is staged for the next commit. The pre-commit gate.
      --unstaged      only what git reports under "Changes not staged for commit" and
                      "Untracked files" - i.e. work in progress that has not been staged.
                      The gate to run *before* `git add`, so a secret is caught before it
                      reaches the index.

    The flags are additive rather than mutually exclusive: passing both scans everything
    uncommitted, staged or not, which is the honest answer to "is my working tree clean?"

    Gitignored paths never appear in any scope - that is what correctly keeps `.env` and
    data/raw/medicare_pubs/pdf/ out of scope.
    """
    if staged or unstaged:
        chunks = []
        if staged:
            # index vs HEAD
            chunks.append(git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"))
        if unstaged:
            # working tree vs index, plus untracked files git would let you add
            chunks.append(git("diff", "--name-only", "-z", "--diff-filter=ACMR"))
            chunks.append(git("ls-files", "-z", "--others", "--exclude-standard"))
        names = "".join(chunks)
    else:
        names = git("ls-files", "-z") + git("ls-files", "-z", "--others", "--exclude-standard")

    rels = sorted({n for n in names.split("\0") if n})
    if paths:
        prefixes = tuple(p.rstrip("/") for p in paths)
        rels = [r for r in rels if r.startswith(prefixes)]
    # A path can be listed but absent (deleted in the working tree, or a rename's old
    # name). Drop those here so the filename rules below never fire on a file that is gone.
    return [p for p in (REPO_ROOT / r for r in rels) if p.is_file()]


def read_text(path: Path) -> str | None:
    """Return file text, or None if it is missing or binary."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return None
    return raw.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #
def scan_text(text: str, rel_path: str, markers: list[Marker]) -> list[Finding]:
    findings: list[Finding] = []
    for marker in markers:
        if not marker.applies_to(rel_path):
            continue
        validator = VALIDATORS[marker.validator] if marker.validator else None
        for m in marker.compiled().finditer(text):
            value = m.group(1) if m.groups() else m.group(0)
            if validator and not validator(value):
                continue
            start = max(0, m.start() - 70)
            context = re.sub(r"\s+", " ", text[start : m.end() + 70]).strip()
            findings.append(
                Finding(
                    marker=marker,
                    path=rel_path,
                    line=text.count("\n", 0, m.start()) + 1,
                    matched=value,
                    context=context if marker.layer == "licensing" else "",
                )
            )
    return findings


def scan_filenames(files: list[Path]) -> list[Finding]:
    """Some files are blocking on their name alone - no content match required."""
    marker = Marker(
        key="cred:secret-filename",
        label="secret-bearing filename",
        pattern="",
        severity="blocking",
        layer="cred",
    )
    findings = []
    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if any(fnmatch.fnmatch(rel, g) for g in SECRET_FILENAME_EXCEPTIONS):
            continue
        if any(fnmatch.fnmatch(rel, g) for g in SECRET_FILENAME_GLOBS):
            findings.append(Finding(marker, rel, 1, rel, ""))
    return findings


def scan_history_filenames() -> list[Finding]:
    """Was a secret-bearing file EVER committed, on any branch?

    Filename-only and therefore near-instant. Content history is --deep-history.
    """
    marker = Marker(
        key="cred:secret-filename-in-history",
        label="secret-bearing filename in git history",
        pattern="",
        severity="blocking",
        layer="cred",
    )
    names = git("log", "--all", "--diff-filter=A", "--name-only", "--format=")
    findings = []
    for rel in sorted({n.strip() for n in names.splitlines() if n.strip()}):
        if any(fnmatch.fnmatch(rel, g) for g in SECRET_FILENAME_EXCEPTIONS):
            continue
        if any(fnmatch.fnmatch(rel, g) for g in SECRET_FILENAME_GLOBS):
            findings.append(Finding(marker, rel, 1, rel, ""))
    return findings


def scan_deep_history() -> list[Finding]:
    """Scan every blob ever committed for credentials. Slow; opt-in.

    Credentials only - the licensing and PII layers describe what is *currently* publishable
    and would report the same corpus text once per revision.
    """
    findings: list[Finding] = []
    listing = git("rev-list", "--objects", "--all")
    blobs = []
    for line in listing.splitlines():
        sha, _, name = line.partition(" ")
        if name and len(sha) == 40:
            blobs.append((sha, name))
    for sha, name in blobs:
        content = subprocess.run(
            ["git", "cat-file", "-p", sha],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
        ).stdout
        if b"\x00" in content[:8192]:
            continue
        text = content.decode("utf-8", errors="replace")
        for f in scan_text(text, name, CREDENTIAL_MARKERS):
            f.path = f"{sha[:10]}:{name}"
            findings.append(f)
    return findings


def apply_allowlist(findings: list[Finding], baseline: Baseline) -> None:
    for f in findings:
        for allow in baseline.allows:
            if allow.covers(f):
                f.allowlisted_by = allow.reason
                break


# --------------------------------------------------------------------------- #
# Baseline
# --------------------------------------------------------------------------- #
def load_baseline(path: Path) -> Baseline:
    if not path.exists():
        return Baseline()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    allows = []
    for entry in data.get("allow", []):
        reason = entry.get("reason", "").strip()
        if not reason:
            raise SystemExit(f"{path.name}: every [[allow]] entry needs a reason. Got: {entry}")
        allows.append(
            Allow(
                marker=entry.get("marker", "*"),
                match=entry.get("match"),
                match_regex=entry.get("match_regex"),
                path=entry.get("path"),
                reason=reason,
            )
        )
    return Baseline(allows=allows, counts=dict(data.get("baseline", {})))


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
def self_test() -> int:
    failures = []
    for marker in ALL_MARKERS:
        if marker.canary is None:
            continue
        # Canaries bypass path scoping - they test the pattern, not where it applies.
        probe = Marker(**{**marker.__dict__, "path_globs": ()})
        if not scan_text(marker.canary, "canary.txt", [probe]):
            failures.append(f"DEAD DETECTOR  {marker.key}: canary did not match")

    for text, marker_key in ANTI_CANARIES:
        marker = next(m for m in ALL_MARKERS if m.key == marker_key)
        probe = Marker(**{**marker.__dict__, "path_globs": ()})
        if scan_text(text, "anti.txt", [probe]):
            failures.append(f"FALSE POSITIVE {marker_key}: matched {text!r}")

    covered = sum(1 for m in ALL_MARKERS if m.canary)
    print(
        f"Self-test: {covered}/{len(ALL_MARKERS)} detectors have a canary, "
        f"{len(ANTI_CANARIES)} anti-canaries checked."
    )
    for line in failures:
        print(f"  {line}", file=sys.stderr)
    if failures:
        print(f"\n{len(failures)} self-test failure(s).", file=sys.stderr)
        return 1
    print("  all detectors fire; no anti-canary matched.")
    return 0


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def scope_label(staged: bool, unstaged: bool) -> str:
    """Name the scope in the output. A scan that does not say what it covered invites
    reading "clean" as "the repo is clean" when only three files were looked at."""
    if staged and unstaged:
        return "uncommitted (staged + unstaged + untracked)"
    if staged:
        return "staged"
    if unstaged:
        return "unstaged + untracked"
    return "public-bound (tracked + untracked)"


def report(
    findings: list[Finding], baseline: Baseline, scanned: int, scope: str, comparable: bool
) -> int:
    live = [f for f in findings if not f.allowlisted_by]
    suppressed = [f for f in findings if f.allowlisted_by]
    blocking = [f for f in live if f.marker.severity == "blocking"]
    advisory = [f for f in live if f.marker.severity == "advisory"]

    print(f"Scanned {scanned} {scope} file(s).\n")

    print("Blocking (secrets, PII, licence-restricted content):")
    by_key: dict[str, list[Finding]] = {}
    for f in blocking:
        by_key.setdefault(f.marker.key, []).append(f)
    for marker in ALL_MARKERS:
        if marker.severity != "blocking":
            continue
        hits = by_key.get(marker.key, [])
        if not hits:
            print(f"  ok     {marker.label}: none")
            continue
        print(f"  ERROR  {marker.label}: {len(hits)} hit(s) — DO NOT COMMIT", file=sys.stderr)
        for f in hits[:5]:
            print(f"           {f.path}:{f.line}  {redact(f.marker, f.matched)}", file=sys.stderr)
    for f in by_key.get("cred:secret-filename", []) + by_key.get(
        "cred:secret-filename-in-history", []
    ):
        print(f"  ERROR  {f.marker.label}: {f.path}", file=sys.stderr)

    drift = []
    counts: dict[str, int] = {}
    for f in advisory:
        counts[f.marker.key] = counts.get(f.marker.key, 0) + 1

    if comparable:
        print("\nAdvisory (expected in this corpus — investigate a jump, not a nonzero):")
    else:
        print(
            "\nAdvisory (listed only — the baseline counts the whole repo, so it means "
            "nothing against a subset):"
        )
    for marker in ALL_MARKERS:
        if marker.severity != "advisory":
            continue
        actual = counts.get(marker.key, 0)
        expected = baseline.counts.get(marker.key)
        if not comparable:
            state = "not compared"
        elif expected is None:
            state = "no baseline recorded"
        elif actual > expected:
            state = f"ABOVE BASELINE {expected} — review, then update the baseline"
            drift.append((marker.key, expected, actual))
        elif actual < expected:
            state = f"below baseline {expected} — corpus shrank; lower the baseline"
        else:
            state = "at baseline"
        flagged = comparable and actual > (expected or 0)
        print(f"  {'warn ' if flagged else 'info '} {marker.label}: {actual} ({state})")
        # In a scoped scan every advisory hit is new work, so show it even without a
        # baseline to compare against — that is the whole point of scanning before staging.
        if flagged or (not comparable and actual):
            for f in [x for x in advisory if x.marker.key == marker.key][:3]:
                detail = f.context or redact(f.marker, f.matched)
                print(f"           {f.path}:{f.line}  …{detail[:150]}…")

    if suppressed:
        reasons: dict[str, int] = {}
        for f in suppressed:
            reasons[f.allowlisted_by or ""] = reasons.get(f.allowlisted_by or "", 0) + 1
        print(f"\nAllowlisted ({len(suppressed)} suppressed — visible, not silent):")
        for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>4}x  {reason}")

    if blocking:
        print(
            f"\n{len(blocking)} blocking hit(s). DO NOT COMMIT until resolved. "
            "If a real credential leaked, rotate it before deleting it — it is already "
            "in git history.",
            file=sys.stderr,
        )
        return 1
    if drift:
        print(
            f"\n{len(drift)} advisory count(s) above baseline. Review, then update "
            f"{BASELINE_PATH.name}."
        )
        return 2
    print("\nClean: no blocking hits, advisory counts at or below baseline.")
    return 0


def as_json(
    findings: list[Finding], baseline: Baseline, scanned: int, scope: str, comparable: bool
) -> int:
    live = [f for f in findings if not f.allowlisted_by]
    blocking = [f for f in live if f.marker.severity == "blocking"]
    advisory = [f for f in live if f.marker.severity == "advisory"]
    counts: dict[str, int] = {}
    for f in advisory:
        counts[f.marker.key] = counts.get(f.marker.key, 0) + 1
    drift = (
        {
            k: {"expected": baseline.counts.get(k), "actual": v}
            for k, v in counts.items()
            if v > baseline.counts.get(k, 0)
        }
        if comparable
        else {}
    )
    code = 1 if blocking else (2 if drift else 0)
    print(
        json.dumps(
            {
                "scanned_files": scanned,
                "scope": scope,
                "exit_code": code,
                "blocking": [
                    {
                        "marker": f.marker.key,
                        "label": f.marker.label,
                        "path": f.path,
                        "line": f.line,
                        "match": redact(f.marker, f.matched),
                    }
                    for f in blocking
                ],
                "advisory_counts": counts,
                "baseline": baseline.counts,
                "baseline_comparable": comparable,
                "drift": drift,
                "allowlisted": len([f for f in findings if f.allowlisted_by]),
            },
            indent=2,
        )
    )
    return code


# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(
        description="Scan public-bound files for secrets, PII/PHI, and licence-restricted "
        "content (see CLAUDE.md's public-repo data guardrail).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--staged", action="store_true", help="only files staged for commit")
    p.add_argument(
        "--unstaged",
        action="store_true",
        help="only unstaged work: modified-but-not-staged files plus untracked files. "
        "Combine with --staged to scan everything uncommitted.",
    )
    p.add_argument("--paths", nargs="+", metavar="P", help="restrict the scan to these paths")
    p.add_argument(
        "--deep-history", action="store_true", help="also scan every blob ever committed (slow)"
    )
    p.add_argument(
        "--self-test", action="store_true", help="prove every detector still fires, then exit"
    )
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument(
        "--baseline",
        type=Path,
        default=BASELINE_PATH,
        help=f"allowlist / baseline file (default {BASELINE_PATH.name})",
    )
    args = p.parse_args()

    if args.self_test:
        return self_test()

    baseline = load_baseline(args.baseline)
    files = target_files(args.staged, args.unstaged, args.paths)

    findings: list[Finding] = []
    scanned = 0
    for path in files:
        text = read_text(path)
        if text is None:
            continue
        scanned += 1
        findings.extend(scan_text(text, path.relative_to(REPO_ROOT).as_posix(), ALL_MARKERS))

    findings.extend(scan_filenames(files))
    findings.extend(scan_history_filenames())
    if args.deep_history:
        if not args.json:
            print("Scanning every blob ever committed — this takes a while…\n")
        findings.extend(scan_deep_history())

    apply_allowlist(findings, baseline)
    scope = scope_label(args.staged, args.unstaged)
    # The baseline counts the whole repo. Comparing it against a subset would report every
    # advisory marker as "below baseline" and advise lowering it — actively wrong.
    comparable = not (args.staged or args.unstaged or args.paths)
    if args.json:
        return as_json(findings, baseline, scanned, scope, comparable)
    return report(findings, baseline, scanned, scope, comparable)


if __name__ == "__main__":
    sys.exit(main())
