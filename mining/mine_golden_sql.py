"""从 pi 会话日志挖掘 golden SQL 候选（问题 → SQL → 执行成功三元组）。

设计原则：只产出待评审候选，不自动写入 golden_sql.yaml——
agent 生成的 SQL 必须经人工确认口径后才能成为团队知识。

用法：
  uv run --with pyyaml python mining/mine_golden_sql.py            # 扫描生成候选
  uv run --with pyyaml python mining/mine_golden_sql.py --list     # 查看候选队列
  uv run --with pyyaml python mining/mine_golden_sql.py --promote g001   # 晋升指定候选
"""
from __future__ import annotations

import argparse
import json
import re
import uuid
from collections import defaultdict
from pathlib import Path

import yaml

SESSIONS_DIR = Path.home() / ".pi" / "agent" / "sessions"
HERE = Path(__file__).resolve().parent
CANDIDATES_FILE = HERE / "golden_candidates.yaml"
GOLDEN_FILE = HERE.parent / "data" / "golden_sql.yaml"

# 从 codemode JS 代码里提取 SQL：反引号模板、双引号、单引号三种形态
SQL_PATTERNS = [
    re.compile(r"(?:const|let|var)\s+sql\s*=\s*`([^`]+)`"),
    re.compile(r"mcp__wren__query\(\s*\{\s*sql\s*:\s*`([^`]+)`"),
    re.compile(r"mcp__wren__query\(\s*\{\s*sql\s*:\s*[\"']([^\"']+)[\"']"),
]
WRITE_KEYWORDS = ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
                  "TRUNCATE", "GRANT", "REPLACE", "MERGE")


def extract_sql(code: str) -> list[str]:
    found = []
    for pat in SQL_PATTERNS:
        found.extend(pat.findall(code))
    # 只保留 SELECT，过滤写操作与明显残缺
    out = []
    for sql in found:
        s = sql.strip().rstrip(";").strip()
        head = re.split(r"\s", s.upper(), 1)[0] if s else ""
        if head == "SELECT" and not any(k in s.upper() for k in WRITE_KEYWORDS):
            out.append(" ".join(s.split()))  # 规范化空白
    return list(dict.fromkeys(out))  # 去重保序


def scan_sessions() -> list[dict]:
    triples: dict[str, dict] = {}  # normalized sql -> candidate
    for path in sorted(SESSIONS_DIR.rglob("*.jsonl")):
        current_question = None
        pending_sql: list[str] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = ev.get("message") or {}
            role = msg.get("role")

            if role == "user":
                for c in msg.get("content", []) or []:
                    if isinstance(c, dict) and c.get("type") == "text":
                        current_question = c["text"].strip()

            elif role == "assistant":
                for c in msg.get("content", []) or []:
                    if isinstance(c, dict) and c.get("type") == "toolCall" \
                            and c.get("name") == "codemode":
                        pending_sql.extend(
                            extract_sql((c.get("arguments") or {}).get("code", "")))

            elif role == "toolResult" and pending_sql:
                failed = bool(msg.get("isError"))
                output = " ".join(
                    c.get("text", "") for c in (msg.get("content") or [])
                    if isinstance(c, dict))
                failed = failed or ("errorCode" in output and "SQL error" in output)
                if not failed:
                    for sql in pending_sql:
                        key = sql.lower()
                        item = triples.setdefault(key, {
                            "id": "", "question": current_question or "(无关联问题)",
                            "sql": sql, "sources": [], "count": 0,
                        })
                        item["count"] += 1
                        if path.name not in item["sources"]:
                            item["sources"].append(path.name)
                        # 优先保留有业务含义的问题
                        if item["question"].startswith("(") and current_question:
                            item["question"] = current_question.strip()
                pending_sql = []

    # 评分：复用频次 + 会话覆盖 + 问题质量
    candidates = list(triples.values())
    for i, item in enumerate(sorted(
            candidates, key=lambda x: (x["count"], len(x["sources"])), reverse=True), 1):
        item["id"] = f"g{i:03d}"
        item["status"] = "pending"
    return candidates


def write_candidates(candidates: list[dict]) -> None:
    CANDIDATES_FILE.write_text(
        yaml.safe_dump({"candidates": candidates}, allow_unicode=True,
                       sort_keys=False), encoding="utf-8")


def load_candidates() -> list[dict]:
    if not CANDIDATES_FILE.exists():
        return []
    return yaml.safe_load(CANDIDATES_FILE.read_text(encoding="utf-8"))["candidates"]


def promote(cid: str) -> None:
    candidates = load_candidates()
    target = next((c for c in candidates if c["id"] == cid), None)
    if not target:
        raise SystemExit(f"候选 {cid} 不存在，先用 --list 查看")
    golden = yaml.safe_load(GOLDEN_FILE.read_text(encoding="utf-8")) or {"queries": []}
    if any(" ".join(q["sql"].split()).lower() == target["sql"].lower()
           for q in golden["queries"]):
        print(f"{cid} 已存在于 golden_sql.yaml，跳过")
    else:
        golden["queries"].append({
            "question": target["question"],
            "sql": target["sql"],
            "tags": ["mined", f"freq={target['count']}"],
        })
        GOLDEN_FILE.write_text(
            yaml.safe_dump(golden, allow_unicode=True, sort_keys=False),
            encoding="utf-8")
        print(f"已晋升 {cid} → {GOLDEN_FILE}")
    target["status"] = "promoted"
    write_candidates(candidates)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="查看候选队列")
    ap.add_argument("--promote", metavar="ID", help="晋升候选到 golden_sql.yaml")
    args = ap.parse_args()

    if args.promote:
        promote(args.promote)
        return
    if args.list:
        for c in load_candidates():
            print(f"[{c['status']:8s}] {c['id']} ×{c['count']}  {c['question'][:40]}")
            print(f"            {c['sql'][:90]}")
        return

    candidates = scan_sessions()
    write_candidates(candidates)
    print(f"扫描 {SESSIONS_DIR}")
    print(f"挖掘到 {len(candidates)} 条去重 SQL 候选 → {CANDIDATES_FILE.name}")
    for c in candidates[:10]:
        print(f"  {c['id']} ×{c['count']} {c['question'][:36]} | {c['sql'][:60]}")
    print("\n评审后用 --promote <id> 晋升；候选文件不自动生效。")


if __name__ == "__main__":
    main()
