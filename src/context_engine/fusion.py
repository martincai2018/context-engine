"""RRF（Reciprocal Rank Fusion）：把 BM25 与向量两路排名融合成单一排名。

分数 = Σ  weight / (RRF_K + rank)，只依赖排名不依赖分数量纲，工程上最稳健。
"""
from __future__ import annotations

from .config import BM25_WEIGHT, RRF_K, VECTOR_WEIGHT


def reciprocal_rank_fusion(
    bm25_hits: list[tuple[int, float]],
    vector_hits: list[tuple[int, float]],
    top_k: int,
    weights: tuple[float, float] = (BM25_WEIGHT, VECTOR_WEIGHT),
    rrf_k: int = RRF_K,
) -> list[tuple[int, float]]:
    scores: dict[int, float] = {}
    w_bm25, w_vec = weights
    for rank, (idx, _) in enumerate(bm25_hits, start=1):
        scores[idx] = scores.get(idx, 0.0) + w_bm25 / (rrf_k + rank)
    for rank, (idx, _) in enumerate(vector_hits, start=1):
        scores[idx] = scores.get(idx, 0.0) + w_vec / (rrf_k + rank)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return ranked[:top_k]
