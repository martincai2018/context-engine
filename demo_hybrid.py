"""混合检索验证：重点测试无词面重叠的语义近似问法（BM25 弱项、向量强项）。"""
from context_engine.corpus import build_corpus
from context_engine.indexer import ContextIndex
from context_engine.retriever import Retriever


def main() -> None:
    docs, mdl = build_corpus()
    index = ContextIndex().build(docs, mdl)
    print("向量状态:", index.vector.stats())
    retriever = Retriever(index)

    # 这些问法刻意避开术语原词，纯靠语义理解召回
    semantic_questions = [
        "客户花钱的总规模有多大？",        # 期望召回 GMV/Orders
        "生意最好的客群是哪一类？",        # 期望召回客户细分市场/CustomerOrderSummary
        "一年里每个月的业绩走向",          # 期望召回 MonthlyGmv/月度趋势
        "有多少单子还在处理中？",          # 期望召回待处理订单 golden SQL
    ]
    for q in semantic_questions:
        pkt = retriever.search(q)
        print("\n" + "=" * 68)
        print("提问:", q, f"[模式: {pkt['retrieval_mode']}]")
        print("  模型  :", [m["name"] for m in pkt["models"] if "tableReference" in m])
        print("  视图  :", [mv.get("name") for mv in pkt["metrics_views"]])
        print("  Golden:", [g["question"] for g in pkt["golden_sql"]])
        print("  口径  :", [g["term"] for g in pkt["glossary"]])


if __name__ == "__main__":
    main()
