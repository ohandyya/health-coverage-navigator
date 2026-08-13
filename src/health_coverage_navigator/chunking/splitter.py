"""The source-agnostic boundary ladder: one text span in, a list of overlapping spans out.

The problem this ladder exists to solve is that **none of the three corpora have paragraphs**.
All three ingestion scripts strip blank lines, so `\\n\\n` never occurs in any of the 2,056
documents. Worse, in healthcare_gov a bare `\\n` is not even a sentence boundary: HTML inline tags
became line breaks mid-sentence, so a line can start with a bare comma. A splitter that trusted
newlines would cut sentences in half on one corpus while never firing at all on another.

So boundaries are tried in descending order of trustworthiness, and the first rung that offers a
cut inside the acceptable window wins:

  0. `\\n\\n`        paragraph. Fires zero times on today's corpora; kept so the splitter stays
                     correct for a source that does have paragraphs.
  1. `.?!:` + `\\n`  a newline that follows terminal punctuation and precedes a capital. This is
                     the workhorse, and the answer to "newlines are unreliable" — a *punctuated*
                     newline is not an inline-tag artifact. 8,093 segments on healthcare_gov,
                     p99 699 chars.
  2. `\\n`           bare newline. Bullets (601 medicare_pubs pages) and heading-ish fragments.
  3. `.?!` + space   sentence end inside a line. Required for medicare_pubs, whose reflowing
                     unwrapped whole paragraphs into single lines with no interior newline.
  4. space           word boundary.
  5. hard cut        guarantees forward progress.

**The snap direction is a correctness property, not a detail.** The next span starts at the
greatest boundary at or before `end - overlap_chars`, never after it. Snapping backward means the
realized overlap is always >= `overlap_chars`, so any contiguous run of <= `overlap_chars`
characters within one span is wholly contained in at least one chunk — which is what makes a gold
`expected_snippet` (longest: 279 chars) guaranteed findable in a single chunk rather than
accidentally findable. "Snap to the nearest boundary" is the obvious-looking version of this code
and it silently breaks that guarantee.
"""

import re

from health_coverage_navigator.chunking.params import ChunkParams

#: (start, end) half-open offsets into the text the splitter was handed.
Span = tuple[int, int]

# Each rung matches at a *candidate cut point*: the split happens at the match's start offset, so
# the boundary character(s) stay with the preceding chunk where they read naturally.
_LADDER: tuple[re.Pattern[str], ...] = (
    re.compile(r"\n\n"),
    re.compile(r"(?<=[.!?:])\n(?=[A-Z0-9\"“•(])"),
    re.compile(r"\n"),
    re.compile(r"(?<=[.!?])\s+(?=[A-Z])"),
    re.compile(r" "),
)


def _cut_points(text: str, pattern: re.Pattern[str], lo: int, hi: int) -> list[int]:
    """Offsets in `[lo, hi]` where `pattern` says the text may be cut."""
    return [m.start() for m in pattern.finditer(text, lo, hi) if lo <= m.start() <= hi]


def _best_cut(text: str, start: int, stop: int, params: ChunkParams) -> int:
    """The end offset for a chunk starting at `start`, packing the budget as full as possible."""
    hard_end = min(start + params.max_chars, stop)
    if hard_end >= stop:
        return stop

    floor = start + params.min_fill_chars
    for pattern in _LADDER:
        cuts = _cut_points(text, pattern, floor, hard_end)
        if cuts:
            return max(cuts)
    return hard_end


def _next_start(text: str, end: int, params: ChunkParams, floor: int, stop: int) -> int:
    """Where the following chunk begins: the last boundary at or before `end - overlap_chars`.

    Backward-only, per the module docstring. `floor` keeps the walk from stepping behind the
    current chunk's own start, which would not terminate.
    """
    target = max(floor, end - params.overlap_chars)
    if target <= floor:
        return floor
    for pattern in _LADDER:
        cuts = _cut_points(text, pattern, floor, target)
        if cuts:
            # A boundary match starts *at* the separator; the text resumes after it.
            resumed = _skip_leading(text, max(cuts), stop)
            if floor < resumed <= target:
                return resumed
    return target


def split_span(text: str, params: ChunkParams, lo: int = 0, hi: int | None = None) -> list[Span]:
    """Split `text[lo:hi]` into overlapping spans, returned in `text`'s own coordinates.

    The region is addressed by offsets rather than sliced out because the ladder's lookbehind
    assertions must still see the character *before* `lo`. Slicing would make every section start
    look like the start of a document and move the first cut.

    A region at or under budget yields exactly one span. Leading and trailing whitespace is
    excluded, so every returned span slices to already-stripped text.
    """
    end_limit = len(text) if hi is None else min(hi, len(text))
    start = _skip_leading(text, lo, end_limit)
    stop = _trim_end(text, start, end_limit)
    if stop <= start:
        return []
    if stop - start <= params.max_chars:
        return [(start, stop)]

    spans: list[Span] = []
    while start < stop:
        end = _trim_end(text, start, _best_cut(text, start, stop, params))
        spans.append((start, end))
        if end >= stop:
            break
        # Forward progress is guaranteed structurally, but enforce it rather than trusting the
        # ladder: a pathological input that stalled here would hang the whole ingestion.
        start = max(_next_start(text, end, params, start + 1, stop), start + 1)

    return _merge_short_tail(spans, params)


def _skip_leading(text: str, pos: int, limit: int) -> int:
    while pos < limit and text[pos].isspace():
        pos += 1
    return pos


def _trim_end(text: str, start: int, end: int) -> int:
    """Pull `end` back off trailing whitespace so the slice needs no post-hoc stripping."""
    while end > start and text[end - 1].isspace():
        end -= 1
    return end


def _merge_short_tail(spans: list[Span], params: ChunkParams) -> list[Span]:
    """Fold a runt final span into its predecessor when the result still fits.

    A 40-character trailing fragment is not a retrievable unit; it is the tail of the sentence in
    the chunk above it. The ceiling is `max_chars + overlap_chars` because the merged span
    subsumes an overlap region that was already counted once.
    """
    if len(spans) < 2:
        return spans
    last_start, last_end = spans[-1]
    prev_start, _ = spans[-2]
    if last_end - last_start >= params.min_tail_chars:
        return spans
    if last_end - prev_start > params.max_chars + params.overlap_chars:
        return spans
    return spans[:-2] + [(prev_start, last_end)]
