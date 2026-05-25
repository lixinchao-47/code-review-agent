"""
最终测试 #06：前端 4 个示例各跑 5 次（共 20 次）

输出与前端一致的关键信息，不做专项断言，只收集展示。

用法：python tests/final/final_06_frontend_samples_20runs.py
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

SAMPLES = {
    "示例一 SQL注入+硬编码密码": """
DB_PASSWORD = "admin123"

def get_users(filter_role=None):
    query = "SELECT * FROM users"
    if filter_role:
        query += " WHERE role = '%s'" % filter_role
    import sqlite3
    conn = sqlite3.connect("app.db")
    return conn.execute(query).fetchall()
""",
    "示例二 eval+exec双重隐患": """
def calculate(expression, x):
    return eval(expression)

def run_script(code_str):
    exec(code_str)

def load_config(data):
    import pickle
    return pickle.loads(data)
""",
    "示例三 循环内字符串拼接+低效数据结构": """
def build_report(users):
    result = ""
    for u in users:
        result += u["name"] + "," + u["email"] + "\\n"
    return result

def find_duplicates(items):
    seen = []
    duplicates = []
    for item in items:
        if item in seen:
            duplicates.append(item)
        else:
            seen.append(item)
    return duplicates
""",
    "示例四 bare except+无类型注解": """
def read_config(path):
    try:
        with open(path) as f:
            return f.read()
    except:
        return None

def process(data, options):
    tmp = data.copy()
    tmp.update(options)
    return sorted(tmp.items())
""",
}

ALL_NODES = [
    "code_parser", "security_reviewer", "performance_reviewer",
    "style_reviewer", "critic_agent", "coder_agent",
    "sandbox_executor", "reflect_node", "human_review", "output_node",
]

RUNS_PER_SAMPLE = 5


async def run_one(app, code, thread_id):
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = code
    config = {"configurable": {"thread_id": thread_id}}

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
    print(f"=== 最终测试 #06：前端 4 示例 × 5 次 = 20 轮 ===")
    print(f"  LLM: {LLM_MODEL}  MAX_RETRY: {MAX_RETRY}\n")

    total_start = time.time()
    run_id = 0
    all_results = []

    for sample_name, code in SAMPLES.items():
        print(f"{'='*70}")
        print(f"  {sample_name}")
        print(f"{'='*70}")
        sample_results = []

        for i in range(1, RUNS_PER_SAMPLE + 1):
            run_id += 1
            t0 = time.time()
            state = await run_one(app, code.strip(), f"final06-{run_id}")
            elapsed = time.time() - t0

            report = state.get("final_report")

            print(f"--- 第{i}轮 ({elapsed:.0f}s) ---")
            if not report:
                print(f"  final_report: None")
                sample_results.append(None)
                continue

            print(f"  status: {report.status}")
            print(f"  score: {report.score_before} → {report.score_after}")
            print(f"  sandbox_passed: {report.sandbox_passed}")
            print(f"  retry_count: {report.retry_count}")
            print(f"  action_items: {len(report.action_items)} 条")
            if report.skipped_items:
                print(f"  skipped_items: {len(report.skipped_items)} 条")
                for s in report.skipped_items:
                    print(f"    - {s[:120]}")
            if report.notes:
                print(f"  notes: {report.notes[:200]}")
            print()
            sample_results.append(report)

        all_results.append((sample_name, sample_results))

    total_elapsed = time.time() - total_start

    # 汇总
    print(f"\n{'='*70}")
    print(f"  汇总（共 {run_id} 轮，总耗时 {total_elapsed:.0f}s）")
    print(f"{'='*70}")
    for sample_name, results in all_results:
        valid = [r for r in results if r is not None]
        if not valid:
            print(f"  {sample_name}: 全部返回 None")
            continue
        statuses = [r.status for r in valid]
        scores_before = [r.score_before for r in valid]
        scores_after = [r.score_after for r in valid]
        sandbox_ok = sum(1 for r in valid if r.sandbox_passed)
        actions = [len(r.action_items) for r in valid]
        skipped = [len(r.skipped_items) for r in valid]
        has_notes = sum(1 for r in valid if r.notes)

        print(f"  {sample_name}:")
        print(f"    status: {statuses}")
        print(f"    score_before: {scores_before}  (avg={sum(scores_before)/len(scores_before):.0f})")
        print(f"    score_after:  {scores_after}  (avg={sum(scores_after)/len(scores_after):.0f})")
        print(f"    sandbox 通过: {sandbox_ok}/{len(valid)}")
        print(f"    action_items: {actions}  (avg={sum(actions)/len(actions):.1f})")
        print(f"    skipped: {skipped}")
        print(f"    有 notes: {has_notes}/{len(valid)}")
        print()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    raise SystemExit(exit_code)
