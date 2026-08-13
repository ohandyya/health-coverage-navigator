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
    becomes_answerable_at_phase: int | None = None

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
            if self.becomes_answerable_at_phase is not None:
                raise ValueError(
                    f"{self.id}: becomes_answerable_at_phase only applies to abstentions"
                )
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
        return [q for q in self.questions if not q.expected_abstain]
