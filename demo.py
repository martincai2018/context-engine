"""端到端演示：不经过 MCP 网络，直接验证检索效果（中文问题 → 上下文包）。"""
from context_engine.corpus import build_corpus
from context_engine.indexer import ContextIndex
from context_engine.retriever import Retriever


def main() -> None:
    docs, mdl = build_corpus()
    index = ContextIndex().build(docs, mdl)
    retriever = Retriever(index)
    print("=== 索引统计 ===")
    print(index.stats())

    questions = [
        "每种订单状态的成交额 GMV 是多少？",
        "哪个客户细分市场贡献的销售额最高？",
        "帮我看看按月的营收趋势",
    ]
    for q in questions:
        packet = retriever.search(q)
        print("\n" + "=" * 70)
        print("问题:", q)
        print("术语扩展:", packet["synonym_expansions"])
        print("命中模型:", [m["name"] for m in packet["models"] if "tableReference" in m])
        print("Join 候选:", [r["name"] for r in packet["join_candidates"]])
        print("Golden SQL:", [g["question"] for g in packet["golden_sql"]])
        print("-" * 70)
        print(packet["context_text"])
        print(f"[字符数 {packet['char_count']} / 预算 {packet['budget']}]")


if __name__ == "__main__":
    main()
