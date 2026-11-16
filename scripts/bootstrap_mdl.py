"""语义库搭建流水线 ①：从 Wren 已连接的物理库自动生成 MDL 初稿。

list_remote_tables 的响应已内联每张表的列信息（name/type/notNull），
直接映射为 MDL 模型；人工再补业务命名、primaryKey、relationships/metrics。
用法： uv run --with "mcp[cli]<2" python scripts/bootstrap_mdl.py > draft.mdl.json
"""
from __future__ import annotations

import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

WREN_MCP = os.getenv("WREN_MCP_URL", "http://localhost:9000/mcp")


def _split_table(full_name: str) -> dict:
    parts = full_name.split(".", 1)
    return {"schema": parts[0], "table": parts[1]} if len(parts) == 2 else {"table": full_name}


async def main() -> None:
    async with streamablehttp_client(WREN_MCP) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()

            raw = (await session.call_tool("list_remote_tables", {})).content[0].text
            remote = json.loads(raw)
            data_source = (await session.call_tool(
                "get_current_data_source_type", {})).content[0].text.strip('"')

            models = []
            for item in remote:
                full_name = item["name"] if isinstance(item, dict) else str(item)
                ref = _split_table(full_name)
                cols_in = item.get("columns", []) if isinstance(item, dict) else []
                columns = [{
                    "name": c["name"],
                    "expression": c["name"],
                    "type": (c.get("type") or "varchar").lower(),
                    "notNull": bool(c.get("notNull")),
                } for c in cols_in]
                models.append({
                    "name": ref["table"],
                    "tableReference": ref,
                    "columns": columns,
                    "_todo": "业务命名/同义词、primaryKey、calculated 字段",
                })

            draft = {
                "catalog": "wren",
                "schema": "draft",
                "dataSource": data_source,
                "models": models,
                "_next": "人工评审 → 补 relationships/metrics/views/enumDefinitions "
                         "→ mdl_validate_manifest 校验 → 9001 部署",
            }
            print(json.dumps(draft, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
