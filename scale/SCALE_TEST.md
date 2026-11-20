# 百表规模压测报告（120 表 / 2322 字段）

> 目的：真实 Doris 就绪前，在本地仿真 EDW 上验证"物理发现 → MDL 建模 → 大规模 schema linking"全链路。
> 数据：`generate_warehouse.py` 生成的 120 张表（ods 24/dwd 48/dws 30/dim 18，8 业务域，53 张宽表）。

## 1. 三段链路的实测表现

| 阶段 | 结果 | 耗时 | 结论 |
|---|---|---|---|
| ① 物理发现（ibis v2 metadata） | 120 表全部发现，含完整列结构 | ~1.0s | 发现能力不随表数退化 |
| ② MDL 初稿生成 | 120 模型 / 2322 字段 | ~1ms（本地转换） | 自动化可行，人工只需补业务语义 |
| ③ 索引构建（2442 篇 Doc，含向量） | 成功 | **~150s（一次性）** | 嵌入是主要成本，增量更新可摊薄 |
| ③ 单次检索延迟 | p50 23ms / max 51ms | 毫秒级 | 检索本身无规模压力 |

## 2. 关键发现：规模放大后检索质量崩塌与修复

### 初版结果：域定向命中率仅 50%

直接复用演示规模的全局混合检索，10 个域问题中 5 个 top10 完全没有目标表，
诊断发现 top 命中 **100% 是 column 文档**——2322 篇列文档把 120 篇模型文档完全淹没，
部分问题甚至零命中（BM25 零分截断 + 向量被列文档挤占）。

### 两个修复

1. **类型化混合检索 `hybrid_search_typed`**：模型选择只在 model 类型文档内做
   BM25 与向量双路 RRF，列文档不参与表级排名；生产 `Retriever` 已切换到该路径。
2. **数仓域语义词典 `glossary.scale.yaml`**：14 组中文业务词 → 表名英文词元
   （库存→inventory/stock、优惠券→coupon、汇总层→dws 等），查询期扩展。

### 修复后：域定向命中率 100%

| 问题（示例） | top3 命中 |
|---|---|
| 库存周转分析要找哪些表？ | dwd_inventory_stock、dws_inventory_stock_3、…stock_movement_4 |
| 营销优惠券核销情况 | dwd_marketing_campaign、…coupon_3 |
| 会员等级分布 | dwd_customer_member、dwd_customer_member_level |
| 商品SKU分类 | dwd_product_sku、dim_product_sku、ods_product_sku |

## 3. 仍然存在的精度问题（rerank 的必要性证据）

命中率到 100% 不等于排序精准：部分问题的 top3 混入跨域表
（如"供应商采购"第 2 名是 dws_order_order，"GMV 分层"前两名是库存/财务表）。
原因是仿真表大量复用通用列名（customer_id、amount、attr_xx），语义区分度低。
这正是路线图中 **bge-reranker-v2-m3 精排**要解决的问题：粗召回取宽 → cross-encoder 重排。

另一个结构性事实：top-10 在 120 模型中只覆盖 8.3%，表数继续增长时，
"先域过滤再表检索"（metadata 过滤 + 分层路由）会比单纯调大 top-k 更有效。

## 4. 过程中踩的环境坑

- **DuckDB 文件版本不兼容**：context-engine 环境的 duckdb 较新（文件版本 999），
  ibis-server 1.3.2 只读 64–66。规模库必须用 ibis venv 的 Python 生成
  （`/c/AI/wren-engine/ibis-server/.venv/Scripts/python.exe`）。接真实 Doris 后此问题消失。
- 元数据接口用 **v2** 端点（MCP 同款），v3 的 DTO 形状不同会 422。

## 5. 对真实 Doris 接入的直接指导

1. 连接器已原生支持（9001 选 DORIS，host/9030 端口/database/user/password）；
2. 建只读账号后，先用 `bootstrap_mdl.py` 跑全量发现，预期百表级秒回；
3. **必须同步建业务域语义词典**（本报告的 14 组模板可直接改写），否则中文问法在英文表名上重蹈 50% 覆辙；
4. 表级检索走 `hybrid_search_typed`，不要让列文档参与表排名；
5. 接入后第一周收集真实问题做域命中率基线，再决定 rerank 上线优先级；
6. 大规模库的索引构建走增量（按变更的模型重建对应 Doc），避免每次全量 150s。

## 6. 复现命令

```bash
# 1. 生成规模库（必须用 ibis venv 的 duckdb）
/c/AI/wren-engine/ibis-server/.venv/Scripts/python.exe scale/generate_warehouse.py
# 2. 三段压测（发现/建模/检索）
PYTHONPATH=src uv run --with httpx --with pyyaml --with jieba \
  --with rank-bm25 --with fastembed --with lancedb python scale/run_scale_test.py
```
