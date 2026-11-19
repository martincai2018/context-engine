"""Agent 模式评测：让 pi agent 看自然语言问题，自主经 context+wren MCP 生成并执行 SQL。

与 run_e2e_eval.py 的区别：金标 SQL 不告诉 agent，只给业务问题，
度量真实的 NL→SQL 端到端能力。agent 输出有模型波动，支持每题多轮取 pass@k。

用法：
  uv run --with "mcp[cli]<2" python evals/run_agent_eval.py              # 每题 1 轮
  uv run --with "mcp[cli]<2" python evals/run_agent_eval.py --runs 3     # pass@3
前置：9100/9000 MCP 在线；pi CLI 路径可用 PI_CLI 覆盖
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

import yaml

PI_CLI = os.getenv(
    "PI_CLI",
    r"C:\AI\pi-main\packages\coding-agent\dist\bundle\cli.js",
)
# 在知识库目录启动以加载 skills
PI_CWD = os.getenv("PI_CWD", r"C:\AI\data-agent-knowledge")
RESULT_RE = re.compile(r"AGENT_RESULT:\s*(\[.*?\])\s*$", re.DOTALL | re.MULTILINE)
FLOAT_TOL = 0.01
TIMEOUT_S = int(os.getenv("AGENT_EVAL_TIMEOUT", "300"))

PROMPT_TMPL = (
    "你是数仓 data agent。请通过已注册的 MCP 工具完成任务：{q}\n"
    "要求：1) 先调 context 的 search_context 获取语义上下文；"
    "2) 用 wren 的 dry_run 验证 SQL 后再 query 执行；"
    "3) 回答正文正常给出中文说明，但最后一行必须严格输出 "
    "AGENT_RESULT: <wren query 返回的 data 数组 JSON>，该行不得包含其他文字。"
)


def norm(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return v
    return v


def rows_match(actual, expected) -> bool:
    if len(actual) != len(expected):
        return False
    pool = {tuple(norm(x) for x in row) for row in actual}
    for row in expected:
        cand = tuple(norm(x) for x in row)
        hit = next((a for a in pool if len(a) == len(cand) and all(
            (abs(x - y) < FLOAT_TOL if isinstance(x, float) else x == y)
            for x, y in zip(a, cand))), None)
        if hit is None:
            return False
        pool.discard(hit)
    return True


def parse_agent_output(raw: str) -> tuple[str, object]:
    """从 pi --mode json 的 NDJSON 事件流拼出文本并提取 AGENT_RESULT。"""
    text_parts = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        ame = ev.get("assistantMessageEvent") or {}
        if ame.get("type") == "text_delta":
            text_parts.append(ame.get("delta", ""))
    text = "".join(text_parts)
    m = list(RESULT_RE.finditer(text))
    if not m:
        return "PARSE_FAIL", text[-300:]
    try:
        return "OK", json.loads(m[-1].group(1))
    except json.JSONDecodeError:
        return "PARSE_FAIL", text[-300:]


def evaluate_rows(rows, case: dict) -> tuple[bool, str]:
    if "expected_rows" in case:
        return (rows_match(rows, case["expected_rows"]),
                "" if rows_match(rows, case["expected_rows"]) else f"结果集不符: {rows}")
    shape = case.get("shape", {})
    if shape.get("min_rows") and len(rows) < shape["min_rows"]:
        return False, f"行数 {len(rows)} 低于 min_rows {shape['min_rows']}"
    if shape.get("first_row_first_col") and rows:
        if rows[0][0] != shape["first_row_first_col"]:
            return False, f"首行首列应为 {shape['first_row_first_col']}，实际 {rows[0][0]}"
    return True, ""


def run_once(question: str) -> tuple[str, str, float]:
    prompt = PROMPT_TMPL.format(q=question)
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            ["node", PI_CLI, "-p", "--mode", "json", prompt],
            cwd=PI_CWD, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return "TIMEOUT", f"超过 {TIMEOUT_S}s", time.perf_counter() - t0
    elapsed = time.perf_counter() - t0
    if proc.returncode != 0:
        return "TOOL_ERROR", (proc.stderr or proc.stdout)[-300:], elapsed
    status, payload = parse_agent_output(proc.stdout)
    if status != "OK":
        return status, str(payload), elapsed
    return "OK", payload, elapsed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1, help="每题运行轮数（统计 pass@k）")
    args = ap.parse_args()

    all_cases = yaml.safe_load(
        (Path(__file__).parent / "e2e_questions.yaml").read_text(encoding="utf-8")
    )["cases"]
    # 负向用例（坏 SQL 拦截）不属于 agent 生成能力评测，跳过
    cases = [c for c in all_cases if not c.get("dry_run_fail")]

    summary = []
    latencies = []
    for case in cases:
        cid, question = case["id"], case["question"]
        run_results = []
        for r in range(args.runs):
            status, payload, elapsed = run_once(question)
            latencies.append(elapsed)
            detail = ""
            if status == "OK":
                ok, detail = evaluate_rows(payload, case)
                status = "PASS" if ok else "WRONG_RESULT"
            run_results.append((status, detail or str(payload)[:160], elapsed))
        passes = sum(1 for s, _, _ in run_results if s == "PASS")
        verdict = "PASS" if passes else "FAIL"
        summary.append((cid, verdict, passes, args.runs, run_results))
        print(f"[{verdict}] {cid} {question}  pass={passes}/{args.runs}")
        for s, d, el in run_results:
            if s != "PASS":
                print(f"        {s} ({el:.0f}s): {d}")

    total_runs = args.runs * len(cases)
    total_pass = sum(p for _, _, p, _, _ in summary)
    print("\n===== Agent 模式评测汇总 =====")
    print(f"用例: {len(cases)} | 总运行: {total_runs} | 通过: {total_pass}")
    print(f"pass@{args.runs}: {total_pass/total_runs:.0%}")
    if latencies:
        print(f"单轮延迟 p50={statistics.median(latencies):.0f}s "
              f"max={max(latencies):.0f}s")
    fail_types = {}
    for _, _, _, _, runs in summary:
        for s, _, _ in runs:
            if s != "PASS":
                fail_types[s] = fail_types.get(s, 0) + 1
    if fail_types:
        print("失败分类:", fail_types)
    raise SystemExit(0 if total_pass == total_runs else 1)


if __name__ == "__main__":
    main()
