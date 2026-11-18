"""团队记忆 MCP Server（streamable HTTP，默认 :9200/mcp）。

工具：
  remember(content, scope, kind)   写入记忆（user 个人 / team 团队）
  recall(query, scope)             检索记忆（team/user/all）
  list_memories(scope)             列出记忆
  promote_to_team(memory_id)       个人记忆评审晋升为团队记忆
  forget(memory_id)                删除记忆
  memory_stats()                   统计
"""
from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from . import store

mcp = FastMCP("Team Memory", host="0.0.0.0",
              port=int(os.getenv("MEMORY_MCP_PORT", "9200")))


@mcp.tool()
def remember(content: str, scope: str = "user", kind: str = "lesson",
             user_id: str | None = None) -> dict:
    """沉淀一条经验记忆。kind: lesson/decision/correction/convention/issue。"""
    return store.remember(content, scope=scope, kind=kind, user_id=user_id)


@mcp.tool()
def recall(query: str, scope: str = "team", user_id: str | None = None,
           limit: int = 5) -> list[dict]:
    """按问题检索历史经验；默认查团队共享库。"""
    return store.recall(query, scope=scope, user_id=user_id, limit=limit)


@mcp.tool()
def list_memories(scope: str = "team", user_id: str | None = None) -> list[dict]:
    """列出某作用域下的全部记忆。"""
    return store.list_memories(scope=scope, user_id=user_id)


@mcp.tool()
def promote_to_team(memory_id: str) -> dict:
    """把个人记忆晋升到团队共享库（评审通过后调用）。"""
    return store.promote_to_team(memory_id)


@mcp.tool()
def forget(memory_id: str) -> dict:
    """删除一条过时或错误的记忆。"""
    store.forget(memory_id)
    return {"status": "deleted", "id": memory_id}


@mcp.tool()
def memory_stats() -> dict:
    """记忆库统计与配置。"""
    return store.stats()


if __name__ == "__main__":
    mcp.run(transport=os.getenv("MEMORY_MCP_TRANSPORT", "streamable-http"))
