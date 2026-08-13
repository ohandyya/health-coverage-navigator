"""Per-source planning: where a document's sections are, and what metadata its chunks carry.

The three corpora share a field vocabulary but emphatically do not chunk alike, so this is the one
module that is allowed to know which source it is looking at. Everything downstream
(`splitter.py`, `models.py`, `pipeline.py`) is source-agnostic.

  * **healthcare_gov** — one section, no heading. HTML stripping destroyed the heading structure
    (`<h2>` collapsed to a bare line, indistinguishable from a sentence fragment), so claiming a
    heading here would mean inventing one. `heading` is `None` for this entire corpus, which is
    honest; the boundary ladder recovers the only structure that survived, which is sentences.

  * **medicare_ncd** — split on the `## ` headings that `download_medicare_ncd.py` wrote into
    `text` for exactly this purpose, keeping the heading as the citation label. Section bodies are
    wildly unequal (`Benefit Category` median 27 chars, `Indications and Limitations of Coverage`
    max 16,381) and **small sections are deliberately not merged** — see docs/chunking.md.

  * **medicare_pubs** — one section per page, with the page's printed running header promoted to
    `heading`. The header stays in `text` (stripping it would break the verbatim-slice contract
    for the sake of ~40 characters); promoting it just means chunks 2..n of a page, whose slice no
    longer contains the banner, still know what section they are in.
"""

import collections
import re
from typing import NamedTuple

from health_coverage_navigator.corpus import CorpusName


#: A region of a document's `text` that chunks may not span across, plus the label its chunks
#: inherit. Offsets are into the parent document's `text`.
class Section(NamedTuple):
    start: int
    end: int
    heading: str | None


#: Corpus-wide facts a per-document plan needs. Only medicare_pubs uses one (its running-header
#: detection is inherently cross-page); the others get an empty mapping.
DocContext = dict[str, object]

_NCD_HEADING = re.compile(r"(?:\A|\n\n)## ([^\n]+)\n")

#: A printed page banner is short. Anything longer is body text that happens to start the page.
_MAX_RUNNING_HEADER_CHARS = 80

#: A banner appears on every page of its section, so it shows up at least twice. A first line
#: unique within its publication is just that page's opening sentence.
_MIN_RUNNING_HEADER_PAGES = 2


def build_context(source: CorpusName, docs: list[dict]) -> DocContext:
    """Pre-compute whatever a source's per-document planning needs from the corpus as a whole."""
    if source != "medicare_pubs":
        return {}

    first_lines: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for doc in docs:
        line = _first_line(doc["text"])
        if line and len(line) <= _MAX_RUNNING_HEADER_CHARS:
            first_lines[doc["pub_id"]][line] += 1
    return {
        "running_headers": {
            pub_id: {line for line, n in counts.items() if n >= _MIN_RUNNING_HEADER_PAGES}
            for pub_id, counts in first_lines.items()
        }
    }


def plan_sections(source: CorpusName, doc: dict, ctx: DocContext) -> list[Section]:
    """The regions of `doc["text"]` that chunks are built within, in document order."""
    text = doc["text"]
    if source == "medicare_ncd":
        return _ncd_sections(text)
    if source == "medicare_pubs":
        return [Section(0, len(text), _pubs_heading(doc, ctx))]
    return [Section(0, len(text), None)]


def doc_metadata(source: CorpusName, doc: dict) -> dict:
    """The per-source `Chunk` fields for every chunk of this document."""
    if source == "medicare_ncd":
        return {
            "section_number": doc["section_number"],
            "effective_date": doc["effective_date"],
        }
    if source == "medicare_pubs":
        return {"page": doc["page"], "plan_year": doc["plan_year"]}
    return {}


def _ncd_sections(text: str) -> list[Section]:
    """Split on `\\n\\n## `, excluding the heading line itself from the chunkable body.

    The heading is metadata, not content: leaving `## ` markup inside a cited passage would put
    generated syntax in front of a user. It reappears as `Chunk.heading` and in the citation
    label.
    """
    matches = list(_NCD_HEADING.finditer(text))
    if not matches:
        return [Section(0, len(text), None)]

    sections: list[Section] = []
    for i, m in enumerate(matches):
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        if body_end > body_start:
            sections.append(Section(body_start, body_end, m.group(1).strip()))
    return sections


def _pubs_heading(doc: dict, ctx: DocContext) -> str | None:
    """The page's printed banner if it has one, else the bookmark breadcrumb's leaf."""
    headers = ctx.get("running_headers")
    if isinstance(headers, dict):
        line = _first_line(doc["text"])
        if line and line in headers.get(doc["pub_id"], set()):
            return line

    section = doc.get("section")
    if section:
        leaf = section.split(">")[-1].strip()
        return leaf or None
    return None


def _first_line(text: str) -> str:
    return text.split("\n", 1)[0].strip()
