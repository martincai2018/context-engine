"""团队记忆初始化：把项目中已验证的教训/决策/规范沉淀到 Mem0 团队库。

用法： PYTHONUTF8=1 PYTHONPATH=src uv run python scripts/seed_team_memories.py
幂等：已存在的知识重复写入会被 Mem0 去重/更新，不会无限膨胀。
"""
from memory import store

SEEDS = [
    ("correction", "订单状态枚举 P 的权威含义是 Pending（待处理），禁止解释为 Partial（部分完成）；F=Fulfilled 已完成，O=Open 进行中。"),
    ("convention", "GMV 口径统一为 SUM(totalprice)，即订单总价之和，未扣退款；任何回答涉及 GMV 必须标注该口径。订单量用 COUNT(DISTINCT orderkey)，不能用 lineitem 行数代替。"),
    ("issue", "Windows 上启动 Python 服务必须设置 PYTHONUTF8=1，否则 GBK 控制台渲染 rich 横幅 emoji 会抛 UnicodeEncodeError 导致进程退出。"),
    ("issue", "wren-core-py 0.1.0 在 PyPI 没有 Windows wheel，会回退 maturin 源码编译并因 crates.io 超时失败；解法是显式安装 0.8.2 的 cp311-abi3 win_amd64 wheel。"),
    ("decision", "ibis-server 的 Python 版本硬约束为 >=3.11,<3.12，3.12/3.14 无法安装；统一用 py -3.11 建虚拟环境，Poetry 装在项目 venv 内避免污染系统。"),
    ("lesson", "pi-web 只能监听 127.0.0.1，远程访问必须走 SSH 端口转发，禁止直接暴露公网；破坏性请求（DROP/UPDATE）由 sql-standards skill 与只读账号双重拦截。"),
    ("decision", "知识分层：权限在引擎侧强制（L0），指标关系在 Wren MDL（L1），规范术语在 Pi skills（L2），经验教训在 Mem0（本层），海量文档检索走向量库；各层不可互相替代。"),
    ("lesson", "Context Engine 实测：纯 BM25 在无词面重叠的语义问法上命中率 95.5%，加入 bge 向量与 RRF 融合后达到 100%；新增检索逻辑必须跑 evals/run_eval.py 回归。"),
]


def main() -> None:
    for kind, content in SEEDS:
        result = store.remember(content, scope="team", kind=kind)
        action = result.get("results", [{}])[0].get("event", "?") if isinstance(result, dict) else "?"
        print(f"[{kind:11s}] {action:8s} {content[:36]}...")
    print("\n团队记忆统计:", store.stats())


if __name__ == "__main__":
    main()
