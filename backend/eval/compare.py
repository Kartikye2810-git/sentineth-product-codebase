"""Compare two harness reports on the questions they share.

A retrieval change is scored on the same questions before and after, so the
runs are paired and the interesting quantity is not the gap between two
percentages - it is which individual questions changed answer. Two runs can
differ by four points of recall@5 with sixteen questions moving in each
direction, which is noise, or with four moving one way and none the other,
which is not.

The p-value is McNemar's exact test over the discordant questions: given
that a question changed, how surprising is it that so many changed in the
same direction? Computed exactly rather than by the chi-squared
approximation because the counts here are small.

Pairs the recall-scored questions, which is what a report's `results` block
holds. The unanswerable and conflict families are not hit-or-miss at a rank
and are reported in their own blocks; compare those by reading them.

    python eval/compare.py before.json after.json
"""

import argparse
import json
from math import comb
from pathlib import Path
from typing import Any


REPORTED_K = (1, 3, 5)


def hits(report: dict[str, Any], k: int) -> dict[str, bool]:
    """Per-question hit/miss at rank k, keyed by question id."""
    return {
        result["id"]: result["rank"] is not None and result["rank"] <= k
        for result in report["results"]
    }


def mcnemar_exact(gained: int, lost: int) -> float:
    """Two-sided exact p over the questions that changed.

    Under the null the direction of each change is a coin flip, so the
    discordant pairs are Binomial(gained + lost, 0.5).
    """
    n = gained + lost

    if n == 0:
        return 1.0

    tail = sum(comb(n, i) for i in range(min(gained, lost) + 1))

    return min(1.0, 2 * tail / 2**n)


def compare(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    shared = sorted(set(hits(before, 1)) & set(hits(after, 1)))
    lines: list[str] = []

    dropped = (len(before["results"]) - len(shared)) + (len(after["results"]) - len(shared))
    if dropped:
        lines.append(f"note: {dropped} question(s) not present in both runs, ignored")
        lines.append("")

    lines.append(f"{len(shared)} questions scored in both runs")
    lines.append("")
    lines.append(f"{'metric':<10}{'before':>9}{'after':>9}{'delta':>10}{'won':>6}{'lost':>6}{'p':>9}")

    for k in REPORTED_K:
        b, a = hits(before, k), hits(after, k)

        gained = sum(1 for q in shared if a[q] and not b[q])
        lost = sum(1 for q in shared if b[q] and not a[q])

        rate_b = sum(b[q] for q in shared) / len(shared)
        rate_a = sum(a[q] for q in shared) / len(shared)
        p = mcnemar_exact(gained, lost)

        lines.append(
            f"{'recall@' + str(k):<10}{rate_b:>8.1%}{rate_a:>9.1%}"
            f"{(rate_a - rate_b) * 100:>+9.1f}pp{gained:>6}{lost:>6}{p:>9.3f}"
        )

    mrr_b = before["metrics"]["mrr"]
    mrr_a = after["metrics"]["mrr"]
    lines.append(f"{'MRR':<10}{mrr_b:>9.3f}{mrr_a:>9.3f}{mrr_a - mrr_b:>+10.3f}")
    lines.append("")

    lines.append(
        f"chunks     {before['corpus']['chunks']:>9}{after['corpus']['chunks']:>9}"
    )
    lines.append("")

    significant = [
        k for k in REPORTED_K
        if mcnemar_exact(
            sum(1 for q in shared if hits(after, k)[q] and not hits(before, k)[q]),
            sum(1 for q in shared if hits(before, k)[q] and not hits(after, k)[q]),
        ) < 0.05
    ]

    if significant:
        lines.append(
            "Distinguishable from noise at p<0.05: "
            + ", ".join(f"recall@{k}" for k in significant)
        )
    else:
        lines.append(
            "No metric is distinguishable from noise at p<0.05. The questions "
            "that changed changed in both directions, which is what a "
            "reshuffle looks like rather than an improvement."
        )

    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    args = parser.parse_args()

    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))

    print("\n".join(compare(before, after)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
