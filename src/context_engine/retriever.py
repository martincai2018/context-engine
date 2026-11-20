"""检索与上下文包组装：BM25 召回 → 关系图扩展 → 分节输出，受字符预算约束。"""
from __future__ import annotations

import os
import re

from .config import CONTEXT_CHAR_BUDGET, TOP_K
from .indexer import ContextIndex

# Wren SQL 中带双引号的模型引用，如 "Orders" / "Customer"
_MODEL_REF_RE = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"')


class Retriever:
    def __init__(self, index: ContextIndex) -> None:
        self.index = index

    def search(self, question: str, top_k: int = TOP_K) -> dict:
        hits, retrieval_mode = self.index.hybrid_search(question, top_k)
        hit_docs = [self.index.docs[i] for i, _ in hits]

        # 模型/字段二次召回：类型过滤的混合检索，百表规模下防止列文档淹没模型文档
        typed_hits, _ = self.index.hybrid_search_typed(question, ("model", "column"), 8)
        model_docs = [self.index.docs[i] for i, _ in typed_hits]

        # 从命中的 golden SQL 解析模型引用，补全模型文档（中文问题匹配不到英文模型名的兜底）
        referenced = set()
        for d in hit_docs:
            if d.type == "golden_sql":
                referenced |= set(_MODEL_REF_RE.findall(d.payload.get("sql", "")))
        all_models = {d.payload["name"]: d for d in self.index.docs if d.type == "model"}
        have = {d.payload.get("name") for d in model_docs}
        for name in referenced:
            if name in all_models and name not in have:
                model_docs.append(all_models[name])
                have.add(name)

        # 关系图扩展：命中模型 → 邻居模型（join 候选）
        hit_models = [d.payload["name"] for d in model_docs if d.type == "model"]
        neighbor_names = self.index.graph_expand(hit_models)
        neighbor_docs = [d for d in self.index.docs
                         if d.type == "model" and d.payload.get("name") in neighbor_names]

        # 命中的术语（用于提示口径）
        glossary_hits = [d for d in hit_docs if d.type == "glossary"]
        golden_hits = [d for d in hit_docs if d.type == "golden_sql"]
        metric_view_hits = [d for d in hit_docs if d.type in ("metric", "view", "enum")]

        # 团队经验记忆（Mem0，可选组件；未装 memory extra 或服务异常时静默降级）
        memories: list[dict] = []
        if os.getenv("CONTEXT_MEMORY", "1") != "0":
            try:
                from memory import store as memory_store
                memories = memory_store.recall(question, scope="all", limit=3)
                retrieval_mode += "+memory"
            except Exception as exc:  # 记忆层不可用不影响主检索
                memories = [{"memory": f"(记忆层不可用: {type(exc).__name__})",
                             "metadata": {"kind": "error"}}]

        packet = {
            "question": question,
            "retrieval_mode": retrieval_mode,
            "synonym_expansions": self.index.expand_question(question),
            "team_memories": memories,
            "glossary": [d.payload for d in glossary_hits],
            "models": [d.payload for d in model_docs][:8],
            "join_candidates": _relationships_for(self.index, hit_models, neighbor_names),
            "metrics_views": [d.payload for d in metric_view_hits],
            "golden_sql": [d.payload for d in golden_hits],
            "neighbor_models": neighbor_names,
        }
        packet["context_text"] = self._render(packet)
        packet["char_count"] = len(packet["context_text"])
        packet["budget"] = CONTEXT_CHAR_BUDGET
        return packet

    def _render(self, packet: dict) -> str:
        """生成给 LLM 的可读上下文文本，超预算时按优先级裁剪 golden SQL。"""
        lines: list[str] = ["# 检索到的语义上下文"]
        if packet["synonym_expansions"]:
            lines.append("## 术语扩展\n" + ", ".join(packet["synonym_expansions"]))
        if packet["glossary"]:
            lines.append("## 业务口径")
            for g in packet["glossary"]:
                lines.append(f"- {g['term']}：{g.get('meaning','')}")
        if packet["models"]:
            lines.append("## 相关模型与字段")
            for m in packet["models"]:
                if "tableReference" in m:  # model 级
                    cols = ", ".join(c["name"] for c in m.get("columns", []))
                    lines.append(f"- 模型 {m['name']}（物理表 {m['tableReference'].get('table')}）: {cols}")
        if packet["join_candidates"]:
            lines.append("## Join 路径（来自 MDL 关系定义，禁止自行猜测外键）")
            for r in packet["join_candidates"]:
                lines.append(f"- {r['name']}: {r['condition']} ({r.get('joinType')})")
        if packet["metrics_views"]:
            lines.append("## 指标/视图/枚举")
            for mv in packet["metrics_views"]:
                lines.append(f"- {mv.get('name')}")
        real_memories = [m for m in packet["team_memories"]
                         if m.get("metadata", {}).get("kind") != "error"]
        if real_memories:
            lines.append("## 团队历史经验（Mem0）")
            for m in real_memories:
                kind = m.get("metadata", {}).get("kind", "note")
                lines.append(f"- [{kind}] {m.get('memory', '')}")

        if packet["golden_sql"]:
            lines.append("## Golden SQL 参考")
            for g in packet["golden_sql"]:
                lines.append(f"-- {g['question']}\n{g['sql'].strip()}")

        text = "\n".join(lines)
        if len(text) > CONTEXT_CHAR_BUDGET:
            # 保底裁剪：去掉 golden SQL 明细，只保留标题
            text = text.split("## Golden SQL 参考")[0].rstrip() + "\n## Golden SQL 参考\n（超出预算已省略）"
        return text


def _relationships_for(index: ContextIndex, hit_models: list[str], neighbors: list[str]) -> list[dict]:
    wanted = set(hit_models) | set(neighbors)
    out = []
    for rel in index.relationships:
        models = rel.get("models", [])
        if any(m in wanted for m in models):
            out.append(rel)
    return out
