"""百表规模 rerank 前后对照：量化精排对域内排序精度的改善。

指标：
  域命中率（top-k 内出现目标域表的比例）
  域纯度（top-k 中目标域表占比，衡量跨域噪声）
  首位命中率（top1 即目标域）
运行： PYTHONPATH=src uv run --with httpx --with pyyaml --with jieba \
        --with rank-bm25 --with fastembed --with lancedb python scale/run_rerank_compare.py
"""
from __future__ import annotations

import shutil
from pathlib import Path

from context_engine import config as cfg
from context_engine import indexer as idx_mod
from context_engine.corpus import _model_docs
from context_engine.indexer import ContextIndex

HERE = Path(__file__).resolve().parent
GLOSSARY_TMP = HERE / "data" / "glossary_data"
GLOSSARY_TMP.mkdir(parents=True, exist_ok=True)
shutil.copy(HERE / "glossary.scale.yaml", GLOSSARY_TMP / "glossary.yaml")
cfg.DATA_DIR = str(GLOSSARY_TMP)
idx_mod.DATA_DIR = str(GLOSSARY_TMP)

import json  # noqa: E402

QUESTIONS = [
    ("库存周转分析要找哪些表？", "inventory"),
    ("营销优惠券核销情况", "marketing"),
    ("物流配送时效", "logistics"),
    ("供应商采购金额", "supplier"),
    ("财务结算发票", "finance"),
    ("会员等级分布", "customer"),
    ("商品SKU分类", "product"),
    ("订单退款明细", "order"),
    ("GMV 汇总指标在哪个分层", "order"),
    ("宽表里的库存移动记录", "inventory"),
]
TOP_K = 10


def domain_of(name: str, domain: str) -> bool:
    return f"_{domain}_" in name


def evaluate(index: ContextIndex, use_rerank: bool) -> dict:
    hit_rates, purities, top1 = [], [], []
    for q, domain in QUESTIONS:
        if use_rerank:
            hits, _ = index.rerank_typed(q, ("model",), TOP_K)
        else:
            hits, _ = index.hybrid_search_typed(q, ("model",), TOP_K)
        names = [index.docs[i].payload["name"] for i, _ in hits]
        in_domain = [n for n in names if domain_of(n, domain)]
        hit_rates.append(1.0 if in_domain else 0.0)
        purities.append(len(in_domain) / max(len(names), 1))
        top1.append(1.0 if names and domain_of(names[0], domain) else 0.0)
    n = len(QUESTIONS)
    return {
        "域命中率": sum(hit_rates) / n,
        "平均域纯度": sum(purities) / n,
        "首位命中率": sum(top1) / n,
    }


def main() -> None:
    mdl = json.loads((HERE / "scale_draft.mdl.json").read_text(encoding="utf-8"))
    docs = _model_docs(mdl)
    index = ContextIndex().build(docs, mdl)
    print(f"文档 {len(docs)} | reranker: {index.reranker.stats()}")

    print("\n=== 无 rerank（RRF 粗排）===")
    base = evaluate(index, use_rerank=False)
    for k, v in base.items():
        print(f"  {k}: {v:.0%}")

    print("\n=== 有 rerank（cross-encoder 精排）===")
    reranked = evaluate(index, use_rerank=True)
    for k, v in reranked.items():
        print(f"  {k}: {v:.0%}")

    print("\n=== 改善 ===")
    for k in base:
        delta = reranked[k] - base[k]
        print(f"  {k}: {delta:+.0%}")

    # 展示一个具体案例的排序变化
    q, domain = QUESTIONS[3]  # 供应商采购，之前 top3 混入 order 表
    print(f"\n案例「{q}」top5 对比：")
    base_hits, _ = index.hybrid_search_typed(q, ("model",), 5)
    rerank_hits, _ = index.rerank_typed(q, ("model",), 5)
    print("  粗排:", [index.docs[i].payload["name"] for i, _ in base_hits])
    print("  精排:", [index.docs[i].payload["name"] for i, _ in rerank_hits])


if __name__ == "__main__":
    main()
