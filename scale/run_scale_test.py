"""百表规模压测：物理发现 → MDL 初稿 → 大规模 schema linking 检索。

不修改 9001 的全局连接配置，用显式 connectionInfo 直调 ibis-server v3 API。
运行： uv run --with httpx --with pyyaml --with jieba --with rank-bm25 \
        PYTHONPATH=src python scale/run_scale_test.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

import shutil

from context_engine import config as cfg
from context_engine import indexer as idx_mod
from context_engine.corpus import _model_docs
from context_engine.indexer import ContextIndex

HERE = Path(__file__).resolve().parent
IBIS = "http://localhost:8000"
DB_DIR = str(HERE / "data").replace("\\", "/")
CONNECTION = {"url": DB_DIR, "format": "duckdb"}  # v2 端点接受扁平结构
METADATA_ENDPOINT = f"{IBIS}/v2/connector/duckdb/metadata/tables"

# 把数仓域术语表注入索引器的数据目录（与生产 glossary.yaml 同构）
GLOSSARY_TMP = HERE / "data" / "glossary_data"
GLOSSARY_TMP.mkdir(parents=True, exist_ok=True)
shutil.copy(HERE / "glossary.scale.yaml", GLOSSARY_TMP / "glossary.yaml")
cfg.DATA_DIR = str(GLOSSARY_TMP)
idx_mod.DATA_DIR = str(GLOSSARY_TMP)

# 域定向问题：期望命中的表名前缀（用于命中率断言）
DOMAIN_QUESTIONS = [
    ("库存周转分析要找哪些表？", "inventory"),
    ("营销优惠券核销情况", "marketing"),
    ("物流配送时效", "logistics"),
    ("供应商采购金额", "supplier"),
    ("财务结算发票", "finance"),
    ("会员等级分布", "customer"),
    ("商品SKU分类", "product"),
    ("订单退款明细", "order"),
    ("GMV 汇总指标在哪个分层", "order"),
    ("宽表里的库存移动记录", "inventory"),
]


def step1_discovery() -> list[dict]:
    print("=" * 68)
    print("步骤 1：物理发现（ibis v2 metadata/tables）")
    t0 = time.perf_counter()
    resp = httpx.post(
        METADATA_ENDPOINT,
        json={"connectionInfo": CONNECTION}, timeout=120,
    )
    elapsed = (time.perf_counter() - t0) * 1000
    resp.raise_for_status()
    tables = resp.json()
    print(f"  发现表数: {len(tables)} | 耗时: {elapsed:.0f}ms")
    sample = tables[0]
    print(f"  样例结构: {sample['name']}，列数 {len(sample.get('columns', []))}")
    assert len(tables) >= 120
    return tables


def step2_mdl_draft(tables: list[dict]) -> dict:
    print("=" * 68)
    print("步骤 2：生成 MDL 初稿（模拟 bootstrap_mdl 流水线）")
    t0 = time.perf_counter()
    models = []
    for t in tables:
        full = t["name"]
        schema, table = full.split(".", 1) if "." in full else (None, full)
        ref = {"schema": schema, "table": table} if schema else {"table": table}
        models.append({
            "name": table,
            "tableReference": ref,
            "columns": [
                {"name": c["name"], "expression": c["name"],
                 "type": (c.get("type") or "varchar").lower()}
                for c in t.get("columns", [])
            ],
        })
    mdl = {"catalog": "wren", "schema": "scale", "dataSource": "duckdb",
           "models": models, "relationships": [], "metrics": [], "views": []}
    elapsed = (time.perf_counter() - t0) * 1000
    draft_path = HERE / "scale_draft.mdl.json"
    draft_path.write_text(json.dumps(mdl, ensure_ascii=False, indent=2), encoding="utf-8")
    total_cols = sum(len(m["columns"]) for m in models)
    print(f"  模型数: {len(models)} | 总字段: {total_cols} | 生成耗时: {elapsed:.0f}ms")
    print(f"  初稿已写: {draft_path}")
    return mdl


def step3_scale_retrieval(mdl: dict) -> None:
    print("=" * 68)
    print("步骤 3：百表 schema linking 检索压测")
    docs = _model_docs(mdl)
    t0 = time.perf_counter()
    index = ContextIndex().build(docs, mdl)
    build_ms = (time.perf_counter() - t0) * 1000
    print(f"  索引文档: {len(docs)} | 构建耗时: {build_ms:.0f}ms")

    # 检索延迟（生产同款：类型化混合检索，只在 model 文档内排名）
    latencies = []
    domain_hits = 0
    for q, domain in DOMAIN_QUESTIONS:
        t0 = time.perf_counter()
        hits, mode = index.hybrid_search_typed(q, ("model",), 10)
        latencies.append((time.perf_counter() - t0) * 1000)
        hit_names = [index.docs[i].payload["name"] for i, _ in hits]
        domain_tables = [n for n in hit_names if f"_{domain}_" in n]
        ok = bool(domain_tables)
        domain_hits += ok
        top3 = hit_names[:3]
        print(f"  [{'✓' if ok else '✗'}] {q:<22} 模式={mode:<12} top3={top3}")

    print("-" * 68)
    print(f"  域定向命中率: {domain_hits}/{len(DOMAIN_QUESTIONS)} "
          f"= {domain_hits/len(DOMAIN_QUESTIONS):.0%}")
    print(f"  单次检索延迟 p50={sorted(latencies)[len(latencies)//2]:.0f}ms "
          f"max={max(latencies):.0f}ms")

    # top-k 截断分析：120 个模型时 top10 只覆盖 8.3%
    print(f"  top-10 覆盖模型比例: {10/len(mdl['models']):.1%}（截断风险随表数上升）")


def main() -> None:
    tables = step1_discovery()
    mdl = step2_mdl_draft(tables)
    step3_scale_retrieval(mdl)
    print("=" * 68)
    print("压测完成。结论与建议见 SCALE_TEST.md")


if __name__ == "__main__":
    main()
