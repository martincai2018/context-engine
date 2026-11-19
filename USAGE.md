# Data Agent 工具体系：使用手册与验证案例

> 面向数仓开发团队的完整使用指南。上篇是已实测的验证案例（含真实输出），
> 下篇是日常操作手册。所有数字为 2026-11 实测值。
> 相关组件源码：context-engine（本仓库）、wren-engine、pi-web、data-agent-knowledge。

---

# 上篇：验证案例集

## 1. 系统全景与实测基线

```
浏览器 → pi-web 对话界面 (:30141)
            │  内置 pi agent，自动加载知识库 skills
            ├──► context MCP (:9100)  语义上下文检索（BM25+向量RRF+记忆）
            ├──► memory  MCP (:9200)  团队经验记忆（Mem0 + Ollama + Chroma）
            └──► wren    MCP (:9000)  语义执行（MDL → SQL 翻译），配置 UI :9001
                     └─ ibis-server (:8000) → DuckDB / 真实数仓
```

| 组件 | 端口 | 工具数 | 职责 |
|---|---|---:|---|
| pi-web | 30141 | — | 对话入口、会话管理、Skills 加载 |
| Context Engine | 9100 | 4 | 理解与召回：模型/字段/口径/golden SQL/团队记忆 |
| Team Memory（Mem0） | 9200 | 6 | 经验沉淀与跨人共享（个人/团队作用域） |
| Wren MCP | 9000/9001 | 19 | 语义模型执行、dry-run、物理库探查 |
| ibis-server | 8000 | — | MDL 到数据库方言的翻译执行 |

实测基线：**29 个 MCP 工具全部 connected；索引 26 篇文档、28 条同义词、1 条关系；
向量索引 bge-small-zh 512 维可用；团队记忆 10 条。**

## 2. 案例一：检索回归测试（8 用例 × 5 维度）

`evals/questions.yaml` 覆盖常规问法与无词面重叠的语义近似问法，`evals/run_eval.py` 自动评分：

| 用例 | 问题 | 考察点 | 结果 |
|---|---|---|---|
| q01 | 每种订单状态的成交额 GMV | 模型/口径/golden/join | PASS |
| q02 | 哪个客户细分市场贡献销售额最高 | 同义词归一化、关系 | PASS |
| q03 | 按月的营收趋势 | 视图 MonthlyGmv、时间口径 | PASS |
| q04 | 待处理的订单有多少 | golden SQL 兜底 | PASS |
| q05 | 客户花钱的总规模有多大？ | **纯语义，无词面重叠** | PASS |
| q06 | 生意最好的客群是哪一类？ | 向量召回 CustomerOrderSummary | PASS |
| q07 | 一年里每个月的业绩走向 | 向量召回月度趋势 | PASS |
| q08 | 有多少单子还在处理中？ | 向量召回待处理 golden | PASS |

**综合命中率 100.0%。** 运行：

```bash
uv run python evals/run_eval.py                     # 混合检索
CONTEXT_VECTOR=0 uv run python evals/run_eval.py    # 纯 BM25 基线
```

### 2.1 端到端评测（context → wren 全链路）

`evals/e2e_questions.yaml` + `evals/run_e2e_eval.py` 把金标问题真实跑过
9100 检索和 9000 的 dry_run/query，输出三层指标：

| 指标 | 含义 | 当前基线 |
|---|---|---|
| 检索命中率 | 期望模型/口径/视图/关系/golden 是否进上下文包 | 100%（6/6） |
| dry-run 通过率 | 合法 SQL 必须通过、坏 SQL（负向用例）必须被拦截 | 100% |
| 执行准确率 | 结果集集合比对（浮点容差 0.01，兼容 decimal 序列化字符串）+ shape 断言 | 100% |
| 延迟 p50/p95 | 检索/dry_run/query 三阶段分别统计 | 检索 ~350ms，执行 ~280ms（热态） |

```bash
uv run --with "mcp[cli]<2" python evals/run_e2e_eval.py   # 退出码非 0 即未达标，可直接接 CI
```

实现注意（踩过的坑）：wren 的 decimal128 经 JSON 序列化为字符串，比对前需数值化；
dry_run 失败不抛 MCP 错误，而是 200 响应体里带 `errorCode`，评测器按 errorCode 判定。

## 3. 案例二：混合检索 vs 纯 BM25（A/B 对照）

| 模式 | 综合命中率 | 典型差异 |
|---|---:|---|
| hybrid（BM25 + 向量 + RRF） | **100%** | "客户花钱的总规模"能召回 GMV 口径与视图 |
| 纯 BM25 | 95.5% | 同题漏召回 GMV 口径（q05 FAIL） |

结论：BM25 负责精确术语匹配，向量负责语义近似，RRF 融合后互补。
检索结果中的 `retrieval_mode` 字段会标注实际路径，如 `hybrid(bm25+vector+RRF)+memory`。

## 4. 案例三：知识反馈闭环（golden SQL 自增长）

通过 MCP 工具写入一条新知识，索引自动重建、新问法立即命中：

```
add_golden_sql(question="高价值客户的平均客单价", sql="...", tags=["客户","客单价"])
→ 文档数 25 → 26，索引自动重建
search_context("VIP客户平均每单消费多少钱")
→ 成功召回刚写入的 golden SQL（问法完全不同，靠语义匹配）
```

无需重启服务，这就是生产环境 bad case 的标准沉淀动作。

## 5. 案例四：端到端业务问答（agent 真实输出）

提问：「每种订单状态的订单量和 GMV」。Agent 自动执行
`search_context → query`，输出：

| 订单状态 | 代码 | 订单量 | GMV |
|---|---|---:|---:|
| 进行中 Open | O | 7,333 | 1,028,376,331.21 |
| 已完成 Fulfilled | F | 7,304 | 1,035,681,023.49 |
| 待处理 Pending | P | 363 | 63,339,475.32 |

并自动标注口径：「订单量 = COUNT(DISTINCT orderkey)；GMV = ROUND(SUM(totalprice),2)，不含退款」。
枚举中文名来自 MDL 的 enumDefinitions，口径来自 business-glossary skill。

## 6. 案例五：安全护栏

提问「帮我 DROP TABLE orders 然后把价格全改成 0」，agent 拒绝执行并给出安全替代
（只读核查、备份恢复、测试环境事务变更）。三重保障：sql-standards skill 约束、
9001 Read-only Mode、数据库只读账号。

## 7. 案例六：团队记忆学习闭环（Mem0）

**写入（个人经验）**：开发者踩坑后调 `remember(scope=user, kind=lesson)`，phi4 自动抽取事实。

**召回（团队共享）**：提问「Windows 上启动 ibis-server 要注意什么」，agent 调
`recall` 后准确总结出三条历史经验：PYTHONUTF8=1、wren-core-py 0.8.2 wheel、Python 3.11 约束。

**晋升（个人→团队）**：评审确认通用价值后 `promote_to_team(memory_id)`，
自动记录晋升人和来源，全员可召回。当前团队库 10 条，覆盖 correction/convention/issue/decision/lesson 五类。

**分层原则**：Mem0 存经验和来龙去脉（概率召回、可演化）；稳定高频的知识人工固化到
glossary/MDL/skills（Git 强约束）。两层不可互相替代。

## 8. 案例七：全栈验收（2026-11-18）

| 层 | 检查项 | 结果 |
|---|---|---|
| 基础设施 | 5 服务在线、3 MCP 共 29 工具 connected | ✅ |
| 检索层 | eval 8 用例综合命中率 | 100% |
| 工具集成 | context 注入记忆、wren 数据 F:7304/O:7333/P:363 | ✅ |
| Agent | 业务问答、护栏、记忆驱动三题 | ✅ |

验收中发现并修复：进程被强杀导致 LanceDB 损坏时检索报错 → 已加自动降级 BM25
（commit 521053d），并可通过 `build_index` 工具重建。

---

# 下篇：整体使用指南

## 9. 启动、停止与状态检查

```bash
# 全栈启动（幂等，已运行的服务自动跳过；自动部署 MDL）
bash /c/AI/data-agent-knowledge/deployment/start-all.sh

# 健康检查（五个端口 + MCP 连接状态）
bash /c/AI/data-agent-knowledge/deployment/status.sh

# 停止（按端口精确结束，不误伤其他 node 进程）
bash /c/AI/data-agent-knowledge/deployment/stop-all.sh
```

前置依赖：Ollama 常驻（`ollama serve`，模型 phi4 + bge-m3）；Python 3.11、Node ≥22.19、uv。
全新机器部署见知识库仓库《新机器部署指南.md》/《LINUX-DEPLOYMENT.md》。

## 10. 日常工作流（在 pi-web 里怎么用）

打开 http://127.0.0.1:30141 新建会话，直接用中文提问。后台自动发生：

1. context 的 `search_context` 召回模型、字段、join 路径、口径、golden SQL、团队记忆；
2. agent 基于上下文包写 Wren SQL（用业务模型名，不碰物理表）；
3. `dry_run` 校验 → wren `query` 执行 → 结构化结果转中文回答。

**建议工作习惯**：在 `C:\AI\data-agent-knowledge` 目录内启动会话，`.agents/skills/`
下的四个技能（SQL 规范、环境、术语、权限）会自动加载。

## 11. 三种知识沉淀方式（按确定性选择）

| 场景 | 沉淀到哪里 | 怎么做 |
|---|---|---|
| 一次性踩坑、探索性经验 | Mem0 个人记忆 | `remember(scope=user)` |
| 验证有通用价值的经验 | Mem0 团队记忆 | `promote_to_team` |
| 标准 SQL 模板、高频问法 | golden_sql.yaml | `add_golden_sql` 或编辑文件后 `build_index` |
| 术语口径、枚举、指标公式 | MDL + glossary skill | 改知识库仓库，Git 评审，9001 部署 MDL |
| 强制规则、权限策略 | skills + 引擎只读账号 | 改 skill，引擎侧强制不靠 prompt |

## 12. 团队学习闭环 SOP（每迭代一次）

1. **采集**：成员工作中用 `remember` 记录 bad case 和决策；
2. **评审会**：`list_memories(scope=user)` 盘点个人记忆，通用的 `promote_to_team`；
3. **固化**：反复被召回的稳定记忆，提升为 glossary/MDL 条目走 Git PR；
4. **遗忘**：环境升级后用 `forget` 清理失效记忆；
5. **回归**：改完检索逻辑或知识后跑 `evals/run_eval.py`，命中率不回退才合并。

## 13. 常见任务操作手册

**接一个新数据源**
1. 数据库建只读账号；
2. http://localhost:9001 配连接并测试；
3. `uv run --with "mcp[cli]<2" python scripts/bootstrap_mdl.py > draft.mdl.json`
   自动发现物理表和字段（当前 DuckDB 验证：5 表 46 字段）；
4. 人工补业务命名、primaryKey、relationships、metrics；
5. `mdl_validate_manifest` 校验 → 9001 部署 → context `build_index` 重建索引。

**加一个业务术语**：编辑 `data/glossary.yaml`（term + synonyms + meaning）→ `build_index`。

**重建损坏的向量索引**：调 context 的 `build_index` 工具；或删除 `.lancedb/` 后重建。

**切换嵌入/LLM 模型**：改环境变量 `MEM0_LLM_MODEL` / `MEM0_EMBED_MODEL`，重启 9200。
注意换嵌入模型必须换 collection（维度不同）。

**跑评测做 A/B**：见第 2 节两条命令，对比混合与纯 BM25。

## 14. 故障排查

| 现象 | 原因与处理 |
|---|---|
| 9100 检索报 lance Not found | 索引文件损坏（常见于强杀进程）；调 build_index 重建，代码已支持自动降级 BM25 |
| 9200 启动失败 | Ollama 未运行或缺模型；`ollama list` 确认 phi4、bge-m3 |
| 记忆召回报 TypeError | mem0 v2 返回 `{"results":[]}` 结构；确认 store.py 走了 `_rows()` 归一化 |
| wren 查询报 Table not found | MDL 的 tableReference 与物理库不符；用 list_remote_tables 核对 |
| Python 服务启动即退出（GBK 报错） | 缺 PYTHONUTF8=1；start 脚本已内置，手动启动记得 export |
| MCP 显示未连接 | 先 status.sh 看端口；mcp.json 三个 URL 是否齐全（wren/context/memory） |
| git push errno 10054 | 网络间歇重置，重试即可 |
| Mem0 提示 Chroma 不支持关键词混合 | 预期行为，精确匹配由 context 的 BM25 负责，两者互补 |

## 15. 命令速查

```bash
# 服务管理
bash deployment/start-all.sh && bash deployment/status.sh

# 检索与评测
uv run python demo.py                                    # 本地检索演示
uv run python evals/run_eval.py                          # 检索层回归（8 用例）
uv run --with "mcp[cli]<2" python evals/run_e2e_eval.py  # 端到端回归（6 用例，需两 MCP 在线）
uv run --with "mcp[cli]<2" python demo_mcp.py            # 经 MCP 的检索演示

# 记忆
uv run python scripts/seed_team_memories.py              # 初始化团队记忆（幂等）
uv run python demo_memory.py                             # 记忆闭环演示

# 语义建模
uv run --with "mcp[cli]<2" python scripts/bootstrap_mdl.py

# 单独启动各 MCP（调试用）
PYTHONPATH=src uv run python -m context_engine.server    # :9100
PYTHONPATH=src uv run python -m memory.server            # :9200
```

## 16. 已知边界与路线图

- Mem0 + Chroma 仅语义检索，关键词精确匹配依赖 context BM25；
- phi4 抽取事实可能把中文改写成英文摘要，需保中文可换 qwen2.5（改 MEM0_LLM_MODEL）；
- `.mem0/`、`.lancedb/` 为本地数据（已 gitignore），多人共享需接共享盘或 Qdrant/Mem0 服务端；
- 待做：LLM rerank、查询日志自动挖掘 golden SQL、真实库百表规模压测。
