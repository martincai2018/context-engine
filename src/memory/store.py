"""Mem0 v2 封装：四作用域映射到团队学习模型。

作用域设计：
  user  作用域 = 开发者个人经验（user_id=登录名），自动事实抽取，量大、噪声高
  team  作用域 = 团队共享记忆（agent_id=TEAM_AGENT_ID），由个人记忆 promote 评审而来
记忆类别（metadata.kind）：lesson 教训 / decision 决策 / correction 口径纠错 /
                          convention 规范 / issue 环境问题
注意：mem0 v2 的 search/get_all 必须用 filters 传作用域，不再接受顶层 entity 参数。
"""
from __future__ import annotations

import os

# 必须在 import mem0 前关闭遥测，避免向 PostHog 外发数据
os.environ.setdefault("MEM0_TELEMETRY", "False")

import getpass
from functools import lru_cache
from pathlib import Path
from typing import Any

from context_engine.config import PROJECT_ROOT

TEAM_AGENT_ID = "dw-dev-team"
MEMORY_DB_PATH = str(Path(os.getenv("MEM0_DB_PATH", PROJECT_ROOT / ".mem0" / "chroma")))
OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
LLM_MODEL = os.getenv("MEM0_LLM_MODEL", "phi4:latest")
EMBED_MODEL = os.getenv("MEM0_EMBED_MODEL", "bge-m3:latest")
EMBED_DIMS = int(os.getenv("MEM0_EMBED_DIMS", "1024"))

VALID_KINDS = {"lesson", "decision", "correction", "convention", "issue"}


@lru_cache(maxsize=1)
def get_memory():
    """单例 Mem0 实例；mem0ai 未安装时给出明确报错。"""
    try:
        from mem0 import Memory
    except ImportError as exc:
        raise RuntimeError("未安装记忆依赖：uv sync --extra vector --extra memory") from exc

    config = {
        "vector_store": {
            "provider": "chroma",
            "config": {"collection_name": "dw_team_memory", "path": MEMORY_DB_PATH},
        },
        "llm": {
            "provider": "ollama",
            "config": {"model": LLM_MODEL, "ollama_base_url": OLLAMA_URL,
                       "temperature": 0.1},
        },
        "embedder": {
            "provider": "ollama",
            "config": {"model": EMBED_MODEL, "ollama_base_url": OLLAMA_URL,
                       "embedding_dims": EMBED_DIMS},
        },
    }
    return Memory.from_config(config)


def current_user() -> str:
    return os.getenv("MEM0_USER") or getpass.getuser()


def remember(content: str, scope: str = "user", kind: str = "lesson",
             user_id: str | None = None, metadata: dict | None = None) -> dict:
    """写入一条记忆。scope=user 进个人库，scope=team 进团队库。"""
    if kind not in VALID_KINDS:
        raise ValueError(f"kind 必须是 {VALID_KINDS} 之一")
    meta = {"kind": kind, **(metadata or {})}
    m = get_memory()
    if scope == "team":
        return m.add(content, agent_id=TEAM_AGENT_ID, metadata=meta)
    return m.add(content, user_id=user_id or current_user(), metadata=meta)


def recall(query: str, scope: str = "team", user_id: str | None = None,
           limit: int = 5) -> list[dict[str, Any]]:
    """检索相关记忆。scope: team（默认）/ user / all（团队+个人）。"""
    m = get_memory()
    if scope == "team":
        return _rows(m.search(query, filters={"agent_id": TEAM_AGENT_ID}, top_k=limit))
    if scope == "user":
        return _rows(m.search(query, filters={"user_id": user_id or current_user()}, top_k=limit))
    team = _rows(m.search(query, filters={"agent_id": TEAM_AGENT_ID}, top_k=limit))
    mine = _rows(m.search(query, filters={"user_id": user_id or current_user()}, top_k=limit))
    return team + mine


def _rows(result: Any) -> list[dict]:
    """mem0 v2.2 的 get_all 返回 {'results': [...]}，search 返回 list，统一兼容。"""
    if isinstance(result, dict):
        return result.get("results", [])
    return result or []


def list_memories(scope: str = "team", user_id: str | None = None) -> list[dict]:
    m = get_memory()
    filters = ({"agent_id": TEAM_AGENT_ID} if scope == "team"
               else {"user_id": user_id or current_user()})
    return _rows(m.get_all(filters=filters, top_k=100))


def promote_to_team(memory_id: str) -> dict:
    """个人记忆 → 团队记忆的评审晋升：读取原记忆，原样写入团队库并保留来源。"""
    m = get_memory()
    source = m.get(memory_id)
    content = source.get("memory", "")
    meta = {k: v for k, v in (source.get("metadata") or {}).items()}
    meta.update({"promoted_from": memory_id, "promoted_by": current_user()})
    return m.add(content, agent_id=TEAM_AGENT_ID, metadata=meta)


def forget(memory_id: str) -> None:
    get_memory().delete(memory_id)


def stats() -> dict:
    m = get_memory()
    team = _rows(m.get_all(filters={"agent_id": TEAM_AGENT_ID}, top_k=100))
    return {"team_memories": len(team), "store": MEMORY_DB_PATH,
            "llm": LLM_MODEL, "embedder": EMBED_MODEL, "team_agent_id": TEAM_AGENT_ID}
