"""跑网页示例代码第一个，多次验证修复效果 + 修改稳定性

检测：
  1. [需人工] 命中率 — 凭据类 100%
  2. skipped_items 无污染 — 只含 [需人工] 条目
  3. 凭据行未被修改 — DB_PASSWORD 保持原样
  4. fix_instruction 无 [修复]/[跳过] 残留
  5. 修改稳定性 — changes 数量/内容是否波动

用法：python tests/stability/test_st_05_web_sample1_stability.py
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

# 网页示例代码第一个：SQL 注入 + 硬编码密码
SAMPLE = """
DB_PASSWORD = \"admin123\"

def get_users(filter_role=None):
    query = \"SELECT * FROM users\"
    if filter_role:
        query += \" WHERE role = '%s'\" % filter_role
    import sqlite3
    conn = sqlite3.connect(\"app.db\")
    return conn.execute(query).fetchall()
"""

ALL_NODES = [
    "code_parser", "security_reviewer", "performance_reviewer",
    "style_reviewer", "critic_agent", "coder_agent",
    "sandbox_executor", "reflect_node", "human_review", "output_node",
]


async def run_one(app, code, run_id):
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = code
    config = {"configurable": {"thread_id": f"st05-{run_id}"}}

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
    RUNS = 5
    print(f"=== 网页示例 #1 稳定性测试 ===")
    print(f"  LLM: {LLM_MODEL}  MAX_RETRY: {MAX_RETRY}")
    print(f"  跑 {RUNS} 次\n")

    all_results = []
    for i in range(1, RUNS + 1):
        t0 = time.time()
        state = await run_one(app, SAMPLE, i)
        elapsed = time.time() - t0

        critic = state.get("critic_summary")
        coder = state.get("coder_result")
        report = state.get("final_report")

        errors = []

        # === 检测 1: [需人工] 在 skipped_items ===
        manual_in_action = 0
        if critic and critic.action_plan:
            for item in critic.action_plan:
                if "[需人工]" in item.fix_instruction:
                    manual_in_action += 1

        skipped = coder.skipped_items if coder else []
        manual_in_skipped = sum(1 for s in skipped if "[需人工]" in s)

        # === 检测 2: skipped_items 无污染 ===
        polluted = []
        for s in skipped:
            if "[需人工]" not in s:
                polluted.append(s[:80])
        if polluted:
            errors.append(f"skipped_items 污染 {len(polluted)} 条无 [需人工] 标签")

        # === 检测 3: 凭据行未改 ===
        if report and report.fixed_code:
            if "DB_PASSWORD = \"admin123\"" not in report.fixed_code:
                errors.append("DB_PASSWORD 字符串消失")
            if "os.environ" in report.fixed_code or "getenv" in report.fixed_code:
                errors.append("修复后代码出现 os.environ/getenv")

        # === 检测 4: 标签卫生 ===
        if critic and critic.action_plan:
            for item in critic.action_plan:
                inst = item.fix_instruction
                if inst.startswith("[修复]"):
                    errors.append(f"[修复] 前缀残留: {inst[:60]}")
                if "[跳过]" in inst:
                    errors.append(f"[跳过] 标签残留: {inst[:60]}")

        # === 收集修改详情 ===
        changes_summary = []
        if coder and coder.changes:
            for ch in coder.changes:
                changes_summary.append({
                    "lineno": ch.lineno if hasattr(ch, 'lineno') else "?",
                    "desc": (ch.reason or ch.description)[:80],
                })

        total_actions = len(critic.action_plan) if critic else 0
        change_count = len(coder.changes) if coder else 0

        r = {
            "run": i,
            "errors": errors,
            "manual_count": manual_in_action,
            "skipped_count": len(skipped),
            "total_actions": total_actions,
            "change_count": change_count,
            "changes_summary": changes_summary,
            "score_before": report.score_before if report else "?",
            "score_after": report.score_after if report else "?",
            "status": report.status if report else "?",
            "elapsed": elapsed,
        }
        all_results.append(r)

        tag = "❌" if errors else "✅"
        print(f"  #{i} {tag} 评分 {r['score_before']}→{r['score_after']}  "
              f"需人工 {manual_in_action}  跳过 {len(skipped)}  "
              f"critic {total_actions}条  coder改 {change_count}处  "
              f"状态 {r['status']}  {elapsed:.0f}s")

        # 打印每条 critic 指令
        if critic and critic.action_plan:
            for item in critic.action_plan:
                sev = item.severity.value if hasattr(item.severity, 'value') else item.severity
                cat = str(item.category) if hasattr(item, 'category') else "?"
                has_manual = "[需人工]" in item.fix_instruction
                display_tag = "[需人工]" if has_manual else "[修复]"
                print(f"       {display_tag} [{sev}] L{item.lineno} {cat}: {item.fix_instruction[:80]}")

        # 打印每条 coder 修改
        if coder and coder.changes:
            for ch in coder.changes:
                ln = ch.lineno if hasattr(ch, 'lineno') else "?"
                desc = (ch.reason or "")[:80]
                print(f"       ✏️  L{ln}: {desc}")

        # 打印凭据行
        if report and report.fixed_code:
            for line in report.fixed_code.split("\n"):
                if "PASSWORD" in line or "getenv" in line:
                    print(f"       🔑 {line.strip()}")

        # 打印错误
        for err in errors:
            print(f"       ❌ {err}")

    # === 汇总 ===
    print(f"\n{'='*60}")
    print(f"  汇总")
    print(f"{'='*60}")

    total_errors = sum(len(r["errors"]) for r in all_results)
    changes_per_run = [r["change_count"] for r in all_results]
    actions_per_run = [r["total_actions"] for r in all_results]
    manual_per_run = [r["manual_count"] for r in all_results]

    print(f"\n  === 核心检测 ===")
    print(f"  错误轮次: {sum(1 for r in all_results if r['errors'])}/{RUNS}")
    if total_errors > 0:
        for r in all_results:
            for err in r["errors"]:
                print(f"    #{r['run']}: {err}")

    print(f"\n  === 稳定性 ===")
    print(f"  critic action_plan 数: {actions_per_run}  波动: {max(actions_per_run)-min(actions_per_run)}")
    print(f"    各轮明细: {actions_per_run}")
    print(f"  coder changes 数:     {changes_per_run}  波动: {max(changes_per_run)-min(changes_per_run)}")
    print(f"    各轮明细: {changes_per_run}")
    print(f"  [需人工] 数:          {manual_per_run}  波动: {max(manual_per_run)-min(manual_per_run)}")
    print(f"  status 分布:          {[r['status'] for r in all_results]}")
    print(f"  评分: {[r['score_before'] for r in all_results]} → {[r['score_after'] for r in all_results]}")
    print(f"  耗时: {[round(r['elapsed']) for r in all_results]}s")

    # 分析哪些修改点不稳定
    print(f"\n  === 修改点逐轮对比 ===")
    all_change_sigs = {}
    for r in all_results:
        for ch in r["changes_summary"]:
            sig = f"L{ch['lineno']}: {ch['desc'][:60]}"
            all_change_sigs[sig] = all_change_sigs.get(sig, 0) + 1

    stable = [(sig, cnt) for sig, cnt in all_change_sigs.items() if cnt >= RUNS]
    unstable = [(sig, cnt) for sig, cnt in all_change_sigs.items() if cnt < RUNS]

    if stable:
        print(f"  稳定修改（{RUNS}/{RUNS} 轮）: {len(stable)} 处")
        for sig, cnt in stable:
            print(f"    ✅ {sig}")
    if unstable:
        print(f"  不稳定修改（<{RUNS} 轮）: {len(unstable)} 处")
        for sig, cnt in unstable:
            print(f"    🔸 ({cnt}/{RUNS}) {sig}")

    if total_errors == 0:
        print(f"\n  结论: ✅ 所有修复 bug 未复现")
    else:
        print(f"\n  结论: ❌ 有 {total_errors} 个错误")

    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    print(f"\nexit={exit_code}")
    raise SystemExit(exit_code)
