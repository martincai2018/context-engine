"""Context Engine 回归评测：对 evals/questions.yaml 跑检索并计算各维度命中率。

用法：
  uv run python evals/run_eval.py                # 当前索引（默认混合）
  CONTEXT_VECTOR=0 uv run python evals/run_eval.py   # 纯 BM25 基线对比
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from context_engine.corpus import build_corpus  # noqa: E402
from context_engine.indexer import ContextIndex  # noqa: E402
from context_engine.retriever import Retriever  # noqa: E402

DIMENSIONS = {
    "models": (lambda p: [m["name"] for m in p["models"] if "tableReference" in m]),
    "glossary": (lambda p: [g["term"] for g in p["glossary"]]),
    "golden": (lambda p: [g["question"] for g in p["golden_sql"]]),
    "views": (lambda p: [v.get("name") for v in p["metrics_views"]]),
    "joins": (lambda p: [j["name"] for j in p["join_candidates"]]),
}


def main() -> None:
    cases = yaml.safe_load(
        (Path(__file__).parent / "questions.yaml").read_text(encoding="utf-8")
    )["cases"]

    docs, mdl = build_corpus()
    index = ContextIndex().build(docs, mdl)
    retriever = Retriever(index)
    mode = "hybrid" if index.vector.available else "bm25"
    print(f"检索模式: {mode} | 用例数: {len(cases)}\n")

    totals: dict[str, list[int]] = {d: [] for d in DIMENSIONS}
    all_pass = True
    for case in cases:
        packet = retriever.search(case["question"])
        actual = {d: fn(packet) for d, fn in DIMENSIONS.items()}
        case_ok = True
        detail = []
        for dim, expected in case["expect"].items():
            hit = all(e in actual[dim] for e in expected)
            totals[dim].append(1 if hit else 0)
            case_ok &= hit
            detail.append(f"{dim}={'✓' if hit else '✗ 缺' + str([e for e in expected if e not in actual[dim]])}")
        all_pass &= case_ok
        print(f"[{'PASS' if case_ok else 'FAIL'}] {case['id']} {case['question']}")
        print("       " + " | ".join(detail))

    print("\n===== 维度命中率 =====")
    for dim, marks in totals.items():
        if not marks:
            continue
        print(f"  {dim:9s}: {sum(marks)}/{len(marks)} = {sum(marks)/len(marks):.0%}")
    overall = sum(sum(v) for v in totals.values()) / sum(len(v) for v in totals.values() if v)
    print(f"\n综合命中率: {overall:.1%}")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
