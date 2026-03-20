"""Pipeline benchmark — run before and after each stage to compare quality.

Usage:
    # Establish a baseline (before any changes):
    python -m backend.agents.test_pipeline_benchmark --label "baseline"

    # After Stage 1 changes:
    python -m backend.agents.test_pipeline_benchmark --label "stage1_github_org"

    # The script automatically loads the previous run and prints a diff table.

Saved to: backend/benchmark_results/history.json
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(name)s | %(message)s")
# Suppress noisy sub-loggers for cleaner output
logging.getLogger("backend.agents.people_finder").setLevel(logging.INFO)
logging.getLogger("backend.tools.github_org").setLevel(logging.INFO)

from backend.agents.people_finder import PeopleFinder

# ── Test scenarios ─────────────────────────────────────────────────────────

TEST_CASES = [
    {
        "id": "stripe_intern",
        "company": "Stripe",
        "role": "Software Engineering Intern",
        "label": "Stripe SWE Intern",
    },
]

RESULTS_DIR = Path(__file__).parent.parent / "benchmark_results"


# ── Metrics collection ─────────────────────────────────────────────────────

def collect_metrics(people, duration_s: float) -> dict:
    """Extract comparable metrics from a list of Person objects."""
    if not people:
        return {"total": 0, "duration_s": round(duration_s, 1)}

    scores = [p.priority_score for p in people]
    influence_scores = [p.influence_score for p in people]
    reach_scores = [p.reachability_score for p in people]

    source_counts: dict[str, int] = {}
    for p in people:
        src = p.discovery_source or "unknown"
        source_counts[src] = source_counts.get(src, 0) + 1

    cat_counts: dict[str, int] = {}
    for p in people:
        cat = p.contact_category or "unknown"
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    score_bands = {
        "0.0-0.5": sum(1 for s in scores if s < 0.5),
        "0.5-0.6": sum(1 for s in scores if 0.5 <= s < 0.6),
        "0.6-0.7": sum(1 for s in scores if 0.6 <= s < 0.7),
        "0.7-0.8": sum(1 for s in scores if 0.7 <= s < 0.8),
        "0.8+":    sum(1 for s in scores if s >= 0.8),
    }

    top3 = [
        {"name": p.name, "title": p.title[:60], "score": round(p.priority_score, 3),
         "source": p.discovery_source, "warm": len(p.warm_signals)}
        for p in people[:3]
    ]

    return {
        "total": len(people),
        "avg_score": round(sum(scores) / len(scores), 3),
        "avg_influence": round(sum(influence_scores) / len(influence_scores), 3),
        "avg_reachability": round(sum(reach_scores) / len(reach_scores), 3),
        "max_score": round(max(scores), 3),
        "with_warm_signals": sum(1 for p in people if p.warm_signals),
        "with_github": sum(1 for p in people if p.has_public_github),
        "by_source": source_counts,
        "by_category": cat_counts,
        "score_bands": score_bands,
        "top3": top3,
        "duration_s": round(duration_s, 1),
    }


# ── Comparison printer ─────────────────────────────────────────────────────

def _delta(new_val, old_val, higher_is_better: bool = True) -> str:
    """Format a numeric delta with color hint."""
    if old_val is None or new_val is None:
        return ""
    try:
        diff = float(new_val) - float(old_val)
    except (TypeError, ValueError):
        return ""
    if diff == 0:
        return "(=)"
    arrow = "▲" if diff > 0 else "▼"
    good = (diff > 0) == higher_is_better
    sign = "+" if diff > 0 else ""
    tag = " ✓" if good else " ✗"
    return f"({arrow}{sign}{diff:.3g}{tag})"


def print_comparison(label_new: str, metrics_new: dict,
                     label_old: str | None, metrics_old: dict | None) -> None:
    W = 80
    print("\n" + "=" * W)
    print(f"  BENCHMARK RESULTS")
    print(f"  New  : {label_new}")
    if label_old:
        print(f"  Prev : {label_old}")
    print("=" * W)

    def row(name: str, key: str, higher_is_better: bool = True, fmt=None):
        nv = metrics_new.get(key)
        ov = metrics_old.get(key) if metrics_old else None
        nv_str = fmt(nv) if fmt and nv is not None else str(nv)
        ov_str = (fmt(ov) if fmt and ov is not None else str(ov)) if ov is not None else "—"
        delta = _delta(nv, ov, higher_is_better) if metrics_old else ""
        print(f"  {name:<30}  {nv_str:<10}  {ov_str:<10}  {delta}")

    print(f"\n  {'METRIC':<30}  {'NOW':<10}  {'PREV':<10}  DELTA")
    print(f"  {'-'*30}  {'-'*10}  {'-'*10}  {'------'}")

    row("Total contacts",       "total")
    row("Avg priority score",   "avg_score",       fmt=lambda x: f"{x:.3f}")
    row("Avg influence",        "avg_influence",   fmt=lambda x: f"{x:.3f}")
    row("Avg reachability",     "avg_reachability",fmt=lambda x: f"{x:.3f}")
    row("Max score",            "max_score",       fmt=lambda x: f"{x:.3f}")
    row("With warm signals",    "with_warm_signals")
    row("With GitHub",          "with_github")
    row("Duration (s)",         "duration_s",      higher_is_better=False, fmt=lambda x: f"{x:.1f}s")

    # Discovery source breakdown
    print(f"\n  Discovery sources:")
    all_sources = set(metrics_new.get("by_source", {}).keys())
    if metrics_old:
        all_sources |= set(metrics_old.get("by_source", {}).keys())
    for src in sorted(all_sources):
        nv = metrics_new.get("by_source", {}).get(src, 0)
        ov = metrics_old.get("by_source", {}).get(src, 0) if metrics_old else None
        ov_str = str(ov) if ov is not None else "—"
        delta = _delta(nv, ov) if metrics_old else ""
        print(f"    {src:<28}  {nv:<10}  {ov_str:<10}  {delta}")

    # Category breakdown
    print(f"\n  Contact categories:")
    all_cats = set(metrics_new.get("by_category", {}).keys())
    if metrics_old:
        all_cats |= set(metrics_old.get("by_category", {}).keys())
    for cat in sorted(all_cats):
        nv = metrics_new.get("by_category", {}).get(cat, 0)
        ov = metrics_old.get("by_category", {}).get(cat, 0) if metrics_old else None
        ov_str = str(ov) if ov is not None else "—"
        delta = _delta(nv, ov) if metrics_old else ""
        print(f"    {cat:<28}  {nv:<10}  {ov_str:<10}  {delta}")

    # Score bands
    print(f"\n  Score distribution:")
    for band, nv in metrics_new.get("score_bands", {}).items():
        ov = metrics_old.get("score_bands", {}).get(band) if metrics_old else None
        ov_str = str(ov) if ov is not None else "—"
        delta = _delta(nv, ov) if metrics_old else ""
        print(f"    {band:<28}  {nv:<10}  {ov_str:<10}  {delta}")

    # Top 3 contacts
    print(f"\n  Top 3 contacts (this run):")
    for i, c in enumerate(metrics_new.get("top3", []), 1):
        warm_tag = f"  [{c['warm']} warm signals]" if c["warm"] else ""
        print(f"    {i}. {c['name']} ({c['score']:.3f}) — {c['title'][:50]}")
        print(f"       source={c['source']}{warm_tag}")

    print("=" * W + "\n")


# ── History persistence ────────────────────────────────────────────────────

def load_history() -> list[dict]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    history_file = RESULTS_DIR / "history.json"
    if history_file.exists():
        try:
            return json.loads(history_file.read_text())
        except Exception:
            return []
    return []


def save_run(run_data: dict) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    history = load_history()
    history.append(run_data)
    (RESULTS_DIR / "history.json").write_text(json.dumps(history, indent=2))
    # Also write latest for easy inspection
    (RESULTS_DIR / "latest.json").write_text(json.dumps(run_data, indent=2))


def get_previous_run(test_case_id: str) -> dict | None:
    """Get the most recent run for this test case (excluding the current one)."""
    history = load_history()
    for run in reversed(history[:-1] if history else []):  # skip last (just saved)
        if run.get("test_case_id") == test_case_id:
            return run
    return None


# ── Main runner ────────────────────────────────────────────────────────────

async def run_benchmark(stage_label: str) -> None:
    finder = PeopleFinder()

    for tc in TEST_CASES:
        print(f"\nRunning: {tc['label']} …")
        start = time.time()
        try:
            people = await finder.find_people(
                company=tc["company"],
                role=tc["role"],
                target_count=8,
            )
        except Exception as e:
            print(f"  ERROR: {e}")
            people = []
        duration = time.time() - start

        metrics = collect_metrics(people, duration)

        run_data = {
            "test_case_id": tc["id"],
            "test_case_label": tc["label"],
            "stage_label": stage_label,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
        }

        # Load previous before saving (so we compare against the true previous run)
        prev = get_previous_run(tc["id"])
        save_run(run_data)

        print_comparison(
            label_new=f"{stage_label} @ {run_data['timestamp'][:19]}",
            metrics_new=metrics,
            label_old=f"{prev['stage_label']} @ {prev['timestamp'][:19]}" if prev else None,
            metrics_old=prev["metrics"] if prev else None,
        )

    print(f"Results saved to {RESULTS_DIR}/")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline benchmark comparison tool")
    parser.add_argument(
        "--label", "-l",
        default="run",
        help='Stage label for this run, e.g. "baseline", "stage1_github_org"',
    )
    args = parser.parse_args()
    asyncio.run(run_benchmark(args.label))


if __name__ == "__main__":
    main()
