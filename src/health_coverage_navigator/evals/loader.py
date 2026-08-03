"""Load the Phase 0 gold evaluation set from evals/gold/questions.yaml.

Run as a script to print a distribution summary — the "load and inspect the gold eval set"
user-facing capability named in docs/plan.md Phase 0.
"""

import collections

import yaml

from health_coverage_navigator.evals.models import GoldSet
from health_coverage_navigator.paths import GOLD_SET_PATH


def load_gold_set(path=GOLD_SET_PATH) -> GoldSet:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return GoldSet.model_validate(raw)


def _summarize(gold: GoldSet) -> str:
    lines = []
    in_corpus = gold.in_corpus()
    abstentions = gold.abstentions()
    lines.append(
        f"Gold set: {len(gold.questions)} questions "
        f"({len(in_corpus)} in-corpus, {len(abstentions)} abstention)"
    )
    for corpus in ("healthcare_gov", "medicare_ncd", "medicare_pubs"):
        qs = gold.by_corpus(corpus)
        diff = collections.Counter(q.difficulty for q in qs)
        lines.append(
            f"  {corpus:<15} {len(qs):>2}   "
            f"easy {diff['easy']}  medium {diff['medium']}  hard {diff['hard']}"
        )
    n_volatile = sum(1 for q in in_corpus if q.volatile)
    n_plan_year = sum(1 for q in in_corpus if q.plan_year is not None)
    lines.append(f"  volatile: {n_volatile}     plan-year-pinned: {n_plan_year}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(_summarize(load_gold_set()))
