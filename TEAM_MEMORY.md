# 数仓开发团队的可持续学习机制（Mem0）

> 目标：让团队在日常开发中产生的经验**自动沉淀、跨人共享、持续反哺 agent**，
> 而不是散落在聊天记录和个人脑子里。

## 1. 分层学习模型（与知识库 L0–L4 对应）

```
日常工作产生经验（bad case / 评审决策 / 环境踩坑 / 口径纠错）
        │
        ▼  remember(scope=user)   自动事实抽取，量大、噪声高
L4 个人经验记忆（Mem0 user 作用域，按开发者隔离）
        │  评审通过后 promote_to_team
        ▼
L3 团队共享记忆（Mem0 agent_id=dw-dev-team，全员/所有 agent 可召回）
        │  稳定、高频引用的知识，人工固化
        ▼
L2/L1 确定性层：glossary.yaml / golden_sql.yaml / skills / Wren MDL（Git 评审）
```

每层职责不同，**不可互相替代**：Mem0 存"经验和来龙去脉"（概率召回、可演化），
MDL/skills 存"确定口径和规则"（强约束、可审计）。

## 2. 技术实现

| 组件 | 选型 | 说明 |
|---|---|---|
| 记忆框架 | Mem0 SDK v2（mem0ai） | 内置图记忆、ADD-only 事实抽取、混合检索 |
| LLM（抽取） | Ollama + phi4，本地 | 不向外部 API 发送团队数据 |
| 嵌入 | Ollama + bge-m3（1024 维，中英双语） | 本地推理 |
| 向量存储 | Chroma 嵌入式（`.mem0/chroma`） | 无独立服务、无 Docker |
| 协议暴露 | MCP server :9200/mcp | 与 wren/context 平级接入 pi |
| 作用域 | user_id（个人）/ agent_id=dw-dev-team（团队） | 四作用域模型 |

记忆类别（metadata.kind）：`lesson` 教训、`decision` 决策、`correction` 口径纠错、
`convention` 规范、`issue` 环境问题。

## 3. MCP 工具（6 个）

| 工具 | 作用 | 典型场景 |
|---|---|---|
| `remember(content, scope, kind)` | 写记忆 | 踩坑后即时记录，默认写个人库 |
| `recall(query, scope)` | 检索记忆 | agent 回答前自动查团队库（已集成进 search_context） |
| `list_memories(scope)` | 列出记忆 | 评审会盘点本周新增经验 |
| `promote_to_team(memory_id)` | 个人→团队晋升 | 评审确认某条经验有通用价值 |
| `forget(memory_id)` | 删除过时记忆 | 环境升级后旧 issue 失效 |
| `memory_stats()` | 统计 | 观测知识资产增长 |

## 4. 可持续学习闭环（团队工作流）

1. **采集**：开发者在 pi-web 工作时，让 agent 调用 `remember` 记录踩坑和决策；
   agent 也可在纠错对话后自动抽取事实写入个人库。
2. **召回**：`search_context` 的上下文包自动包含"团队历史经验"分节
   （retrieval_mode 显示 `+memory`），新人拿到和老员工一样的背景知识。
3. **评审**：每周/每迭代用 `list_memories(scope=user)` 盘点个人记忆，
   有通用价值的执行 `promote_to_team`（自动记录晋升人和来源 id）。
4. **固化**：反复被召回、已稳定的团队记忆，人工提升到 L2/L1——
   写进 glossary/golden SQL/MDL 并走 Git PR，成为强约束。
5. **遗忘**：环境变化后用 `forget` 清理失效记忆，保持知识库时效。
6. **度量**：结合 evals 回归集，观察记忆注入对命中率/SQL 正确率的提升。

## 5. 本地使用

```bash
# 前置：ollama serve，并已拉取 phi4:latest、bge-m3:latest
uv sync --extra vector --extra memory

# 初始化团队记忆（幂等，可重复执行）
PYTHONUTF8=1 PYTHONPATH=src uv run python scripts/seed_team_memories.py

# 启动记忆 MCP（:9200）
PYTHONUTF8=1 PYTHONPATH=src uv run python -m memory.server

# 闭环演示：写入→召回→晋升→全作用域检索
PYTHONUTF8=1 PYTHONPATH=src uv run python demo_memory.py
```

pi 的 `~/.pi/agent/mcp.json` 注册三个 MCP：

```json
{
  "mcpServers": {
    "wren":    { "url": "http://localhost:9000/mcp" },
    "context": { "url": "http://localhost:9100/mcp" },
    "memory":  { "url": "http://localhost:9200/mcp" }
  }
}
```

## 6. 已知边界

- Mem0 + Chroma 仅语义检索（Chroma 不支持关键词混合打分）；精确术语匹配仍由 context engine 的 BM25 负责，两者互补。
- phi4 抽取事实时可能把中文改写成英文摘要；需要严格保中文时换用中文更强的本地模型（如 qwen2.5 系列），改 `MEM0_LLM_MODEL` 即可。
- 记忆数据在 `.mem0/`（已 gitignore），团队共享需放在共享盘或后续接 Mem0 服务端/Qdrant。
- 遥测已通过 `MEM0_TELEMETRY=False` 关闭。
