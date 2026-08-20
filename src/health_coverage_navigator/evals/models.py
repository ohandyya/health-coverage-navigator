"""Pydantic schema for the Phase 0 gold evaluation set.

`SourceType` is imported from `api/models.py`, which owns the frozen API contract's vocabulary
(docs/frontend_plan.md §4.2). The arrow points eval-set → contract deliberately: the gold set
asserts what the API must eventually return, so it is a consumer of that enum rather than a peer
with its own copy. `api/models.py` imports nothing from this package and nothing from FastAPI,
which is what keeps the arrow one-way and keeps the gold-set tests free of a web framework.

`CorpusName` is imported from `corpus.py`, which owns the text-corpus vocabulary — the eval set is
a consumer of the corpora, not a peer that gets its own copy of the list.

Gold questions are anchored on corpus **doc ids**, not chunk ids, so the set survives re-chunking
(see the plan's design-decision note). Corpus-dependent correctness (do these ids exist, is the
snippet verbatim, is the target retired) is enforced by tests/test_gold_set.py, not here — this
module only enforces structural invariants that hold independent of the corpus.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from health_coverage_navigator.api.models import SourceType
from health_coverage_navigator.corpus import CorpusName

Difficulty = Literal["easy", "medium", "hard"]


class GoldQuestion(BaseModel):
    id: str
    question: str
    difficulty: Difficulty | None = None
    corpus: CorpusName | None = None
    expected_source_type: SourceType | None = None
    expected_abstain: bool = False
    expected_doc_ids: list[str] = Field(default_factory=list)
    expected_snippet: str | None = None
    expected_answer: str | None = None
    answer_key_facts: list[str] = Field(default_factory=list)
    plan_year: int | None = None
    volatile: bool = False
    notes: str = ""
    becomes_answerable_at_phase: str | None = None
    """Which phase turns an abstention into an answer — `"1c"`, `"2"`, `"3"`. A **string** since
    Phase 1-c, because the phases are not integers: `abs-02` ("is metformin on this plan's
    formulary") was labelled `3` and is in fact answered by 1-c's vendored Part D data."""

    expected_table: str | None = None
    """Structured questions only: the `<source>.<table>` the answer must come from."""

    expected_cells: list[str] = Field(default_factory=list)
    """Structured questions only: the cell values a correct answer has to cite, **verbatim** —
    including the trailing space in `'$4,500 '`. Generated from the mirror by
    `python -m health_coverage_navigator.evals.structured_gold`, never retyped: a value that has
    been tidied by hand asserts something the source does not say."""

    @property
    def is_structured(self) -> bool:
        """Whether this question is answered from a table rather than from a passage.

        Keyed off `expected_source_type` rather than a separate flag: the lane a question belongs
        to *is* the thing the routing metric grades, so a second field could disagree with it.
        """
        return self.expected_source_type == "structured_api"

    @property
    def is_web(self) -> bool:
        """Whether this question is answered from the open web (Phase 2).

        Keyed off the lane for the same reason `is_structured` is.
        """
        return self.expected_source_type == "web"

    @model_validator(mode="after")
    def _check_shape(self) -> "GoldQuestion":
        if self.expected_abstain:
            if self.expected_doc_ids:
                raise ValueError(f"{self.id}: abstention must have empty expected_doc_ids")
            if self.expected_source_type is not None:
                raise ValueError(f"{self.id}: abstention must have expected_source_type=null")
            if self.corpus is not None:
                raise ValueError(f"{self.id}: abstention must have corpus=null")
            if self.difficulty is not None:
                raise ValueError(f"{self.id}: abstention must have difficulty=null")
            if self.expected_snippet:
                raise ValueError(f"{self.id}: abstention must have no expected_snippet")
        elif self.is_web:
            # A **third shape**, and the thinnest of the three on purpose. A web question carries an
            # expected *lane* and no expected answer, because a gold answer for "was there a recall
            # this week" is wrong within a week of being written — and a gold set that rots silently
            # is worse than one that admits its scope. It is graded on routing and groundedness,
            # which are stable properties of the system rather than of the world
            # (docs/web_search_tool.md §12).
            if self.expected_doc_ids or self.expected_snippet or self.corpus:
                raise ValueError(
                    f"{self.id}: a web question is answered from a live page, so it must not carry "
                    f"corpus, expected_doc_ids or expected_snippet"
                )
            if self.expected_answer or self.answer_key_facts:
                raise ValueError(
                    f"{self.id}: a web question must not carry an expected_answer or "
                    f"answer_key_facts — the web moves, and a pinned answer would rot silently "
                    f"while still being scored. Phase 2 grades routing, not answer correctness."
                )
            if self.expected_table or self.expected_cells:
                raise ValueError(
                    f"{self.id}: expected_table/expected_cells belong to a structured_api question"
                )
            if self.difficulty is None:
                raise ValueError(f"{self.id}: non-abstention needs a difficulty")
        elif self.is_structured:
            # A third shape, and it shares almost nothing with the reference one: a row has no
            # document, no chunk and no snippet, so requiring those here would force a structured
            # question to carry fields that mean nothing for it — and would drag it into the
            # recall@5 denominator, where it can only ever count as a miss.
            if not self.expected_table:
                raise ValueError(f"{self.id}: a structured_api question needs an expected_table")
            if not self.expected_cells:
                raise ValueError(f"{self.id}: a structured_api question needs expected_cells")
            if self.expected_doc_ids or self.expected_snippet or self.corpus:
                raise ValueError(
                    f"{self.id}: a structured_api question is answered from a table, so it must "
                    f"not carry corpus, expected_doc_ids or expected_snippet"
                )
            if self.plan_year is None:
                raise ValueError(
                    f"{self.id}: a structured_api question needs a plan_year — the tables are "
                    f"per-year and an unpinned question cannot be scored against one"
                )
            if not self.answer_key_facts:
                raise ValueError(f"{self.id}: non-abstention needs at least one answer_key_facts")
            if self.difficulty is None:
                raise ValueError(f"{self.id}: non-abstention needs a difficulty")
        else:
            if not self.expected_doc_ids:
                raise ValueError(f"{self.id}: non-abstention needs at least one expected_doc_id")
            if self.expected_source_type is None:
                raise ValueError(f"{self.id}: non-abstention needs an expected_source_type")
            if self.corpus is None:
                raise ValueError(f"{self.id}: non-abstention needs a corpus")
            if self.difficulty is None:
                raise ValueError(f"{self.id}: non-abstention needs a difficulty")
            if not self.expected_snippet:
                raise ValueError(f"{self.id}: non-abstention needs an expected_snippet")
            if not self.expected_answer:
                raise ValueError(f"{self.id}: non-abstention needs an expected_answer")
            if not self.answer_key_facts:
                raise ValueError(f"{self.id}: non-abstention needs at least one answer_key_facts")
            if self.expected_table or self.expected_cells:
                raise ValueError(
                    f"{self.id}: expected_table/expected_cells belong to a structured_api question"
                )

        if not self.expected_abstain and self.becomes_answerable_at_phase is not None:
            raise ValueError(f"{self.id}: becomes_answerable_at_phase only applies to abstentions")
        return self


class GoldSet(BaseModel):
    questions: list[GoldQuestion]

    def by_corpus(self, corpus: CorpusName) -> list[GoldQuestion]:
        return [q for q in self.questions if q.corpus == corpus]

    def by_difficulty(self, difficulty: Difficulty) -> list[GoldQuestion]:
        return [q for q in self.questions if q.difficulty == difficulty]

    def abstentions(self) -> list[GoldQuestion]:
        return [q for q in self.questions if q.expected_abstain]

    def in_corpus(self) -> list[GoldQuestion]:
        """Questions answered from the reference corpus.

        **Defined by what it *is*, not by what it is not.** This used to exclude abstentions and
        structured questions by name, which meant every new lane had to remember to add itself to
        an exclusion list — and forgetting would silently drop recall@5, making a phase that added
        a capability look like a regression. Selecting on the lane directly cannot be broken that
        way: a retrieval-only runner is asked this set, and asking it for a table lookup or a live
        web page would score a guaranteed miss as if it were a finding.
        """
        return [q for q in self.questions if q.expected_source_type == "reference"]

    def structured(self) -> list[GoldQuestion]:
        return [q for q in self.questions if q.is_structured]

    def web(self) -> list[GoldQuestion]:
        return [q for q in self.questions if q.is_web]
