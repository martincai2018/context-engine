"""Context Engine 现场演示：经 MCP 协议（HTTP :9100）调用，展示完整检索过程。

运行： uv run --with "mcp[cli]<2" python demo_mcp.py
"""
import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_URL = "http://localhost:9100/mcp"
QUESTIONS = [
    "每种订单状态的成交额 GMV 是多少？",
    "哪个客户细分市场贡献的销售额最高？",
    "帮我看看按月的营收趋势",
    "待处理的订单有多少？",
]


async def main() -> None:
    async with streamablehttp_client(MCP_URL) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()

            stats = json.loads((await s.call_tool("index_stats", {})).content[0].text)
            print("=" * 72)
            print(f"索引概况: {stats}")

            for q in QUESTIONS:
                packet = json.loads(
                    (await s.call_tool("search_context", {"question": q})).content[0].text
                )
                print("\n" + "=" * 72)
                print(f"提问: {q}")
                print(f"  术语扩展 : {packet['synonym_expansions']}")
                print(f"  命中模型 : {[m['name'] for m in packet['models'] if 'tableReference' in m]}")
                print(f"  Join 候选: {[j['name'] for j in packet['join_candidates']]}")
                print(f"  业务口径 : {[g['term'] for g in packet['glossary']]}")
                print(f"  指标视图 : {[mv.get('name') for mv in packet['metrics_views']]}")
                print(f"  Golden SQL: {[g['question'] for g in packet['golden_sql']]}")
                print("-" * 72)
                print(packet["context_text"])
                print(f"[上下文 {packet['char_count']} 字符 / 预算 {packet['budget']}]")


if __name__ == "__main__":
    asyncio.run(main())
