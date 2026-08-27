# Negative findings that are still not citable

**Status: closed for the live lane on 2026-08-27. One half remains open — §6, the mirror.**
Written 2026-08-25 during a walkthrough of the Phase 3 branch, so that the residue of a defect
family was recorded rather than rediscovered; kept afterwards because *how* a family gets closed is
worth more than the list of instances, and §6 is still live.

[structured-api-tools.md](structured-api-tools.md) §14a-bis and §18c describe *Family 1* — "a
finding with nothing to cite" — and list the four instances found and fixed while building Phase 3.
This document recorded what those four fixes did **not** cover: the family was closed case by case,
never turned into an invariant, so the same shape survived in five more paths in
`agent/live_tools.py`. All five are now closed, structurally.

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

## 2. What the four original fixes did

Each fixed case makes the **lookup itself** the citable record, with the id assigned in the client
so the model can read it off the result it was handed:

| Finding | Client assigns | Row builder |
|---|---|---|
| openFDA holds no recall | `row_id=f"fda#r{seq}.0"` | `_recall_rows` |
| NPPES holds no such NPI | `row_id=f"npi#{npi}.0"` | `_provider_rows` |
| NPPES rejects a malformed NPI | `row_id=f"npi#{npi}.x"` | `_provider_rows` |
| CMS does not serve a state | `row_id=f"mkt#s{seq}.{state}"` | `_plan_rows` |

The models encode the discipline too: a result-level `row_id` is populated **only** on the negative
branch and left blank otherwise, so the model is never shown an id that would be refused if it cited
it (that is Family 2's rule, §18c).

## 3. The five paths that had the same hole

All five now emit a row. Each was the same shape: guard on `unavailable`, return rows for whatever
came back, and fall through to `[]` on the branch where the finding *was* the absence.

| Path | The finding that had nothing to cite | Now assigned |
|---|---|---|
| `drug_label`, no matching label | the FDA holds no label under that name — after **two** searches, brand then generic | `fda#l{seq}.0` |
| `drug_label`, no such section | the label exists and does not carry that section (the ordinary OTC case) | `fda#l{seq}.0` |
| `find_plans`, empty result | the search ran and matched nothing — the fourth of `PlanMatches`' four states | `mkt#p{seq}.0` |
| `find_drug`, no match | the Marketplace does not recognise that name — usually a misspelling worth naming | `mkt#d{seq}.0` |
| `check_drug_coverage`, empty envelope | nothing came back at all — the residue `DataNotProvided` does not cover | `mkt#c{seq}.0` |

Each id is set in the client, on the negative branch only, and is visible on the result the model
was handed.

**`find_drug` also gained per-match ids** (`mkt#d{seq}.{n}` on `DrugMatch`), which the original
write-up did not ask for. Closing only its negative branch would have left the invariant needing an
exemption for its *positive* one — and that exemption would have been hiding the same defect: the
tool's docstring tells the model to **say which strength it checked**, and "I checked the 20 mg
tablet" is a claim about what the lookup returned. A resolution step is still a step whose output
gets quoted.

## 4. Why it had not bitten as hard as the four fixed cases

Blast radius depends on whether the negative finding is the **whole** answer or one clause of a
compound one. *"Has atorvastatin been recalled"* has nothing else to cite, so the recall case failed
loudly. A drug-label miss usually sits beside reference-corpus citations that satisfy the validator,
so the answer is served — it is just missing the provenance for the one clause that came from the
FDA. That is quieter and arguably worse: a served answer with a silently unevidenced claim, rather
than a visible retry.

That is also why none of the five was caught by a passing eval, and why the fix is a test rather
than five fixes.

## 5. How it was fixed

Option B of the two this document originally offered — the structural one — extended to all five
paths rather than the three named, because the invariant that makes it stick does not admit
exemptions.

**[`_search_row`](../src/health_coverage_navigator/agent/live_tools.py)** is the single place a
reached-but-empty lookup becomes a row. Its docstring carries the rule and both boundaries, so the
next person to write a builder reads them where they are working.

**`_ROW_BUILDERS` and `rows_for(result)`** replace six direct builder calls with a registry keyed on
result type. That is what makes "every live tool" enumerable: a result shape with no builder cannot
record a row at all, and now raises instead of quietly recording nothing.

**Three tests, in `tests/test_live_agent.py`:**

| Test | What it pins |
|---|---|
| `test_a_reached_lookup_is_always_citable` | all nine negative shapes produce a row, with an id the model can read off the result |
| `test_an_outage_stays_uncitable` | the inverse — `unavailable` produces nothing, for every result type |
| `test_every_live_tool_has_a_row_builder` | walks `LIVE_TOOLS`, reads each return annotation, demands a registered builder |

The third is the one that outlives this document: it means a **new** tool inherits the invariant
instead of having to remember it. The client half is pinned separately in `tests/test_live_clients.py`,
because the agent-level test builds results by hand and would pass even if no client ever assigned
the id.

### What must not change

- **`unavailable` still emits nothing.** An outage is not a finding — nothing was looked up, so
  there is nothing to cite. Every builder's first guard stays exactly as it is. Emitting a row for
  an outage would let the model cite the fact that it failed, which is the inverse mistake and a
  worse one. `test_an_outage_stays_uncitable` is the guard.
- **Ids stay source-namespaced** (`fda#`, `npi#`, `mkt#`) and readable off the result the model was
  handed (§14a-bis, §18c Family 2).
- **Cells stay short values, not prose** — a cell is something to copy, not something to read
  (§18c). The negative rows carry a count (`labels_found: "0"`) and the query terms the model
  supplied, and nothing else.

## 6. The same hole in the mirror half — still open

§14a-bis already records it: [relational-tool.md](relational-tool.md) §6 says "empty is an answer"
without saying how such an answer gets cited, so a `query_structured` returning zero rows is in the
same bind. It was left alone deliberately, and remains so: it is Phase 1-c code whose change should
be measured against the mirror slice. `_search_row` is now the natural place to close it — but the
measurement caveat still applies, and it should be a separate change with its own eval run.

## 7. Related

- [structured-api-tools.md](structured-api-tools.md) §14a-bis — why a negative finding is citable
  and not a loophole
- [structured-api-tools.md](structured-api-tools.md) §18c — the two defect families and the rules
  from them
- [agent.md](agent.md) — the grounding guardrail this interacts with
- [relational-tool.md](relational-tool.md) §6 — the mirror half's version of the same gap
