"""语义库搭建流水线 ①：从 Wren 已连接的物理库自动生成 MDL 初稿。

通过 Wren MCP 的 list_remote_tables / get_table_columns_info 探查物理表，
产出带占位表达式的 MDL JSON，人工补充业务名/指标/关系后再部署。
用法： uv run python scripts/bootstrap_mdl.py > draft.mdl.json
"""
from __future__ import annotations

import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

WREN_MCP = os.getenv("WREN_MCP_URL", "http://localhost:9000/mcp")


async def main() -> None:
    async with streamablehttp_client(WREN_MCP) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tables = json.loads((await session.call_tool("list_remote_tables", {})).content[0].text)
            models = []
            for t in tables[:50]:
                # 不同连接器返回结构可能是字符串或对象，做兼容处理
                table_name = t if isinstance(t, str) else t.get("name") or t.get("table")
                info = await session.call_tool(
                    "get_table_columns_info", {"table": table_name}
                )
                try:
                    columns = json.loads(info.content[0].text)
                except (json.JSONDecodeError, IndexError):
                    columns = []
                models.append({
                    "name": table_name,
                    "tableReference": {"table": table_name},
                    "columns": [
                        {"name": c.get("name", c) if isinstance(c, dict) else c,
                         "expression": c.get("name", c) if isinstance(c, dict) else c,
                         "type": (c.get("type", "varchar") if isinstance(c, dict) else "varchar")}
                        for c in columns
                    ],
                    "_todo": "补充业务命名、primaryKey、calculated 字段",
                })
            draft = {"catalog": "wren", "schema": "draft", "models": models,
                     "_next": "人工评审 → 补 relationships/metrics/views → mdl_validate_manifest → 部署"}
            print(json.dumps(draft, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
