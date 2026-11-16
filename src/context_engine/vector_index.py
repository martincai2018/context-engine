"""向量索引：fastembed 本地嵌入（bge-small-zh）+ LanceDB 嵌入式存储。

设计为可选组件：未安装 [vector] extra 或模型不可用时优雅降级为纯 BM25。
"""
from __future__ import annotations

from pathlib import Path

from .config import EMBED_MODEL, LANCEDB_URI
from .corpus import Doc


class VectorIndex:
    def __init__(self) -> None:
        self.available = False
        self._model = None
        self._db = None
        self._table = None
        self.dim: int = 0

    # ---------- 生命周期 ----------
    def build(self, docs: list[Doc]) -> "VectorIndex":
        try:
            from fastembed import TextEmbedding
            import lancedb
        except ImportError:
            return self  # 未装 extra，保持 unavailable

        Path(LANCEDB_URI).mkdir(parents=True, exist_ok=True)
        self._model = TextEmbedding(EMBED_MODEL)
        embeddings = list(self._model.embed([d.text for d in docs]))
        self.dim = len(embeddings[0])

        self._db = lancedb.connect(LANCEDB_URI)
        records = [
            {"id": i, "doc_id": d.id, "type": d.type, "title": d.title,
             "text": d.text, "vector": embeddings[i]}
            for i, d in enumerate(docs)
        ]
        if "context" in self._db.table_names():
            self._db.drop_table("context")
        self._table = self._db.create_table("context", records)
        self.available = True
        return self

    def search(self, question: str, top_k: int) -> list[tuple[int, float]]:
        """返回（docs 下标, 相似度分数），按相似度降序。"""
        if not self.available:
            return []
        q_vec = list(self._model.embed([question]))[0]
        rows = (self._table.search(q_vec)
                .metric("cosine").limit(top_k).to_list())
        # LanceDB cosine 距离越小越相似，转成正向分数
        return [(r["id"], 1.0 - float(r.get("_distance", 0.0))) for r in rows]

    def stats(self) -> dict:
        return {"available": self.available, "model": EMBED_MODEL,
                "dim": self.dim, "store": LANCEDB_URI}
