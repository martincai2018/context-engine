# Context Engine（Data Agent 上下文引擎）

> 📖 **完整使用手册与验证案例集（推荐先读）：[USAGE.md](USAGE.md)**
> 📺 检索专项演示（含真实输出与复现步骤）：[DEMO.md](DEMO.md)
> 🧠 团队记忆机制设计：[TEAM_MEMORY.md](TEAM_MEMORY.md)

轻量级自研 context engine，以独立 MCP server 形态与 Wren 引擎平级接入 pi agent。
回答"本次提问该带哪些上下文"：从 Wren MDL、业务术语表、golden SQL 中检索并组装
受 token 预算约束的上下文包。

## 架构

```
用户问题
 → 同义词扩展（data/glossary.yaml，口语词→规范术语）
 → BM25 混合召回（jieba 中文分词，模型/字段/指标/视图/枚举/术语/golden SQL 统一索引）
 → 模型二次召回 + golden SQL 模型引用解析（解决中文问题匹配不到英文模型名）
 → MDL 关系图扩展（自动补 join 候选，禁止 agent 猜外键）
 → 上下文包组装（字符预算 6000，可 CONTEXT_CHAR_BUDGET 调整）
```

与 Wren 的分工：**context engine 负责"理解和召回"，Wren 负责"翻译和执行"**。

## 目录

```
src/context_engine/
  config.py      环境变量配置
  corpus.py      三类语料 → 统一 Doc（MDL 实时拉自 http://localhost:9001/mdl）
  indexer.py     jieba 分词、BM25、同义词表、MDL 关系图
  retriever.py   检索 + 关系扩展 + 上下文包渲染
  server.py      FastMCP（streamable HTTP，:9100/mcp）
data/
  glossary.yaml  业务术语与同义词（人工维护，Git 评审）
  golden_sql.yaml  verified 问-SQL 对（人工或 add_golden_sql 工具写入）
scripts/
  bootstrap_mdl.py  语义库流水线①：物理库探查 → MDL 初稿
demo.py          不经过网络的本地检索演示
```

## 运行

```bash
uv sync
PYTHONUTF8=1 PYTHONPATH=src uv run python demo.py          # 本地检索演示
PYTHONUTF8=1 PYTHONPATH=src uv run python -m context_engine.server   # 启动 MCP :9100
```

## MCP 工具（4 个）

| 工具 | 作用 |
|---|---|
| `build_index()` | 从 Wren MDL + glossary + golden SQL 重建索引 |
| `search_context(question, top_k)` | 主检索，返回结构化上下文包与 context_text |
| `add_golden_sql(question, sql, tags)` | 反馈闭环：追加 verified SQL 并自动重建索引 |
| `index_stats()` | 索引文档统计 |

pi 注册（~/.pi/agent/mcp.json）：

```json
{
  "mcpServers": {
    "wren":    { "url": "http://localhost:9000/mcp" },
    "context": { "url": "http://localhost:9100/mcp" }
  }
}
```

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| WREN_UI_URL | http://localhost:9001 | MDL 来源 |
| CONTEXT_MCP_PORT | 9100 | MCP 端口 |
| CONTEXT_CHAR_BUDGET | 6000 | 上下文包字符预算 |
| CONTEXT_DATA_DIR | ./data | 术语/golden 文件目录 |

## 混合检索（BM25 + 向量 + RRF）

安装可选向量依赖后自动启用（未装时优雅降级为纯 BM25）：

```bash
uv sync --extra vector                 # fastembed(ONNX) + LanceDB，首次运行下载 bge-small-zh-v1.5
uv run python evals/run_eval.py        # 回归评测（8 用例 × 5 维度）
CONTEXT_VECTOR=0 uv run python evals/run_eval.py   # 纯 BM25 基线对比
```

实测（evals/questions.yaml，含 4 条无词面重叠的语义近似问法）：

| 模式 | 综合命中率 | 典型失败 |
|---|---|---|
| hybrid（BM25+向量+RRF） | **100%** | — |
| 纯 BM25 | 95.5% | "客户花钱的总规模"漏召回 GMV 口径 |

关键环境变量：`CONTEXT_VECTOR=0` 关闭向量、`CONTEXT_EMBED_MODEL` 换模型、
`CONTEXT_BM25_WEIGHT`/`CONTEXT_VECTOR_WEIGHT` 调 RRF 权重。

## 语义库建模流水线

`scripts/bootstrap_mdl.py` 调 Wren MCP 的 `list_remote_tables`（响应已内联列信息），
自动产出 MDL 初稿（模型 + 物理列 + 类型 + notNull），人工补业务命名/关系/指标后走评审部署：

```bash
uv run --with "mcp[cli]<2" python scripts/bootstrap_mdl.py > draft.mdl.json
```

## 路线图

- [x] 向量召回（LanceDB + bge-small-zh，与 BM25 做 RRF 融合）
- [x] 上下文命中率回归评测（evals/，混合 100% vs BM25 95.5%）
- [x] 物理库自动发现 → MDL 初稿（bootstrap_mdl.py）
- [ ] LLM rerank
- [ ] 从查询日志自动挖掘 golden SQL
- [ ] 真实库百模型/千字段规模压测
