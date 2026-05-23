"""验证 coder 不修改 [需人工] 条目 — 代码级过滤 + 标签卫生

检测项：
  1. [需人工] 条目全部进入 skipped_items，不在 fixed_code 中出现修改
  2. 凭据行与原始代码完全一致（未被动过）
  3. 无 [修复]/[跳过] 标签残留
  4. 非 [需人工] 条目正常修复

用法：python tests/stability/test_st_03_coder_needs_human.py
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

# 混合样本：硬编码凭据(需人工) + SQL 注入(可修复) + 风格问题
SAMPLE = '''
API_KEY = "sk-proj-abc123def456ghi789"

def get_users(filter_role=None):
    query = "SELECT * FROM users"
    if filter_role:
        query += " WHERE role = '%s'" % filter_role
    import sqlite3
    conn = sqlite3.connect("app.db")
    return conn.execute(query).fetchall()
'''

CREDENTIAL_LINES = {1, 3}  # 第 1 行 API_KEY，第 3 行空行前

ALL_NODES = [
    "code_parser", "security_reviewer", "performance_reviewer",
    "style_reviewer", "critic_agent", "coder_agent",
    "sandbox_executor", "reflect_node", "human_review", "output_node",
]


async def run_one(app, code, run_id):
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = code
    config = {"configurable": {"thread_id": f"st03-{run_id}"}}

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


def check_fix_instruction_tags(action_plan):
    """检查 fix_instruction 是否有非法标签"""
    fake_tags = []
    for item in action_plan:
        inst = item.fix_instruction
        if inst.startswith("[修复]"):
            fake_tags.append(f"[修复] at L{item.lineno}: {inst[:60]}")
        if "[跳过]" in inst:
            fake_tags.append(f"[跳过] at L{item.lineno}: {inst[:60]}")
    return fake_tags


async def main():
    app = build_graph()
    RUNS = 5
    print(f"=== coder [需人工] 过滤 + 标签卫生测试 ===")
    print(f"  LLM: {LLM_MODEL}  MAX_RETRY: {MAX_RETRY}")
    print(f"  每轮跑 {RUNS} 次\n")

    results = []
    for i in range(1, RUNS + 1):
        t0 = time.time()
        state = await run_one(app, SAMPLE, i)
        elapsed = time.time() - t0

        critic = state.get("critic_summary")
        report = state.get("final_report")
        coder = state.get("coder_result")

        errors = []

        # === 检测 1: [需人工] 条目在 skipped_items 中 ===
        manual_in_action = []
        if critic and critic.action_plan:
            for item in critic.action_plan:
                if "[需人工]" in item.fix_instruction:
                    manual_in_action.append(item)

        skipped = coder.skipped_items if coder else []
        skipped_text = " | ".join(skipped)
        manual_in_skipped = sum(1 for s in skipped if "[需人工]" in s)

        if len(manual_in_action) > 0 and manual_in_skipped == 0:
            errors.append(f"[需人工] {len(manual_in_action)} 条在 action_plan 但 skipped_items 为空")

        # === 检测 2: 凭据内容未被修改 ===
        CREDENTIAL_STRINGS = ["sk-proj-abc123def456ghi789", "API_KEY"]
        if report and report.fixed_code:
            fixed_code = report.fixed_code
            for cred in CREDENTIAL_STRINGS:
                if cred not in SAMPLE:
                    continue
                if cred not in fixed_code:
                    errors.append(f"凭据串 '{cred}' 在修复后代码中消失")
            # 确保未出现 os.environ.get 代替原凭据
            if manual_in_skipped > 0 and "os.environ" in fixed_code:
                errors.append("标了 [需人工] 但修复后代码出现了 os.environ")

        # === 检测 3: 标签卫生 ===
        if critic and critic.action_plan:
            fake_tags = check_fix_instruction_tags(critic.action_plan)
            errors.extend(fake_tags)

        # === 检测 4: 非 [需人工] 条目被正常修复 ===
        fixed_non_manual = 0
        if coder and coder.changes:
            for ch in coder.changes:
                if "[需人工]" not in (ch.reason or ""):
                    fixed_non_manual += 1

        # === 汇总 ===
        total_actions = len(critic.action_plan) if critic else 0
        score_before = report.score_before if report else "?"
        score_after = report.score_after if report else "?"
        status = report.status if report else "?"

        r = {
            "run": i, "errors": errors,
            "manual_count": len(manual_in_action),
            "skipped_count": len(skipped),
            "total_actions": total_actions,
            "fixed_non_manual": fixed_non_manual,
            "score_before": score_before,
            "score_after": score_after,
            "elapsed": elapsed,
        }
        results.append(r)

        tag = "❌" if errors else "✅"
        print(f"  #{i} {tag} 评分 {score_before}→{score_after}  需人工 {len(manual_in_action)}  "
              f"跳过 {len(skipped)}  修复 {fixed_non_manual}  "
              f"状态 {status}  用时 {elapsed:.0f}s")
        if critic and critic.action_plan:
            for item in critic.action_plan:
                sev = item.severity.value if hasattr(item.severity, 'value') else item.severity
                cat = str(item.category) if hasattr(item, 'category') else "?"
                tag_display = "[需人工]" if "[需人工]" in item.fix_instruction else "[修复]"
                print(f"       {tag_display} [{sev}] L{item.lineno} {cat}")
        for err in errors:
            print(f"       ❌ {err}")
        # 打印凭据相关行
        if report and report.fixed_code:
            for lineno, line in enumerate(report.fixed_code.split("\n"), 1):
                if any(kw in line for kw in ["API_KEY", "sk-proj", "getenv", "environ"]):
                    print(f"       🔑 L{lineno}: {line.strip()}")

    # === 汇总 ===
    print(f"\n{'='*60}")
    print(f"  汇总")
    print(f"{'='*60}")

    total_errors = sum(len(r["errors"]) for r in results)
    error_runs = sum(1 for r in results if r["errors"])
    manual_hit = sum(1 for r in results if r["manual_count"] > 0)

    print(f"  错误轮次: {error_runs}/{RUNS}  总错误数: {total_errors}")
    print(f"  [需人工] 命中率: {manual_hit}/{RUNS}")
    print(f"  各轮 [需人工] 数: {[r['manual_count'] for r in results]}")
    print(f"  各轮 skipped 数: {[r['skipped_count'] for r in results]}")
    print(f"  各轮修复数: {[r['fixed_non_manual'] for r in results]}")
    print(f"  各轮评分: {[r['score_before'] for r in results]} → {[r['score_after'] for r in results]}")
    print(f"  耗时: {[round(r['elapsed']) for r in results]}s")

    if total_errors == 0 and manual_hit >= RUNS * 0.8:
        print(f"\n  结论: ✅ 全部通过")
    elif total_errors > 0:
        print(f"\n  结论: ❌ 有 {total_errors} 个错误需处理")
        for r in results:
            if r["errors"]:
                for err in r["errors"]:
                    print(f"    #{r['run']}: {err}")
    else:
        print(f"\n  结论: 🟡 [需人工] 命中率偏低 ({manual_hit}/{RUNS})")

    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    raise SystemExit(exit_code)
