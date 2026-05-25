"""
最终测试 #01：多类型代码 E2E 全流程

5 种不同代码样本，验证完整流水线（code_parser → 三路审查 → critic → coder → sandbox → HITL → output）
每种样本只需结构正确：final_report 非空、status 合法、score 在 0-100。

用法：python tests/final/final_01_e2e_varied_code.py
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
# 5 种不同代码样本
# ============================================================
SAMPLES = {
    "安全-SQL注入": '''
def get_user(name):
    import sqlite3
    conn = sqlite3.connect("users.db")
    query = "SELECT * FROM users WHERE name = '%s'" % name
    return conn.execute(query).fetchall()
''',
    "性能-循环内字符串拼接": '''
def build_csv(rows):
    out = ""
    for r in rows:
        out += str(r["id"]) + "," + r["name"] + "\\n"
    return out
''',
    "风格-bare_except+无类型注解": '''
def load(path):
    try:
        with open(path) as f:
            return f.read()
    except:
        return None
''',
    "混合-安全+性能+风格": '''
API_KEY = "sk-abc123def456"

def fetch_data(user_id):
    result = []
    conn = sqlite3.connect("app.db")
    for uid in user_id:
        row = conn.execute("SELECT * FROM t WHERE id=" + str(uid)).fetchone()
        result.append(row)
    return result
''',
    "干净代码-应得高分": '''
def add(a: int, b: int) -> int:
    return a + b

def greet(name: str) -> str:
    return f"Hello, {name}"
''',
}

ALL_NODES = [
    "code_parser", "security_reviewer", "performance_reviewer",
    "style_reviewer", "critic_agent", "coder_agent",
    "sandbox_executor", "reflect_node", "human_review", "output_node",
]

VALID_STATUSES = {"success", "partial", "failed"}


async def run_one(app, code, run_id):
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = code
    config = {"configurable": {"thread_id": f"final01-{run_id}"}}

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

    # HITL 自动确认
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


def check_report(name, state):
    """验证 final_report 结构完整性"""
    issues = []
    report = state.get("final_report")

    if not report:
        return ["final_report 为 None"]

    # status 合法
    if report.status not in VALID_STATUSES:
        issues.append(f"status 非法: {report.status}")

    # score_before 0-100
    if not (0 <= report.score_before <= 100):
        issues.append(f"score_before 越界: {report.score_before}")

    # score_after 0-100
    if not (0 <= report.score_after <= 100):
        issues.append(f"score_after 越界: {report.score_after}")

    # retry_count >= 0
    if report.retry_count < 0:
        issues.append(f"retry_count 负数: {report.retry_count}")

    # original_code 保留
    if not report.original_code.strip():
        issues.append("original_code 丢失")

    return issues


async def main():
    app = build_graph()
    print(f"=== 最终测试 #01：多类型代码 E2E ===")
    print(f"  LLM: {LLM_MODEL}  MAX_RETRY: {MAX_RETRY}")
    print(f"  样本数: {len(SAMPLES)}\n")

    all_errors = {}
    total_start = time.time()

    for i, (name, code) in enumerate(SAMPLES.items(), 1):
        print(f"--- [{i}/{len(SAMPLES)}] {name} ---")
        t0 = time.time()

        state = await run_one(app, code.strip(), f"var-{i}")
        elapsed = time.time() - t0

        errs = check_report(name, state)
        all_errors[name] = errs

        report = state.get("final_report")
        if report:
            print(f"  status={report.status}  score {report.score_before}→{report.score_after}")
            print(f"  action_items={len(report.action_items)}  skipped={len(report.skipped_items)}")
            print(f"  retry={report.retry_count}  sandbox_passed={report.sandbox_passed}")
            print(f"  elapsed={elapsed:.1f}s")

        if errs:
            for e in errs:
                print(f"  ❌ {e}")
        else:
            print(f"  ✅ 通过")

        # 额外语义检查
        critic = state.get("critic_summary")
        coder = state.get("coder_result")
        if "干净" in name:
            if critic and critic.score_before < 70:
                errs.append(f"干净代码评分过低: {critic.score_before}")
            if coder and coder.changes and len(coder.changes) > 1:
                errs.append(f"干净代码不应有大量修改: {len(coder.changes)}处")
        if "SQL" in name or "注入" in name:
            # 确认安全审查员确实报告了注入问题
            reviewer_found_sql = any(
                any("sql" in (iss.description + iss.code_snippet).lower()
                    for iss in r.issues)
                for r in state.get("review_results", [])
            )
            if not reviewer_found_sql:
                errs.append("安全审查员未检测到 SQL 注入")

        for e in errs[len(check_report(name, state)):]:
            print(f"  ❌ {e}")
        print()

    total_elapsed = time.time() - total_start

    # 汇总
    total_errs = sum(len(e) for e in all_errors.values())
    failed_samples = sum(1 for e in all_errors.values() if e)

    print(f"{'='*60}")
    print(f"  样本数: {len(SAMPLES)}  失败样本: {failed_samples}")
    print(f"  总错误: {total_errs}  总耗时: {total_elapsed:.1f}s")
    print(f"  状态: {'success' if total_errs == 0 else 'failed'}")

    return 0 if total_errs == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    raise SystemExit(exit_code)
