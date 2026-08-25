# Negative findings that are still not citable

**Status: known gap, not scheduled. Nothing here is fixed.** Written 2026-08-25 during a
walkthrough of the Phase 3 branch, so that the residue of a defect family is recorded rather than
rediscovered.

[structured-api-tools.md](structured-api-tools.md) §14a-bis and §18c describe *Family 1* — "a
finding with nothing to cite" — and list the four instances that were found and fixed while
building Phase 3. This document records what those four fixes did **not** cover. The family was
closed case by case; it was never turned into an invariant, so the same shape survives in three
more places in `agent/live_tools.py`.

---

## 1. The mechanism, in one paragraph

[`runtime._validate_grounding`](../src/health_coverage_navigator/agent/runtime.py#L211-L216)
rejects a non-abstained answer that carries no citations:

```python
elif not answer.citations:
    raise ModelRetry("An answer with no citations is not allowed. ...")
```

A citation may only name a row the tools actually recorded in `deps.seen_rows`. So when a tool
establishes something true and emits **no row for it**, the model is left with two moves, and both
are wrong:

- **abstain** — false, because it did answer; and abstention is a first-class field the eval set
  scores, so this shows up as a false abstention rather than as a bug; or
- **cite something else** — measured once already: the agent had CMS's own "Georgia is not served"
  answer in hand, had nothing to point at, searched the web, and cited a worse source for a fact it
  had authoritatively retrieved.

**The rule this implies (§18c states it, and it holds generally):** if a tool can establish
something, it must emit a row for it — *including when what it established is an absence*. The
search that found nothing is the evidence that nothing is there.

## 2. What the four fixes did

Each fixed case makes the **lookup itself** the citable record, with the id assigned in the client
so the model can read it off the result it was handed:

| Finding | Client assigns | Row builder |
|---|---|---|
| openFDA holds no recall | `row_id=f"fda#r{seq}.0"` | [`_recall_rows`](../src/health_coverage_navigator/agent/live_tools.py#L788) |
| NPPES holds no such NPI | `row_id=f"npi#{npi}.0"` | [`_provider_rows`](../src/health_coverage_navigator/agent/live_tools.py#L560) |
| NPPES rejects a malformed NPI | `row_id=f"npi#{npi}.x"` | [`_provider_rows`](../src/health_coverage_navigator/agent/live_tools.py#L546) |
| CMS does not serve a state | `row_id=f"mkt#s{seq}.{state}"` | [`_plan_rows`](../src/health_coverage_navigator/agent/live_tools.py#L640) |

The models encode the discipline too: `DrugRecallResult.row_id` and `PlanMatches.row_id` are
populated **only** on the negative branch and left blank otherwise, so the model is never shown an
id that would be refused if it cited it (that is Family 2's rule, §18c).

## 3. The three paths that still have the hole

### 3a. `drug_label` — the FDA holds no label under that name

[`_label_rows`](../src/health_coverage_navigator/agent/live_tools.py#L743) returns `[]` whenever
`label_found` is false:

```python
if result.unavailable or not result.label_found:
    return []
```

`DrugLabelResult` has **no result-level `row_id` field at all**, so there is nothing to assign even
if the builder wanted to emit a row. Meanwhile `drug_label`'s own docstring instructs the model to
report this outcome:

> `label_found=False` means the FDA holds no label under that name; try the generic name, or say
> the drug was not found.

That is the same instruction `drug_recalls` carries for its empty case — the one that was fixed.

Note this path already runs **two** searches before concluding an absence (brand, then generic), so
the finding is a stronger one than a single miss, and is exactly the kind of thing worth citing.

### 3b. `drug_label` — the label exists but does not carry that section

Same builder, different branch: the list comprehension at
[live_tools.py:764](../src/health_coverage_navigator/agent/live_tools.py#L764) iterates
`result.sections`, so an empty `sections` list produces no rows. This is the documented, ordinary
case for over-the-counter labels, and again the model is told to report it:

> `label_found=True` with empty `sections` means the label exists but does not carry that section
> ... say the label does not include it, never that the drug has no warnings.

### 3c. `find_plans` — the search ran and matched nothing

[`_plan_rows`](../src/health_coverage_navigator/agent/live_tools.py#L685) falls through to a
comprehension over `result.plans`, which is empty in the fourth of the four states `PlanMatches`
documents:

> - empty `plans` with neither set — the search ran and found nothing.

The `state_not_served` sibling of this branch **is** citable; this one is not.

### Two lesser cases, noted but lower priority

- **`find_drug`** never calls `remember_rows` at all
  ([live_tools.py:201](../src/health_coverage_navigator/agent/live_tools.py#L201)), so "the
  Marketplace does not recognise that name" has no row. Lower priority because `find_drug` is a
  resolution step feeding `check_drug_coverage` rather than a final claim — but it *is* a real
  answer when the name is misspelled, and the model is told to say so.
- **`check_drug_coverage`** with an empty `coverage` list yields no rows
  ([`_coverage_rows`](../src/health_coverage_navigator/agent/live_tools.py#L607)). The
  `DataNotProvided` verdict is already citable, which covers the common shape; a wholly empty
  response is the residue.

## 4. Why this has not bitten as hard as the four fixed cases

Blast radius depends on whether the negative finding is the **whole** answer or one clause of a
compound one. *"Has atorvastatin been recalled"* has nothing else to cite, so the recall case failed
loudly. A drug-label miss usually sits beside reference-corpus citations that satisfy the validator,
so the answer is served — it is just missing the provenance for the one clause that came from the
FDA. That is quieter and arguably worse: a served answer with a silently unevidenced claim, rather
than a visible retry.

None of the three is pinned by a test, unlike the four fixed cases.

## 5. How to fix it

Two options. The second is preferred.

### Option A — three more point fixes

Mirror what was done for recalls: add a result-level `row_id` to `DrugLabelResult`, populate it in
`OpenFdaClient.drug_label` on the not-found branch, and emit a row from `_label_rows`; do the same
for the empty-section branch and for `find_plans`' empty result. Roughly:

- `live/models.py` — add `row_id: str = ""` to `DrugLabelResult`, documented the way
  `DrugRecallResult.row_id` is (cite this **only** when there is nothing else to cite).
- `live/openfda.py` — set it on the `payload is None` branch of `drug_label`, and set it when
  `sections` comes back empty.
- `agent/live_tools.py` — `_label_rows` and `_plan_rows` emit a search row on those branches, with
  short copyable cells (`drug`, `label_found: "0"`, `searched: ...`), following the
  cells-are-values rule in §18c.

Cheap, and leaves the family open for the next tool anyone adds.

### Option B — make it structural (preferred)

The recurrence is the actual defect. Every one of these builders has the same shape: guard on
`unavailable`, then return rows for whatever came back, with an ad-hoc negative branch bolted on
where someone noticed. Consider instead a single helper in `agent/live_tools.py`:

```python
def _search_row(*, row_id: str, view: str, source: str, cells: dict[str, str],
                url: str | None, title: str) -> Row:
    """The citable record for 'I looked here and found nothing'."""
```

...and a rule enforced by test rather than by memory: **for every live tool, a result with
`unavailable is None` must produce at least one row.** That is one parametrised test over the six
tools, and it is the check that would have caught all seven instances at once:

```python
# sketch, tests/test_live_agent.py
@pytest.mark.parametrize("result", [...every negative-branch result shape...])
def test_a_reached_lookup_always_leaves_something_citable(result) -> None:
    assert rows_for(result), "a finding with nothing to cite forces a false abstention"
```

It sits naturally beside
`test_live_row_cells_use_names_the_model_was_shown`, which is the equivalent structural guard for
Family 2 and was written for the same reason: *the next instance will be in whichever tool nobody
thought to re-check.*

### What must not change

- **`unavailable` still emits nothing.** An outage is not a finding — nothing was looked up, so
  there is nothing to cite. Every builder's first guard stays exactly as it is. Emitting a row for
  an outage would let the model cite the fact that it failed, which is the inverse mistake and a
  worse one.
- **Ids stay source-namespaced** (`fda#`, `npi#`, `mkt#`) and readable off the result the model was
  handed (§14a-bis, §18c Family 2).
- **Cells stay short values, not prose** — a cell is something to copy, not something to read
  (§18c).

## 6. The same hole in the mirror half

§14a-bis already records it: [relational-tool.md](relational-tool.md) §6 says "empty is an answer"
without saying how such an answer gets cited, so a `query_structured` returning zero rows is in the
same bind. It was left alone deliberately, as Phase 1-c code whose change should be measured against
the mirror slice. If Option B is taken, that helper is the natural place to close both — but the
measurement caveat still applies, and it should be a separate change.

## 7. Related

- [structured-api-tools.md](structured-api-tools.md) §14a-bis — why a negative finding is citable
  and not a loophole
- [structured-api-tools.md](structured-api-tools.md) §18c — the two defect families and the rules
  from them
- [agent.md](agent.md) — the grounding guardrail this interacts with
- [relational-tool.md](relational-tool.md) §6 — the mirror half's version of the same gap
