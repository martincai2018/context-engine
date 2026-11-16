# Context Engine 使用演示

> 环境：Windows 11 原生部署（无 Docker），演示库 TPC-H DuckDB（orders 15,000 行）
> 日期：2026-11-17　复现脚本：`demo_mcp.py`（经 MCP 网络协议调用，非本地函数）

## 1. 演示架构与分工

```
用户中文提问
   │
   ▼
pi-web (:30141) 内的 pi agent
   ├──► context MCP (:9100)  search_context   ← 本演示主角：理解与召回
   │      语料三源：Wren MDL（9001 实时拉取）+ glossary.yaml + golden_sql.yaml
   │      检索：jieba 分词 → BM25 → 同义词扩展 → 模型二次召回 → MDL 关系图扩展
   └──► wren MCP (:9000)     query             ← 翻译与执行
          └─ ibis-server (:8000) → DuckDB
```

分工原则：**Context Engine 回答"该带哪些上下文"，Wren 回答"怎么翻译成 SQL 并执行"**。

## 2. 索引概况（index_stats 真实输出）

```json
{
  "documents": 26,
  "by_type": {"model": 2, "column": 9, "metric": 1, "view": 2,
              "enum": 1, "glossary": 6, "golden_sql": 5},
  "synonyms": 28,
  "relationships": 1
}
```

- 2 个模型（Orders/Customer）、9 个字段、1 指标、2 视图、1 枚举；
- 28 条同义词映射（如 销售额/营收/成交额 → GMV）；
- 1 条关系（CustomerOrders，Customer 1:N Orders）。

## 3. 四个典型问题的检索结果

### Q1 每种订单状态的成交额 GMV 是多少？

```
术语扩展 : ['gmv', '订单状态']
命中模型 : ['Orders', 'Customer']
Join 候选: ['CustomerOrders']
业务口径 : ['GMV', '订单状态']
指标视图 : ['CustomerOrderSummary', 'MonthlyGmv']
Golden SQL: ['每种订单状态的订单量和 GMV', '待处理订单有多少', ...]
```

上下文包节选（实际发给 LLM 的内容，1165 字符 / 预算 6000）：

```markdown
## 业务口径
- GMV：SUM(totalprice)，订单总价之和，未扣退款，币种以源数据为准
- 订单状态：F=已完成 Fulfilled，O=进行中 Open，P=待处理 Pending（禁止解释为 Partial）
## 相关模型与字段
- 模型 Orders（物理表 orders）: orderkey, custkey, orderstatus, totalprice, orderdate, is_completed
## Join 路径（来自 MDL 关系定义，禁止自行猜测外键）
- CustomerOrders: "Customer".custkey = "Orders".custkey (ONE_TO_MANY)
## Golden SQL 参考
SELECT orderstatus, COUNT(DISTINCT orderkey) AS order_count,
       ROUND(SUM(totalprice), 2) AS gmv
FROM "Orders" GROUP BY orderstatus ORDER BY order_count DESC
```

要点：中文"成交额"经同义词映射到 GMV；口径"未扣退款"被自动带入。

### Q2 哪个客户细分市场贡献的销售额最高？

```
术语扩展 : ['gmv', '客户细分市场']
业务口径 : ['客户细分市场', 'GMV', '客户数']
Join 候选: ['CustomerOrders']
Golden SQL: ['各客户细分市场的 GMV 排名', ...]
```

要点：口语"销售额"与"客户细分市场"都被术语表归一化，直接召回排名模板；
模型二次召回机制保证了即使问题是纯中文，英文模型名 Orders/Customer 也能进上下文。

### Q3 帮我看看按月的营收趋势

```
术语扩展 : ['gmv', '月度趋势']
指标视图 : ['MonthlyGmv', 'CustomerOrderSummary', 'orders_by_status']
Golden SQL: ['月度 GMV 和订单量趋势', ...]
```

上下文包还包含规则：`月度趋势：用 DATE_TRUNC('month', orderdate) 分组，注意不完整月份要提示`。
此前的 agent 实测中，模型据此主动识别出"1998-08 只有 2 天数据，不能解读为业务骤降"。

### Q4 待处理的订单有多少？（无同义词命中的兜底场景）

```
术语扩展 : []
业务口径 : ['订单量', '订单状态', '客户数']
Golden SQL: ['待处理订单有多少', ...]
```

要点：问题词面没有命中任何同义词，BM25 直接召回此前沉淀的 golden SQL
（`SELECT COUNT(*) FROM "Orders" WHERE orderstatus = 'P'`）——
**golden SQL 承担了术语表覆盖不到时的兜底职责**。

## 4. 反馈闭环演示（知识库自增长）

通过 MCP 工具 `add_golden_sql` 写入一条新知识：

```python
add_golden_sql(
  question="高价值客户的平均客单价",
  sql='SELECT AVG(gmv) FROM (SELECT custkey, SUM(totalprice) AS gmv FROM "Orders" GROUP BY custkey HAVING SUM(totalprice) > 100000)',
  tags=["客户","客单价","高价值"]
)
```

真实返回：

```json
{"status": "ok", "documents": 26, "golden_sql": 5, "synonyms": 28, "relationships": 1}
```

随即用**完全不同的问法**验证即时生效：

```
search_context("VIP客户平均每单消费多少钱")
→ Golden SQL 命中: ['高价值客户的平均客单价', ...]
```

整个过程无需重启服务：文件落盘 → 索引自动重建 → 新问法可召回。
这就是生产环境 bad case 的标准沉淀路径：纠错 → add_golden_sql → 后续会话受益。

## 5. 完整 Agent 链路（两个 MCP 协作实测）

指令：先 `search_context` 检索"各客户细分市场的订单量"，再用 wren `query` 执行。

Agent 实际生成的 SQL：

```sql
SELECT c.marketsegment,
       COUNT(DISTINCT o.orderkey) AS order_count
FROM "Customer" c
JOIN "Orders" o ON c.custkey = o.custkey
GROUP BY c.marketsegment
ORDER BY order_count DESC;
```

Agent 在回答中明确列出 context 提供的四类关键信息：

1. 术语映射：客户细分市场 → `Customer.marketsegment`（物理列 c_mktsegment）；订单量口径 = COUNT(DISTINCT orderkey)；
2. 模型清单：Orders / Customer 及各自字段；
3. Join 路径：直接取自 MDL 关系 CustomerOrders，**"无需自行猜测外键"**；
4. Golden SQL：参考排名模板改造，去掉本次不需要的 GMV 度量。

## 6. 复现步骤

```bash
cd /c/AI/context-engine
uv sync                                                       # 首次安装依赖

# 方式一：经 MCP 网络协议（本演示使用，要求 9100 服务已启动）
PYTHONUTF8=1 uv run --with "mcp[cli]<2" python demo_mcp.py

# 方式二：不走网络，直接本地函数调用
PYTHONUTF8=1 PYTHONPATH=src uv run python demo.py

# 方式三：先启动 MCP 服务，再在 pi-web (http://127.0.0.1:30141) 自然语言提问
PYTHONUTF8=1 PYTHONPATH=src uv run python -m context_engine.server
```

前置：Wren 三服务（8000/9000/9001）已启动且 9001 上部署了 MDL；
pi 的 `~/.pi/agent/mcp.json` 已注册 context（:9100）与 wren（:9000）两个服务器。

## 7. 当前边界与路线图

| 边界 | 说明 | 规划 |
|---|---|---|
| 纯语义近似问法弱 | BM25 依赖词面重叠，无重叠时召回下降 | LanceDB + bge-small-zh 向量召回，RRF 融合 |
| 无 rerank | 检索结果按 BM25 单路分数排序 | 加 LLM/交叉编码器 rerank |
| golden SQL 靠人工 | 目前手动或 add_golden_sql 写入 | 查询日志自动挖掘高频 SQL |
| 规模未验证 | 当前 26 篇文档的演示规模 | 真实库上百模型/千字段场景压测 |
| 缺命中率评测 | 尚无量化指标 | 建问答回归集，统计上下文命中率与 SQL 正确率 |
