"""运行配置，全部可用环境变量覆盖。"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("CONTEXT_DATA_DIR", PROJECT_ROOT / "data"))

# Wren 配置 UI（GET /mdl 返回当前部署的 MDL JSON）
WREN_UI_URL = os.getenv("WREN_UI_URL", "http://localhost:9001")
# MCP 服务端口与传输
MCP_PORT = int(os.getenv("CONTEXT_MCP_PORT", "9100"))
MCP_TRANSPORT = os.getenv("CONTEXT_MCP_TRANSPORT", "streamable-http")
# 上下文包字符预算（粗略按 1.5 字符/token 估算）
CONTEXT_CHAR_BUDGET = int(os.getenv("CONTEXT_CHAR_BUDGET", "6000"))
TOP_K = int(os.getenv("CONTEXT_TOP_K", "8"))

# ---- 向量召回（可选，装了 [vector] extra 才启用）----
VECTOR_ENABLED = os.getenv("CONTEXT_VECTOR", "1") != "0"
EMBED_MODEL = os.getenv("CONTEXT_EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
LANCEDB_URI = os.getenv("CONTEXT_LANCEDB", str(PROJECT_ROOT / ".lancedb"))
# RRF 融合常数（经验值 60）与两路权重
RRF_K = int(os.getenv("CONTEXT_RRF_K", "60"))
BM25_WEIGHT = float(os.getenv("CONTEXT_BM25_WEIGHT", "1.0"))
VECTOR_WEIGHT = float(os.getenv("CONTEXT_VECTOR_WEIGHT", "1.0"))
