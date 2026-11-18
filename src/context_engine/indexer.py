"""索引：jieba 中文分词 + BM25，附带术语同义词表和 MDL 关系图。"""
from __future__ import annotations

import re
from pathlib import Path

import jieba
import yaml
from rank_bm25 import BM25Okapi

from .config import DATA_DIR, VECTOR_ENABLED
from .corpus import Doc
from .fusion import reciprocal_rank_fusion
from .vector_index import VectorIndex

_TOKEN_RE = re.compile(r"[0-9a-zA-Z_]+")


def tokenize(text: str) -> list[str]:
    """中英文混合分词：jieba 切中文，正则保留英文标识符（表名/字段名）。"""
    tokens = [t.strip().lower() for t in jieba.lcut(text) if t.strip()]
    tokens += [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]
    return [t for t in tokens if t and not t.isspace()]


class ContextIndex:
    def __init__(self) -> None:
        self.docs: list[Doc] = []
        self.bm25: BM25Okapi | None = None
        self.synonym_map: dict[str, str] = {}     # 同义词 -> 规范术语
        self.relationships: list[dict] = []
        self.model_neighbors: dict[str, set[str]] = {}
        self.vector = VectorIndex()

    # ---------- 构建 ----------
    def build(self, docs: list[Doc], mdl: dict) -> "ContextIndex":
        self.docs = docs
        self.bm25 = BM25Okapi([tokenize(d.text) for d in docs])
        self._load_synonyms()
        self._build_relationship_graph(mdl)
        if VECTOR_ENABLED:
            try:
                self.vector.build(docs)
            except Exception as exc:  # 模型下载失败等情况下降级为纯 BM25
                print(f"[context-engine] 向量索引不可用，降级 BM25: {exc}")
        return self

    def _load_synonyms(self) -> None:
        path = Path(DATA_DIR) / "glossary.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for item in data.get("terms", []):
            canon = item["term"]
            self.synonym_map[canon.lower()] = canon
            for syn in item.get("synonyms", []):
                self.synonym_map[syn.lower()] = canon

    def _build_relationship_graph(self, mdl: dict) -> None:
        self.relationships = mdl.get("relationships", [])
        neighbors: dict[str, set[str]] = {}
        for rel in self.relationships:
            a, b = rel.get("models", [None, None])
            if not a or not b:
                continue
            neighbors.setdefault(a, set()).add(b)
            neighbors.setdefault(b, set()).add(a)
        self.model_neighbors = neighbors

    # ---------- 查询期能力 ----------
    def expand_question(self, question: str) -> list[str]:
        """同义词扩展：把口语词替换为规范术语，返回补充 token。"""
        expanded: set[str] = set()
        lowered = question.lower()
        for syn, canon in self.synonym_map.items():
            if syn and syn in lowered and canon.lower() != syn:
                expanded.add(canon.lower())
        return list(expanded)

    def bm25_search(self, question: str, top_k: int) -> list[tuple[int, float]]:
        assert self.bm25 is not None
        tokens = tokenize(question) + self.expand_question(question)
        scores = self.bm25.get_scores(tokens)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [(i, float(s)) for i, s in ranked[:top_k] if s > 0]

    def hybrid_search(self, question: str, top_k: int) -> tuple[list[tuple[int, float]], str]:
        """BM25 + 向量 RRF 融合；向量不可用/索引损坏时自动降级纯 BM25。"""
        bm25_hits = self.bm25_search(question, top_k * 2)
        if self.vector.available:
            try:
                vec_hits = self.vector.search(question, top_k * 2)
                return reciprocal_rank_fusion(bm25_hits, vec_hits, top_k), "hybrid(bm25+vector+RRF)"
            except Exception as exc:
                print(f"[context-engine] 向量检索失败，本次降级 BM25: {type(exc).__name__}: {exc}")
        return bm25_hits[:top_k], "bm25"

    def bm25_search_typed(self, question: str, doc_types: tuple[str, ...], top_k: int) -> list[tuple[int, float]]:
        """限定文档类型的 BM25 检索（用于模型/字段的二次召回）。"""
        assert self.bm25 is not None
        tokens = tokenize(question) + self.expand_question(question)
        scores = self.bm25.get_scores(tokens)
        candidates = [(i, float(s)) for i, s in enumerate(scores)
                      if self.docs[i].type in doc_types and s > 0]
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:top_k]

    def graph_expand(self, hit_model_names: list[str]) -> list[str]:
        """命中模型的关系邻居（join 候选）。"""
        out: set[str] = set()
        for name in hit_model_names:
            out |= self.model_neighbors.get(name, set())
        return list(out - set(hit_model_names))

    def stats(self) -> dict:
        by_type: dict[str, int] = {}
        for d in self.docs:
            by_type[d.type] = by_type.get(d.type, 0) + 1
        return {"documents": len(self.docs), "by_type": by_type,
                "synonyms": len(self.synonym_map), "relationships": len(self.relationships)}
