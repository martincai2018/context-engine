"""团队记忆闭环演示（不经过 MCP 网络，直接验证 Mem0 读写与晋升）。

运行： PYTHONUTF8=1 PYTHONPATH=src uv run python demo_memory.py
前置： Ollama 已启动（phi4 + bge-m3），已执行 scripts/seed_team_memories.py
"""
from memory import store


def main() -> None:
    print("== 1. 团队库统计 ==")
    print(store.stats())

    print("\n== 2. 经验召回：提问一个新问题 ==")
    hits = store.recall("Windows 上服务启动就退出是什么原因？", scope="team", limit=3)
    for h in hits:
        print(f"  [{h.get('metadata', {}).get('kind')}] {h.get('memory', '')[:80]}")

    print("\n== 3. 个人经验写入（模拟开发者踩坑后的沉淀） ==")
    store.remember(
        "ClickHouse 分布式表查询必须带集群名，否则只命中单分片；数仓评审时已确认写入规范。",
        scope="user", kind="lesson",
    )
    mine = store.recall("ClickHouse 查询规范", scope="user", limit=2)
    print("  个人库召回:", [m["memory"][:40] for m in mine])

    print("\n== 4. 晋升为团队记忆（评审通过后） ==")
    if mine:
        mid = mine[0]["id"]
        store.promote_to_team(mid)
        team = store.recall("ClickHouse", scope="team", limit=2)
        print("  团队库现在可召回:", [m["memory"][:40] for m in team])

    print("\n== 5. 全作用域召回（团队+个人） ==")
    all_hits = store.recall("订单状态 P 的含义", scope="all", limit=3)
    for h in all_hits:
        print(f"  - {h.get('memory', '')[:70]}")


if __name__ == "__main__":
    main()
