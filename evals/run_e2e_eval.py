"""端到端评测：金标问题经 context(9100) 检索 → wren(9000) dry_run/query 全链路。

输出指标：
  检索命中率（模型/口径/视图/关系/golden 五维断言）
  dry-run 通过率（含负向用例：坏 SQL 必须被拦截）
  执行准确率（结果集集合比对，浮点容差 0.01；shape 断言）
  延迟 p50/p95、工具步数
用法： uv run --with "mcp[cli]<2" python evals/run_e2e_eval.py
前置： 9100 与 9000 两个 MCP 服务已启动，DuckDB 演示 MDL 已部署
"""
from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path

import yaml
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

CONTEXT_MCP = "http://localhost:9100/mcp"
WREN_MCP = "http://localhost:9000/mcp"
FLOAT_TOL = 0.01


def _tool_text(result) -> str:
    return result.content[0].text if result.content else ""


def _norm(v):
    """数值归一化：int/float 转 float；decimal 经 JSON 会变成数字字符串，尝试解析；
    非数字字符串（枚举码、业务名）保持原样。"""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return v
    return v


def _is_error_payload(text: str) -> bool:
    """wren 的 dry_run/query 失败时返回 200 + 错误体（含 errorCode），而非 MCP isError。"""
    try:
        payload = json.loads(text)
        return isinstance(payload, dict) and "errorCode" in payload
    except (json.JSONDecodeError, TypeError):
        return text.startswith("Error")


def _rows_equal(actual: list, expected: list) -> bool:
    if len(actual) != len(expected):
        return False
    actual_set = {tuple(_norm(x) for x in row) for row in actual}
    for row in expected:
        cand = tuple(_norm(x) for x in row)
        match = next((a for a in actual_set if len(a) == len(cand) and all(
            (abs(x - y) < FLOAT_TOL if isinstance(x, float) else x == y)
            for x, y in zip(a, cand)
        )), None)
        if match is None:
            return False
        actual_set.discard(match)
    return True


async def call_tool(session, name: str, args: dict) -> tuple[str, bool, float]:
    t0 = time.perf_counter()
    try:
        result = await session.call_tool(name, args)
        text = _tool_text(result)
        failed = bool(getattr(result, "isError", False)) or _is_error_payload(text)
        return text, failed, (time.perf_counter() - t0) * 1000
    except Exception as exc:  # 传输/协议层失败
        return f"Exception: {exc}", True, (time.perf_counter() - t0) * 1000


def check_retrieval(packet: dict, expect: dict) -> tuple[bool, list[str]]:
    actual = {
        "models": [m["name"] for m in packet.get("models", []) if "tableReference" in m],
        "glossary": [g["term"] for g in packet.get("glossary", [])],
        "golden": [g["question"] for g in packet.get("golden_sql", [])],
        "views": [v.get("name") for v in packet.get("metrics_views", [])],
        "joins": [j["name"] for j in packet.get("join_candidates", [])],
    }
    failures = []
    for dim, wanted in expect.items():
        missing = [w for w in wanted if w not in actual.get(dim, [])]
        if missing:
            failures.append(f"{dim} 缺 {missing}")
    return not failures, failures


async def main() -> None:
    cases = yaml.safe_load(
        (Path(__file__).parent / "e2e_questions.yaml").read_text(encoding="utf-8")
    )["cases"]

    async with streamablehttp_client(CONTEXT_MCP) as (cr, cw, _), \
               streamablehttp_client(WREN_MCP) as (wr, ww, _):
        async with ClientSession(cr, cw) as ctx, ClientSession(wr, ww) as wren:
            await ctx.initialize()
            await wren.initialize()

            rows = []
            latencies = {"retrieval": [], "dry_run": [], "query": []}
            for case in cases:
                cid, question = case["id"], case["question"]
                detail = []

                # 1) 检索层
                t0 = time.perf_counter()
                pkt = json.loads((await ctx.call_tool(
                    "search_context", {"question": question})).content[0].text)
                lat_ret = (time.perf_counter() - t0) * 1000
                latencies["retrieval"].append(lat_ret)
                ret_ok, ret_fail = check_retrieval(pkt, case.get("expect_retrieval", {}))
                if not ret_ok:
                    detail.extend(ret_fail)

                # 2) dry-run（含负向用例）
                dry_text, dry_failed, lat_dry = await call_tool(
                    wren, "dry_run", {"sql": case["sql"]})
                latencies["dry_run"].append(lat_dry)
                if case.get("dry_run_fail"):
                    dry_ok = dry_failed
                    if not dry_ok:
                        detail.append("负向用例：坏 SQL 未被 dry_run 拦截")
                else:
                    dry_ok = not dry_failed
                    if not dry_ok:
                        detail.append(f"dry_run 失败: {dry_text[:120]}")

                # 3) 执行与结果比对（负向用例跳过）
                exec_ok = True
                if not case.get("dry_run_fail"):
                    q_text, q_failed, lat_q = await call_tool(
                        wren, "query", {"sql": case["sql"]})
                    latencies["query"].append(lat_q)
                    if q_failed:
                        exec_ok, detail = False, detail + [f"query 失败: {q_text[:120]}"]
                    else:
                        result = json.loads(q_text)
                        if "expected_columns" in case and \
                                result.get("columns") != case["expected_columns"]:
                            exec_ok = False
                            detail.append(f"列不符: {result.get('columns')}")
                        if "expected_rows" in case and \
                                not _rows_equal(result.get("data", []), case["expected_rows"]):
                            exec_ok = False
                            detail.append(f"结果集不符: {result.get('data')}")
                        shape = case.get("shape", {})
                        if shape.get("min_rows") and \
                                len(result.get("data", [])) < shape["min_rows"]:
                            exec_ok = False
                            detail.append("行数低于 min_rows")
                        if shape.get("first_row_first_col") and \
                                result["data"][0][0] != shape["first_row_first_col"]:
                            exec_ok = False
                            detail.append(f"首行首列应为 {shape['first_row_first_col']}")

                passed = ret_ok and dry_ok and exec_ok
                rows.append((cid, passed, detail))
                print(f"[{'PASS' if passed else 'FAIL'}] {cid} {question}")
                if detail:
                    for d in detail:
                        print(f"        - {d}")

    n = len(rows)
    passed = sum(1 for _, ok, _ in rows if ok)
    print("\n===== 端到端评测汇总 =====")
    print(f"用例通过率: {passed}/{n} = {passed/n:.0%}")
    for stage, vals in latencies.items():
        if vals:
            vals_sorted = sorted(vals)
            p50 = statistics.median(vals_sorted)
            p95 = vals_sorted[min(len(vals_sorted) - 1, int(len(vals_sorted) * 0.95))]
            print(f"{stage:10s} 延迟 p50={p50:7.0f}ms  p95={p95:7.0f}ms  (n={len(vals)})")
    print(f"工具步数: 每用例 2-3 次 MCP 调用（search_context + dry_run [+ query]）")
    raise SystemExit(0 if passed == n else 1)


if __name__ == "__main__":
    asyncio.run(main())
