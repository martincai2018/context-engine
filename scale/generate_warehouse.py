"""百表级仿真数仓生成器：模拟国内 EDW 分层（ods/dwd/dws/dim），8 个业务域。

产出：scale/data/scale_warehouse.duckdb（120 张表，含列注释，少量数据行）
用途：在真实 Doris 就绪前，验证物理发现→MDL 初稿→大规模检索的全链路表现。
运行： uv run --with duckdb python scale/generate_warehouse.py
"""
from __future__ import annotations

import random
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
DB_DIR = HERE / "data"
DB_PATH = DB_DIR / "scale_warehouse.duckdb"

# 业务域：(域英文, 中文主题, 实体词)
DOMAINS = [
    ("order", "订单交易", ["order", "order_item", "order_refund"]),
    ("customer", "客户会员", ["customer", "member", "member_level"]),
    ("product", "商品SKU", ["product", "sku", "category"]),
    ("inventory", "库存仓储", ["stock", "stock_movement", "warehouse"]),
    ("finance", "财务结算", ["settlement", "invoice", "payment"]),
    ("marketing", "营销优惠券", ["coupon", "campaign", "coupon_use"]),
    ("logistics", "物流配送", ["shipment", "delivery", "express_route"]),
    ("supplier", "供应商采购", ["supplier", "purchase", "purchase_item"]),
]

# 每层的表数量配比（合计 120）
LAYER_PLAN = {"ods": 24, "dwd": 48, "dws": 30, "dim": 18}
COMMON_TS = ["created_at", "updated_at", "dt"]


def build_columns(layer: str, entity: str, idx: int) -> list[tuple[str, str, str]]:
    """返回 (物理列名, 类型, 中文注释)。"""
    cols: list[tuple[str, str, str]] = []
    if layer == "dim":
        cols = [
            (f"{entity}_id", "BIGINT", "主键ID"),
            (f"{entity}_name", "VARCHAR", "名称"),
            ("status", "VARCHAR", "状态枚举"),
            ("create_time", "TIMESTAMP", "创建时间"),
        ]
    elif layer == "ods":
        cols = [
            ("id", "BIGINT", "自增主键"),
            (f"{entity}_no", "VARCHAR", "业务单据号"),
            ("src_sys_code", "VARCHAR", "来源系统编码"),
            ("raw_payload", "VARCHAR", "原始报文"),
        ]
    else:  # dwd / dws
        candidate = [
            (f"{entity}_id", "BIGINT", f"{entity}主键"),
            ("customer_id", "BIGINT", "客户ID（外键）"),
            ("product_id", "BIGINT", "商品ID（外键）"),
            ("amount", "DECIMAL(18,2)", "金额"),
            ("quantity", "INTEGER", "数量"),
            ("order_status", "VARCHAR", "订单状态"),
        ]
        cols = []
        for name, typ, comment in candidate:
            if not any(c[0] == name for c in cols):
                cols.append((name, typ, comment))
        if layer == "dws":
            cols += [
                ("gmv_sum", "DECIMAL(18,2)", "GMV汇总"),
                ("order_cnt", "BIGINT", "订单数汇总"),
                ("buyer_cnt", "BIGINT", "买家数汇总"),
            ]
    # 填充到 8-25 列的宽度，模拟宽表（先按列名去重，避免实体主键与通用外键碰撞）
    rng = random.Random(idx)
    target = rng.randint(8, 25)
    seen = {c[0] for c in cols}
    i = 0
    while len(cols) < target:
        col_name = f"attr_{i:02d}"
        if col_name not in seen:
            cols.append((col_name, "VARCHAR", f"扩展属性{i}"))
            seen.add(col_name)
        i += 1
    for ts in COMMON_TS:
        if ts == "dt":
            cols.append((ts, "DATE", "分区日期"))
        elif not any(c[0] == ts for c in cols):
            cols.append((ts, "TIMESTAMP", ts))
    return cols


def main() -> None:
    random.seed(42)
    DB_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = duckdb.connect(str(DB_PATH))

    table_idx = 0
    catalog: list[dict] = []
    for layer, count in LAYER_PLAN.items():
        per_domain = count // len(DOMAINS)
        for d_idx, (domain, cn, entities) in enumerate(DOMAINS):
            n = per_domain + (1 if d_idx < count % len(DOMAINS) else 0)
            for k in range(n):
                entity = entities[k % len(entities)]
                table = f"{layer}_{domain}_{entity}"
                if table in {t["table"] for t in catalog}:
                    table = f"{table}_{k}"
                cols = build_columns(layer, entity, table_idx)
                col_def = ", ".join(f'"{c[0]}" {c[1]}' for c in cols)
                con.execute(f'CREATE TABLE "{table}" ({col_def})')
                # 写 2-5 行数据，保证元数据与轻量查询可用
                placeholders = ", ".join(["?"] * len(cols))
                for r in range(random.randint(2, 5)):
                    row = []
                    for name, typ, _ in cols:
                        if typ == "BIGINT" or typ == "INTEGER":
                            row.append(r + 1)
                        elif typ.startswith("DECIMAL"):
                            row.append(round(random.uniform(1, 9999), 2))
                        elif typ == "DATE":
                            row.append("2026-11-01")
                        elif typ == "TIMESTAMP":
                            row.append("2026-11-01 10:00:00")
                        else:
                            row.append(f"{name}_v{r}")
                    con.execute(f'INSERT INTO "{table}" VALUES ({placeholders})', row)
                catalog.append({"table": table, "layer": layer, "domain": domain,
                                "domain_cn": cn, "columns": len(cols)})
                table_idx += 1

    con.execute(f"EXPORT DATABASE '{DB_DIR / 'schema_export'}' (FORMAT PARQUET)")
    total = con.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='main'").fetchone()[0]
    wide = sum(1 for t in catalog if t["columns"] >= 20)
    print(f"生成表数: {total}（目标 120）| 宽表(>=20列): {wide}")
    from collections import Counter
    print("分层分布:", dict(Counter(t["layer"] for t in catalog)))
    print("分域分布:", dict(Counter(t["domain"] for t in catalog)))
    print("DB 路径:", DB_PATH)


if __name__ == "__main__":
    main()
