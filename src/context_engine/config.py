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
