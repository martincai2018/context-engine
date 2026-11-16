"""Context Engine MCP Server（streamable HTTP，默认 :9100/mcp）。

工具：
  build_index()          从 Wren MDL + 本地术语/golden 文件重建索引
  search_context(q)      主检索：返回结构化上下文包 + 可读 context_text
  add_golden_sql(...)    反馈闭环：追加 golden SQL 并自动重建
  index_stats()          索引统计
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml
from mcp.server.fastmcp import FastMCP

from .config import DATA_DIR, MCP_PORT, MCP_TRANSPORT, TOP_K
from .corpus import build_corpus
from .indexer import ContextIndex
from .retriever import Retriever

mcp = FastMCP("Context Engine", host="0.0.0.0", port=MCP_PORT)

_state: dict[str, object] = {}


def _ensure_index() -> tuple[ContextIndex, Retriever]:
    if "retriever" not in _state:
        build_index()
    return _state["index"], _state["retriever"]  # type: ignore[return-value]


@mcp.tool()
def build_index() -> dict:
    """从 Wren 已部署 MDL 与本地 glossary/golden_sql 重建检索索引。"""
    docs, mdl = build_corpus()
    index = ContextIndex().build(docs, mdl)
    _state["index"] = index
    _state["retriever"] = Retriever(index)
    _state["mdl"] = mdl
    return {"status": "ok", **index.stats()}


@mcp.tool()
def search_context(question: str, top_k: int = TOP_K) -> dict:
    """按自然语言问题检索语义上下文包（模型、字段、join、口径、golden SQL）。"""
    _, retriever = _ensure_index()
    return retriever.search(question, top_k=top_k)


@mcp.tool()
def add_golden_sql(question: str, sql: str, tags: list[str] | None = None) -> dict:
    """把人工验证的问-SQL 对追加到 golden_sql.yaml 并重建索引（反馈闭环）。"""
    path = Path(DATA_DIR) / "golden_sql.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {"queries": []}
    data["queries"].append({"question": question, "sql": sql, "tags": tags or []})
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return build_index()


@mcp.tool()
def index_stats() -> dict:
    """查看当前索引文档数与构成。"""
    index, _ = _ensure_index()
    return index.stats()


if __name__ == "__main__":
    build_index()
    print(f"Context Engine MCP listening on :{MCP_PORT} (transport={MCP_TRANSPORT})")
    mcp.run(transport=MCP_TRANSPORT)
