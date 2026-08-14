# Chunking

How `data/processed/<source>/corpus.jsonl` becomes `chunks.jsonl`, and why the parameters are the
numbers they are. Written before Phase 1a so the retrieval work inherits the reasoning instead of
re-deriving it — same purpose as [lancedb.md](lancedb.md).

Code: `src/health_coverage_navigator/chunking/`. Run it with `make chunk`.

Scope is the **three text corpora**. `exchange_puf` and `part_d_spuf` are lossless columnar
mirrors with no `text` field; they are never chunked and never embedded. `CorpusName` naming only
the three is the type-level version of that rule.

---

## 1. What makes this corpus awkward

The three ingestion scripts each strip blank lines, with the result that **`\n\n` does not occur in
any of the 2,056 documents**. Every off-the-shelf recursive splitter leads with a paragraph
separator; here that rung is dead on arrival.

Worse, a bare `\n` means something different in each corpus:

| | what a newline is | median doc | p90 | max |
|---|---|---|---|---|
| `healthcare_gov` | often an *artifact* — HTML inline tags became line breaks, so a line can begin with a bare comma | 904 | 3,435 | 39,604 |
| `medicare_ncd` | a real line break inside a policy section; sections are marked with `## ` | 1,537 | 6,667 | 19,720 |
| `medicare_pubs` | a real line break, but paragraphs were already unwrapped into single long lines | 1,764 | 2,706 | 3,820 |

The healthcare_gov case is the sharp one. `html_to_text()` calls `get_text(separator="\n")`, so
`<em>` and `<a>` boundaries became newlines:

```
If you're interested in claiming exemptions for the
2016
tax year
only
, select the links below.
```

A splitter that treated `\n` as a boundary would cut that sentence into five pieces. A splitter
that ignored newlines would have nothing to work with on the other two corpora.

## 2. The boundary ladder

Boundaries are tried in descending order of trustworthiness. The first rung that offers a cut
inside the acceptable window wins, and the **greatest** such cut is taken so chunks pack full.

| # | rung | why |
|---|---|---|
| 0 | `\n\n` | Paragraph. Fires **zero** times today; kept so the splitter is still correct for a future source that has paragraphs. |
| 1 | `(?<=[.!?:])\n(?=[A-Z0-9"“•(])` | **The workhorse.** A newline that follows terminal punctuation *and* precedes a capital is not an inline-tag artifact. On healthcare_gov this yields 8,093 segments, median 119 chars, p99 699, max 1,984 — only 0.19% exceed the budget. |
| 2 | `\n` | Bare newline. Bullets (601 medicare_pubs pages have `•` lines) and heading-ish fragments. |
| 3 | `(?<=[.!?])\s+(?=[A-Z])` | Sentence end *inside* a line. Required for medicare_pubs, where reflowing produced single lines with p90 = 233 chars and no interior newline at all. |
| 4 | `␣` | Word boundary. |
| 5 | hard cut | Guarantees forward progress. |

Sections are addressed by offset rather than sliced out, so rung 1 and 3's lookbehinds can still
see the character *before* a section start. Slicing would make every section look like the start
of a document and move the first cut.

## 3. Parameters, and where each number comes from

These live in [`config.yaml`](../config.yaml) under `chunking:`, validated by `ChunkParams` in
`config.py`. They carry no Python defaults — the committed file is the only source, so a value here
and a value in code cannot drift. Changing any of them invalidates `params_sha256` and every
`snapshot_id` in the committed manifests; run `make chunk` in the same change.

| param | value | derivation |
|---|---|---|
| `max_chars` | 1,200 | ≈300 tokens. The NCD median section body (832) and the healthcare_gov median doc (904) both fit, so the typical retrieval unit survives whole and only the tail splits. |
| `overlap_chars` | 320 | **Derived from the gold set.** The longest `expected_snippet` is 279 chars (median 114). Overlap above that turns single-chunk containment from an observation into a guarantee — see §4. |
| `min_tail_chars` | 300 | A trailing sliver is the tail of the sentence above it, not a retrievable unit; it merges into its predecessor (never across a heading). |
| `min_fill_chars` | 480 | 40% of budget. A rung is only used if it cuts past this, else fall through — otherwise one early sentence break emits a 60-char chunk. |
| `min_doc_chars` | 40 | Whitespace-normalized floor for chunking a document at all. Drops 29 medicare_pubs pages whose text is `"Notes"` or a cover fragment, and **nothing** in the other two corpora (the shortest surviving healthcare_gov doc is 47 chars). |

Sizes are in **characters**, and there is deliberately no tokenizer dependency. Phase 1a is
lexical, where tokens are irrelevant. Phase 1b's embedding ceiling (~32,000 chars) is 27× the
budget, so a real token count would only ever buy a cost estimate — and one baked into a committed
artifact is invalidated by any model change. `Chunk.approx_tokens` uses ≈4 chars/token, which is
within ~10%.

## 4. The overlap guarantee

The next chunk starts at **the greatest boundary at or before `end - overlap_chars`** — never
after it. Backward-only snapping means realized overlap is always ≥ `overlap_chars`, so any
contiguous run of ≤ 320 characters within one section is wholly inside at least one chunk.

That is what makes `test_gold_snippets_survive_chunking` a guarantee rather than luck: a gold
snippet split across two chunks would make its question unanswerable by any single retrieval, and
the eval score would drop for a reason no one could see. "Snap to the nearest boundary" is the
obvious-looking version of this code and it silently breaks the property.

## 5. Per-source strategy

**`healthcare_gov` — pure ladder, no special cases.** HTML stripping destroyed the heading
structure (`<h2>` collapsed to a bare line, indistinguishable from a sentence fragment), so
`heading` is `None` for this entire corpus rather than invented. 446 of 747 docs are under budget
and pass through as a single chunk; the 39,604-char `/privacy` notice becomes 93. That outlier gets
no special handling — it is a 4× outlier, not a different kind of document.

**`medicare_pubs` — one section per page, then the ladder.** Pages are *not* left whole: 197 of
964 exceed 2,400 chars, and one index and one gold set span all three corpora, so letting one
corpus average 2× another's chunk size would make the Phase 1a-vs-1b comparison partly a
measurement of chunk size. Nothing is lost by splitting — `page` is metadata, so the citation stays
*Medicare & You 2026, p. 31* either way.

The printed running header is **kept in `text` and promoted to `heading`**. Stripping it would
break the verbatim-slice contract for the sake of ~40 characters. A running header is detected as
the page's first line when it is ≤ 80 chars *and* is also the first line of ≥ 2 pages in the same
publication — deterministic, computable from committed data, and it lifts heading coverage from
192 pages (`section` alone, some of them degenerate like `"Section 5:"`) to **641 of 935**.
Promotion is what lets chunks 2..n of a page, whose slice no longer contains the banner, still know
what section they are in.

**`medicare_ncd` — split on `## `, and small sections are *not* merged.** The split is exact: all
345 texts start with `## ` and none contain a stray blank line. The heading line itself is excluded
from the chunk body — it is metadata, and `## ` markup should not appear inside a passage shown to
a user — and reappears as `Chunk.heading` and in the citation label.

505 of 2,256 NCD chunks are under 200 chars, overwhelmingly `Benefit Category` (339) and
`Cross-reference` (106). Merging them was considered and **rejected** on four grounds:

1. `download_medicare_ncd.py`'s `build_text()` writes those headings *specifically* so the chunker
   can keep the section name as a citation label. Merging discards the label the data was shaped to
   provide.
2. `Benefit Category` is always first, so it could only merge **forward** — after which the heading
   either lies about 5 KB of clinical indications or needs a compound label. Both are worse than a
   short chunk.
3. They are semantically atomic. A benefit category is a controlled-vocabulary phrase from statute;
   a cross-reference is a pointer. Diluting `"Physicians' Services"` with 5 KB of coverage criteria
   makes it *harder* to retrieve.
4. Contextualization (§6) already addresses the retrieval concern: a 27-char body is indexed as
   ~90 chars carrying the NCD number, its title, and the section name. It is never indexed bare.

> **Handed forward to Phase 1a:** BM25 length normalization may over-favour these short chunks.
> That is a scoring-parameter question (`b`, `k1`) to be measured against the gold set — not a
> chunking bug, and not a reason to reopen the merge decision.

## 6. Contextualization: derived, never stored

`Chunk.text` is a verbatim slice of its parent — `parent["text"][char_start:char_end]`, always.
The retrieval and citation forms are **computed properties**:

| property | example |
|---|---|
| `context_header` | `NCD 30.3 > Acupuncture > Indications and Limitations of Coverage` |
| `retrieval_text` | `context_header` + `\n` + `text` |
| `citation_label` | `Medicare & You 2026, p. 31` |

Baking the header into stored text would end the verbatim-slice contract, which is what lets tests
prove no content was lost and what Phase 4's per-claim highlighting will need. It would also make a
citation-format tweak a full re-chunk. Keeping the pieces as bare fields instead would push header
construction into Phase 1a, Phase 1b and the eval runner separately, where they would drift.

- **Phase 1a (BM25):** index `retrieval_text`; return and cite `text` with `citation_label`. The
  header is legitimate lexical signal — "deductible" in a glossary chunk's title is a real hit.
- **Phase 1b (LanceDB):** embed `retrieval_text`, **store `text`**. The `text` key
  [lancedb.md](lancedb.md)'s loader expects keeps its exact current meaning.

## 7. Output, and why the chunks are not committed

Per source: `chunks.jsonl` (**git-ignored**) and `chunks_meta.json` (**committed**).

| source | docs | chunks | median | p90 | max |
|---|---|---|---|---|---|
| healthcare_gov | 747 | 2,009 | 1,007 | 1,175 | 1,200 |
| medicare_ncd | 345 | 2,256 | 809 | 1,153 | 1,200 |
| medicare_pubs | 964 (29 skipped) | 2,457 | 1,023 | 1,175 | 1,200 |
| **total** | **2,056** | **6,722** | | | |

Every other git-ignore exception in this repo trades a blob for a checksum manifest because the
blob is *large*. This one is different: ~7 MB is not the problem. Chunks are a pure offline
function of committed inputs, rebuilt in about a second, and they churn end-to-end on every
parameter tweak — a handful of tuning commits would outweigh the entire repo history, since git
deltifies reordered JSONL badly.

The decisive argument is the guardrail. `scan_sensitive.py` scopes its licensing markers to
`data/processed/*`, so committed chunks would be scanned — and chunk text is a *verbatim subset* of
corpus text, adding zero new licensing surface while double-counting every narrative CPT/HCPCS
mention (triple, inside overlap regions). The advisory baselines would stop being a number about
the corpus and become a number about chunking parameters.

What replaces the committed artifact is `chunks_meta.json`, which records the input sha256, the
parameters and their hash, the output sha256, and a `snapshot_id`. `make chunk-check` and
`tests/test_chunks.py::test_chunks_match_committed_manifest` both fail if the tree's chunks would
differ — so reproducibility is *checked*, not asserted. The manifest carries counts and hashes only
and deliberately **no prose**: explanatory text in a file under `data/` is what tripped the
scanner's blocking `CDT` marker during the `part_d_spuf` work.

`snapshot_id` — `sha256(input_sha + params_sha + chunker_version)[:12]` — is the pin. An eval run
records the snapshots it was measured against; Phase 1b tags a LanceDB dataset version with the
same string, which is the eval-reproducibility property [lancedb.md](lancedb.md) chose LanceDB for.
It excludes the output hash on purpose, so it can be computed *before* chunking and verified after.

`CHUNKER_VERSION` is bumped by hand for behaviour changes the parameters do not capture — a new
ladder rung, a change to heading detection. Bumping invalidates every `snapshot_id`, which is the
point.

## 8. Determinism

`build_chunks()` is a pure function of the corpus file and the parameters. Four rules keep it that
way, and breaking any of them breaks the manifest:

- iterate the corpus in file order; never let set/dict iteration order reach the output,
- no clock, no randomness, no filesystem ordering inside the build,
- every regex precompiled at module scope,
- every tunable flows from one `ChunkParams` (loaded from `config.yaml`), never read ad hoc.

`chunks.jsonl` is written `.part`-then-renamed (the `part_d_spuf` lesson: a crash must not leave a
truncated file a later run's manifest vouches for), and `chunks_meta.json` is rewritten only when
its content *excluding the timestamp* changes — a re-run that changes nothing must not dirty the
tree, or it becomes a step nobody re-runs.
