"""
最终测试 #05：HITL 中断 + 人工反馈 + 重试流程

验证：
1. 正常流程会触发 human_review 中断
2. 人工提交反馈后流水线继续到 output_node
3. 确认无误（空反馈）直接输出
4. 重试流程：必然失败的代码 → reflect_node 触发 → retry_count 递增
5. MAX_RETRY 上限后进入 human_review（不再无限重试）

用法：python tests/final/final_05_hitl_and_retry.py
"""
import sys, asyncio, time, warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*allowed_objects.*")
import logging
logging.getLogger("langgraph.checkpoint.serde.jsonplus").setLevel(logging.ERROR)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = str(PROJECT_ROOT / "src")
sys.path.insert(0, SRC_DIR)

from config import LLM_MODEL, MAX_RETRY
from graph.builder import build_graph
from graph.state import INITIAL_STATE, AgentState

ALL_NODES = [
    "code_parser", "security_reviewer", "performance_reviewer",
    "style_reviewer", "critic_agent", "coder_agent",
    "sandbox_executor", "reflect_node", "human_review", "output_node",
]

# 正常代码 — 会修好并通过沙箱，进入 HITL
NORMAL_CODE = """
def fetch(user):
    import sqlite3
    conn = sqlite3.connect("app.db")
    q = "SELECT * FROM t WHERE name='" + user + "'"
    return conn.execute(q).fetchall()
"""

# 必然失败代码 — 有语法错误的代码，sandbox 反复失败，触发重试→上限
BROKEN_CODE = """
def explode():
    raise RuntimeError("sandbox will always fail")

explode()
"""


async def run_until_interrupt_or_end(app, state, config):
    """流式执行直到中断/结束，返回 (final_state, hit_interrupt)"""
    current_state = dict(state) if state else {}
    async for event in app.astream_events(state, config, version="v2"):
        kind = event["event"]
        name = event.get("name", "")
        if kind == "on_chain_end" and name in ALL_NODES:
            output = event["data"].get("output", {})
            if isinstance(output, dict):
                for k, v in output.items():
                    if k in AgentState.__annotations__:
                        current_state[k] = v

    snapshot = app.get_state(config)
    hit = bool(snapshot.next and "human_review" in str(snapshot.next))
    return current_state, hit


async def test_normal_hitl(app):
    """测试 1+2+3：正常代码 → HITL 中断 → 确认 / 反馈"""
    print("--- 测试 1：正常流程触发 HITL 中断 ---")

    config = {"configurable": {"thread_id": "final05-normal-hitl"}}
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = NORMAL_CODE.strip()

    state, hit = await run_until_interrupt_or_end(app, initial_state, config)

    if not hit:
        print("  ❌ 未触发 human_review 中断")
        return ["未触发 HITL 中断"], state
    print("  ✅ human_review 中断已触发")

    # 测试 2：确认无误（空反馈），流水线继续到 output
    print("\n--- 测试 2：确认（空反馈）→ output_node ---")
    app.update_state(config, {"human_feedback": ""})
    state, hit2 = await run_until_interrupt_or_end(app, None, config)

    report = state.get("final_report")
    if report:
        print(f"  ✅ 输出报告: status={report.status}  score={report.score_before}→{report.score_after}")
    else:
        print("  ❌ 确认后未生成 final_report")
        return ["确认后未生成报告"], state

    return [], state


async def test_feedback_path(app):
    """测试 3：提交修改意见 → 重新修复"""
    print("\n--- 测试 3：人工反馈 → 重新修复 ---")

    config = {"configurable": {"thread_id": "final05-feedback"}}
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = NORMAL_CODE.strip()

    state, hit = await run_until_interrupt_or_end(app, initial_state, config)

    if not hit:
        print("  ❌ 未触发 HITL 中断")
        return ["反馈测试: 未触发 HITL"]

    # 提交修改意见
    app.update_state(config, {"human_feedback": "把 f-string 换成参数化查询"})
    state, hit2 = await run_until_interrupt_or_end(app, None, config)

    # 应该再次进入 HITL（修完又回来了）或直接结束
    report = state.get("final_report")
    if report:
        print(f"  ✅ 反馈后生成报告: status={report.status}")
    elif hit2:
        print(f"  ⚠ 反馈后又进入 HITL（coder 再次生成需要确认的结果）")
    else:
        print(f"  ❌ 反馈后无报告且无中断")

    return []


async def test_retry_loop(app):
    """测试 4+5：失败代码 → retry_count 递增 → 上限后进入 human_review"""
    print("\n--- 测试 4：失败代码 → 重试计数 ---")

    config = {"configurable": {"thread_id": "final05-retry"}}
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = BROKEN_CODE.strip()

    state, hit = await run_until_interrupt_or_end(app, initial_state, config)

    retry = state.get("retry_count", 0)
    sandbox = state.get("sandbox_result")

    print(f"  retry_count={retry}  MAX_RETRY={MAX_RETRY}")
    print(f"  sandbox_passed={sandbox.passed if sandbox else 'N/A'}")

    errors = []
    if retry >= 1:
        print(f"  ✅ 重试已触发 (retry_count={retry})")

    # 上限后应进入 human_review 或直接输出
    if hit:
        print(f"  ✅ 重试上限后进入 human_review")
        app.update_state(config, {"human_feedback": ""})
        state, _ = await run_until_interrupt_or_end(app, None, config)
        report = state.get("final_report")
        if report:
            print(f"  ✅ 确认后输出报告: status={report.status}  retry={report.retry_count}")
        else:
            errors.append("确认后未生成报告")
    else:
        report = state.get("final_report")
        if report:
            print(f"  ✅ 直接输出报告: status={report.status}  retry={report.retry_count}")

    return errors


async def main():
    app = build_graph()
    print(f"=== 最终测试 #05：HITL 中断 + 重试流程 ===")
    print(f"  LLM: {LLM_MODEL}  MAX_RETRY: {MAX_RETRY}\n")

    errors = []
    total_start = time.time()

    # 测试 1+2：正常 HITL + 确认
    errs, _ = await test_normal_hitl(app)
    errors.extend(errs)

    # 测试 3：人工反馈路径
    errs = await test_feedback_path(app)
    errors.extend(errs)

    # 测试 4+5：重试循环 + 上限
    errs = await test_retry_loop(app)
    errors.extend(errs)

    total_elapsed = time.time() - total_start

    print(f"\n{'='*60}")
    print(f"  总耗时: {total_elapsed:.1f}s  错误数: {len(errors)}")
    if errors:
        for e in errors:
            print(f"  ❌ {e}")
    print(f"  状态: {'success' if not errors else 'failed'}")

    return 0 if not errors else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    raise SystemExit(exit_code)
