# Golden SQL 自动挖掘：知识库自增长机制

## 解决什么问题

golden SQL 是检索质量的关键语料，但纯靠人工编写成本高、覆盖慢。
团队每天与 agent 的真实对话（pi 会话日志）里已经沉淀了大量
"自然语言问题 → 成功执行的 SQL"，挖掘器把它们变成**待评审候选**，
人工只做确认而不是从零编写。

## 数据来源与边界

- 来源：`~/.pi/agent/sessions/**/*.jsonl`（pi 自动记录的全部会话）
- 提取：codemode 工具调用中 `mcp__wren__query` 的 SQL（反引号/引号三种形态）
- 成功判定：toolResult `isError=false` 且输出无 errorCode
- 安全过滤：只保留 SELECT，写操作（INSERT/UPDATE/DROP…）直接排除
- **边界：只产出候选，绝不自动写入 golden_sql.yaml**——口径必须人工确认

## 使用方式

```bash
# 1. 扫描会话，生成候选队列（按复用频次、会话覆盖排序）
uv run --with pyyaml python mining/mine_golden_sql.py

# 2. 查看候选
uv run --with pyyaml python mining/mine_golden_sql.py --list

# 3. 评审通过后晋升（自动去重，已存在的 SQL 跳过）
uv run --with pyyaml python mining/mine_golden_sql.py --promote g002

# 4. 重建索引让新知识在线上生效（MCP 工具）
#    调 context 的 build_index，或重启 9100
```

候选文件 `mining/golden_candidates.yaml` 是本机派生数据（含个人提问原文），
已 gitignore；晋升进 `data/golden_sql.yaml` 的内容才进版本库。

## 实测效果（2026-11，19 个真实会话）

- 挖掘出 12 条去重 SQL 候选，最高复用频次 2；
- 晋升 g002（月度 GMV 趋势，频次 2）后，相关问法的 golden 召回立即生效；
- 过程中发现并修复一个架构问题：新文档加入后全局 top-k 互相挤占导致
  GMV 术语漏召回（100%→95.5%），改为**类型配额召回**（glossary/golden/
  指标视图各自独立检索）后恢复，百表与演示规模评测均保持 100%。

## 完整学习闭环（与 Mem0 的分工）

```
真实对话 → 会话日志 → 挖掘候选（本模块，结构化 SQL 资产）
                    ↘ Mem0 remember（经验教训，非结构化事实）
人工评审 → promote 到 golden_sql.yaml / promote_to_team
       → build_index 重建 → 新会话自动受益
bad case 回流 → 补金标集 → evals 回归防退化
```

- 挖掘器管"可复用的查询模式"（确定性、可执行）
- Mem0 管"为什么这么做的经验"（解释性、可演化）
- 两者都遵守"agent 采集、人工策展、评审后共享"的同一治理原则
