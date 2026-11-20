"""Cross-encoder 重排：粗召回取宽 → bge-reranker 精排 → 截断到 top_k。

与向量召回（bi-encoder）的区别：query 和文档拼接后联合编码，精度更高但更慢，
只对已召回的候选（通常 20-50 条）运行，不碰全库。
未安装依赖或模型加载失败时 available=False，调用方自动跳过。
"""
from __future__ import annotations

from .config import RERANK_MODEL
from .corpus import Doc


class Reranker:
    def __init__(self) -> None:
        self.available = False
        self._model = None
        self.model_name = RERANK_MODEL

    def build(self) -> "Reranker":
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
        except ImportError:
            return self  # fastembed 版本不含 rerank 或未装 vector extra
        try:
            self._model = TextCrossEncoder(model_name=RERANK_MODEL)
            self.available = True
        except Exception as exc:  # 模型下载失败等
            print(f"[context-engine] reranker 不可用，跳过重排: {type(exc).__name__}: {exc}")
        return self

    def rerank(self, question: str, docs: list[Doc], candidates: list[tuple[int, float]],
               top_k: int) -> list[tuple[int, float]]:
        """candidates 为 (doc 下标, 粗排分)，返回重排后的 (下标, rerank 分)。"""
        if not self.available or not candidates:
            return candidates[:top_k]
        pairs = [(question, docs[i].text) for i, _ in candidates]
        # fastembed 支持批量 reret，分数越高越相关
        scores = list(self._model.rerank_pairs(pairs))
        ranked = sorted(
            ((candidates[idx][0], float(score)) for idx, score in enumerate(scores)),
            key=lambda x: x[1], reverse=True,
        )
        return ranked[:top_k]

    def stats(self) -> dict:
        return {"available": self.available, "model": self.model_name}
