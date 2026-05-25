"""
最终测试 #02：稳定性 —— 相同代码多次运行一致性

同一份代码跑 3 次，验证：
- score_before 波动 ≤ 15（同一 LLM 对待相同代码应有基本一致的评分）
- status 三次一致（不应一次 success 一次 failed）
- action_items 数量波动 ≤ 2

用法：python tests/final/final_02_stability_multi_run.py
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

# ============================================================
# 测试用代码 —— 中等复杂，有确定问题
# ============================================================
TEST_CODE = """
def process(items):
    result = ""
    for x in items:
        result += str(x) + ","
    return result

def lookup(user_id):
    import sqlite3
    conn = sqlite3.connect("app.db")
    query = "SELECT * FROM users WHERE id=" + str(user_id)
    return conn.execute(query).fetchone()

def save_config(path, data):
    try:
        f = open(path, "w")
        f.write(str(data))
    except:
        pass
"""

ALL_NODES = [
    "code_parser", "security_reviewer", "performance_reviewer",
    "style_reviewer", "critic_agent", "coder_agent",
    "sandbox_executor", "reflect_node", "human_review", "output_node",
]

RUNS = 3


async def run_one(app, code, run_id):
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = code
    config = {"configurable": {"thread_id": f"final02-{run_id}"}}

    current_state = dict(initial_state)
    async for event in app.astream_events(initial_state, config, version="v2"):
        kind = event["event"]
        name = event.get("name", "")
        if kind == "on_chain_end" and name in ALL_NODES:
            output = event["data"].get("output", {})
            if isinstance(output, dict):
                for k, v in output.items():
                    if k in AgentState.__annotations__:
                        current_state[k] = v

    snapshot = app.get_state(config)
    if snapshot.next and "human_review" in str(snapshot.next):
        app.update_state(config, {"human_feedback": ""})
        async for event in app.astream_events(None, config, version="v2"):
            kind = event["event"]
            name = event.get("name", "")
            if kind == "on_chain_end" and name in ALL_NODES:
                output = event["data"].get("output", {})
                if isinstance(output, dict):
                    for k, v in output.items():
                        if k in AgentState.__annotations__:
                            current_state[k] = v

    return current_state


async def main():
    app = build_graph()
    print(f"=== 最终测试 #02：稳定性（相同代码 × {RUNS} 次） ===")
    print(f"  LLM: {LLM_MODEL}  MAX_RETRY: {MAX_RETRY}\n")

    reports = []
    scores_before = []
    scores_after = []
    statuses = []
    action_counts = []
    errors = []

    total_start = time.time()

    for i in range(1, RUNS + 1):
        print(f"--- 第 {i}/{RUNS} 轮 ---")
        t0 = time.time()
        state = await run_one(app, TEST_CODE.strip(), f"stability-{i}")
        elapsed = time.time() - t0

        report = state.get("final_report")
        if not report:
            errors.append(f"第{i}轮 final_report 为 None")
            print(f"  ❌ final_report 为 None\n")
            continue

        reports.append(report)
        scores_before.append(report.score_before)
        scores_after.append(report.score_after)
        statuses.append(report.status)
        action_counts.append(len(report.action_items))

        print(f"  score_before={report.score_before}  score_after={report.score_after}")
        print(f"  status={report.status}  action_items={len(report.action_items)}")
        print(f"  retry={report.retry_count}  sandbox_passed={report.sandbox_passed}")
        print(f"  elapsed={elapsed:.1f}s\n")

    total_elapsed = time.time() - total_start

    if len(reports) < 2:
        errors.append("有效报告不足 2 份，无法做一致性分析")
    else:
        # 检测 1：score_before 波动
        sb_range = max(scores_before) - min(scores_before)
        print(f"--- score_before 一致性 ---")
        print(f"  值: {scores_before}")
        print(f"  波动范围: {sb_range}")
        if sb_range <= 15:
            print(f"  ✅ 波动 ≤ 15，评分稳定")
        else:
            err = f"score_before 波动过大: {sb_range}"
            errors.append(err)
            print(f"  ❌ {err}")

        # 检测 2：status 一致性
        print(f"\n--- status 一致性 ---")
        print(f"  值: {statuses}")
        unique_statuses = set(statuses)
        if len(unique_statuses) == 1:
            print(f"  ✅ status 完全一致: {statuses[0]}")
        else:
            err = f"status 不一致: {statuses}"
            errors.append(err)
            print(f"  ❌ {err}")

        # 检测 3：action_items 数量波动
        print(f"\n--- action_items 数量一致性 ---")
        print(f"  值: {action_counts}")
        ac_range = max(action_counts) - min(action_counts)
        if ac_range <= 2:
            print(f"  ✅ 数量波动 ≤ 2，稳定")
        else:
            err = f"action_items 数量波动过大: {ac_range}"
            errors.append(err)
            print(f"  ❌ {err}")

        # 检测 4：sandbox 结果一致性
        print(f"\n--- sandbox 结果一致性 ---")
        sandbox_results = [r.sandbox_passed for r in reports]
        print(f"  值: {sandbox_results}")
        if len(set(sandbox_results)) == 1:
            print(f"  ✅ sandbox 结果一致: {sandbox_results[0]}")
        else:
            err = f"sandbox 结果不一致: {sandbox_results}"
            errors.append(err)
            print(f"  ❌ {err}")

    print(f"\n{'='*60}")
    print(f"  运行轮次: {RUNS}  总耗时: {total_elapsed:.1f}s")
    print(f"  错误数: {len(errors)}")
    if errors:
        for e in errors:
            print(f"  ❌ {e}")
    print(f"  状态: {'success' if not errors else 'failed'}")

    return 0 if not errors else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    raise SystemExit(exit_code)
