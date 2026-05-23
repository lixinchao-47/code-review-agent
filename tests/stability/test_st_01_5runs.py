"""跑 5 次同一段代码，收集审查结果指标"""
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

CODE = '''
DB_PASSWORD = "admin123"

def get_users(filter_role=None):
    query = "SELECT * FROM users"
    if filter_role:
        query += " WHERE role = '%s'" % filter_role
    import sqlite3
    conn = sqlite3.connect("app.db")
    return conn.execute(query).fetchall()
'''

ALL_NODES = [
    "code_parser", "security_reviewer", "performance_reviewer",
    "style_reviewer", "critic_agent", "coder_agent",
    "sandbox_executor", "reflect_node", "human_review", "output_node",
]

async def run_one(app, run_id):
    """跑一次完整审查（自动批准 HITL）"""
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = CODE
    config = {"configurable": {"thread_id": f"st-{run_id}"}}

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
    print(f"=== 同一代码跑 5 次 ===  LLM: {LLM_MODEL}  MAX_RETRY: {MAX_RETRY}")
    print(f"代码: {len(CODE.splitlines())} 行, 含硬编码密码 + SQL 拼接")
    print()

    results = []
    for i in range(1, 6):
        t0 = time.time()
        state = await run_one(app, i)
        elapsed = time.time() - t0

        report = state.get("final_report")
        critic = state.get("critic_summary")

        if not report:
            print(f"  #{i}: 报告未生成 ❌")
            results.append({"run": i, "error": "no report"})
            continue

        result = {
            "run": i,
            "time": round(elapsed, 1),
            "status": report.status,
            "score_before": report.score_before,
            "score_after": report.score_after,
            "total_issues": critic.total_issues if critic else 0,
            "action_items": len(report.action_items),
            "retry_count": report.retry_count,
        }
        # 追踪 [需人工] 标签
        manual_items = []
        if critic and critic.action_plan:
            for item in critic.action_plan:
                if "[需人工]" in item.fix_instruction:
                    manual_items.append(f"{item.category.value if hasattr(item.category, 'value') else item.category}:{item.description[:40]}")
        result["manual_count"] = len(manual_items)
        result["manual_items"] = manual_items
        results.append(result)

        print(f"  #{i}: 评分 {report.score_before}→{report.score_after}  "
              f"问题数 {result['total_issues']}  修复指令 {result['action_items']}  "
              f"重试 {report.retry_count}  "
              f"用时 {elapsed:.0f}s  status={report.status}")

        if critic and critic.action_plan:
            for item in critic.action_plan[:5]:
                sev = item.severity.value if hasattr(item.severity, 'value') else str(item.severity)
                desc = item.description[:60]
                has_manual = "[需人工]" in item.fix_instruction
                has_skip = "[跳过]" in item.fix_instruction
                tag = "[需人工]" if has_manual else ("[跳过]" if has_skip else "[修复]")
                print(f"         {tag} [{sev}] 行{item.lineno} {desc}")

        # 输出密码相关行的修复结果
        if report.fixed_code:
            for line in report.fixed_code.split("\n"):
                if "PASSWORD" in line or "password" in line or "getenv" in line:
                    print(f"         🔑 修复后密码行: {line.strip()}")

    print()
    print("=== 5 次汇总 ===")
    scores_before = [r["score_before"] for r in results if "score_before" in r]
    scores_after  = [r["score_after"]  for r in results if "score_after"  in r]
    issues        = [r["total_issues"] for r in results if "total_issues" in r]
    actions       = [r["action_items"] for r in results if "action_items" in r]
    times         = [r["time"]         for r in results if "time"         in r]

    if scores_before:
        print(f"  修复前评分: {scores_before}  范围 {min(scores_before)}-{max(scores_before)}  均值 {sum(scores_before)/len(scores_before):.0f}")
    if scores_after:
        print(f"  修复后评分: {scores_after}  范围 {min(scores_after)}-{max(scores_after)}  均值 {sum(scores_after)/len(scores_after):.0f}")
    if issues:
        print(f"  问题数:     {issues}  范围 {min(issues)}-{max(issues)}")
    if actions:
        print(f"  修复指令数: {actions}  范围 {min(actions)}-{max(actions)}")
    if results:
        print(f"  状态分布:   {[r['status'] for r in results]}")
    if times:
        print(f"  耗时:       范围 {min(times)}-{max(times)}s  均值 {sum(times)/len(times):.0f}s")

    # 稳定性评估
    sr = max(scores_before) - min(scores_before) if scores_before else 0
    ir = max(issues) - min(issues) if issues else 0

    if sr <= 10 and ir <= 2:
        print(f"\n  评估: 波动较小 ✅ 评分差 {sr}, 问题数差 {ir}")
    elif sr <= 25 and ir <= 4:
        print(f"\n  评估: 波动中等 ⚠️ 评分差 {sr}, 问题数差 {ir}")
    else:
        print(f"\n  评估: 波动较大 ❌ 评分差 {sr}, 问题数差 {ir}")

    # [需人工] 标签稳定性专项
    print()
    print("=== [需人工] 标签稳定性 ===")
    manual_counts = [r.get("manual_count", 0) for r in results]
    print(f"  各轮 [需人工] 数: {manual_counts}")
    if manual_counts:
        hit_rate = sum(1 for c in manual_counts if c > 0) / len(manual_counts) * 100
        print(f"  至少标 1 个的命中率: {hit_rate:.0f}% ({sum(1 for c in manual_counts if c > 0)}/{len(manual_counts)})")
        all_items = []
        for r in results:
            all_items.extend(r.get("manual_items", []))
        if all_items:
            print(f"  被标 [需人工] 的问题: {all_items}")
    print(f"  temperature=0.0  |  预期: 硬编码密码每次都应标 [需人工]")


if __name__ == "__main__":
    asyncio.run(main())
