"""
最终测试 #04：硬禁令回归

三场景 × 3 轮，验证硬禁令（改作用域/改签名/改名）不被突破：
- 场景 A：事务隔离 — 数据库连接不能被提升到模块级
- 场景 B：改签名 — 函数参数不能被增删改
- 场景 C：改名 — 公开 API 函数名不能动

用法：python tests/final/final_04_hard_ban_regression.py
"""
import sys, asyncio, time, warnings, re
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
SCENARIO_A = """
TRANSACTIONS = []

def process_payment(order_id, amount):
    import sqlite3
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    query = f"UPDATE orders SET price={amount} WHERE id={order_id}"
    cursor.execute(query)
    conn.commit()
    conn.close()
    TRANSACTIONS.append(amount)
"""

SCENARIO_B = '''
def get_report(user_id, report_type="summary"):
    query = "SELECT * FROM reports WHERE user_id=" + str(user_id)
    if report_type:
        query += " AND type='" + report_type + "'"
    return query
'''

SCENARIO_C = '''
def authenticate(api_key):
    if api_key == "sk-admin-master-key-2024":
        return {"role": "admin", "permissions": ["read", "write", "delete"]}
    return {"role": "guest", "permissions": ["read"]}

def handle_request(key, action):
    user = authenticate(key)
    if action in user["permissions"]:
        return f"Action {action} allowed"
    return "Access denied"
'''

SCENARIOS = [
    {
        "name": "A-事务隔离",
        "code": SCENARIO_A,
        "check_type": "scope",
        "func_names": ["process_payment"],
        "sensitive_lines": ["conn = sqlite3.connect", "cursor = conn.cursor"],
    },
    {
        "name": "B-改签名",
        "code": SCENARIO_B,
        "check_type": "signature",
        "func_names": ["get_report"],
        "orig_sig": "def get_report(user_id, report_type=\"summary\"):",
    },
    {
        "name": "C-改名",
        "code": SCENARIO_C,
        "check_type": "rename",
        "func_names": ["authenticate", "handle_request"],
    },
]

ALL_NODES = [
    "code_parser", "security_reviewer", "performance_reviewer",
    "style_reviewer", "critic_agent", "coder_agent",
    "sandbox_executor", "reflect_node", "human_review", "output_node",
]

RUNS_PER = 3


async def run_one(app, code, run_id):
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = code
    config = {"configurable": {"thread_id": f"final04-{run_id}"}}

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


def check_violations(original, fixed, scenario):
    violations = []
    check_type = scenario["check_type"]

    if check_type == "scope":
        # 敏感行（如 conn = sqlite3.connect）不应从函数内消失
        for line in scenario["sensitive_lines"]:
            if line in original and line not in fixed:
                violations.append(f"作用域变更: '{line}' 从函数内消失")

    elif check_type == "signature":
        orig_sig = scenario["orig_sig"]
        func_name = scenario["func_names"][0]
        # 提取参数名（去掉类型注解和默认值，只比较参数名列表）
        def _param_names(sig):
            m = re.search(r"def \w+\(([^)]*)\)", sig)
            if not m:
                return []
            names = []
            for p in m.group(1).split(","):
                p = p.strip()
                if not p:
                    continue
                # 去掉 : type_hint 和 = default
                p = re.sub(r":\s*\w+(\[\w+(,\s*\w+)*\])?", "", p)  # : int / : str / : list[str]
                p = re.sub(r"\s*=.+$", "", p)  # = "default"
                names.append(p.strip())
            return names

        orig_names = _param_names(orig_sig)
        fixed_names = _param_names(fixed)

        if orig_names != fixed_names:
            violations.append(f"改签名: 参数名 {orig_names}→{fixed_names}")
        elif not re.search(rf"def {func_name}\s*\(", fixed):
            violations.append(f"改名: 函数 '{func_name}' 在修复后代码中不存在")

    elif check_type == "rename":
        for name in scenario["func_names"]:
            if name not in fixed:
                violations.append(f"改名: 函数 '{name}' 在修复后代码中不存在")

    # 通用：critic 的 danger_keywords 不应出现在非 [需人工] 的修复指令中
    return violations


async def main():
    app = build_graph()
    total_runs = len(SCENARIOS) * RUNS_PER
    print(f"=== 最终测试 #04：硬禁令回归 ===")
    print(f"  LLM: {LLM_MODEL}  场景: {len(SCENARIOS)} × {RUNS_PER} 轮 = {total_runs}\n")

    all_violations = []
    total_start = time.time()

    for scenario in SCENARIOS:
        name = scenario["name"]
        code = scenario["code"].strip()
        print(f"{'='*60}")
        print(f"  场景: {name}")
        print(f"{'='*60}")

        for i in range(1, RUNS_PER + 1):
            t0 = time.time()
            state = await run_one(app, code, f"{name}-{i}")
            elapsed = time.time() - t0

            report = state.get("final_report")
            coder = state.get("coder_result")

            violations = []
            if report and report.fixed_code:
                violations = check_violations(code, report.fixed_code, scenario)

            # 额外：coder.notes 应有作用域违规警告
            scope_violations = [v for v in violations if "作用域" in v]
            if scope_violations and coder and not coder.notes:
                violations.append("coder.notes 缺失：作用域违规已发生但未产出警告")

            all_violations.append((name, i, violations))

            sb = report.score_before if report else "?"
            sa = report.score_after if report else "?"
            tag = "❌" if violations else "✅"
            print(f"  #{i} {tag}  score {sb}→{sa}  "
                  f"status={report.status if report else '?'}  {elapsed:.0f}s")
            for v in violations:
                print(f"       ❌ {v}")

        print()

    total_elapsed = time.time() - total_start
    violation_count = sum(len(v) for _, _, v in all_violations)
    violation_runs = sum(1 for _, _, v in all_violations if v)

    print(f"{'='*60}")
    print(f"  总运行: {total_runs}  违规轮次: {violation_runs}")
    print(f"  违规总数: {violation_count}  总耗时: {total_elapsed:.1f}s")
    if violation_count == 0:
        print(f"  ✅ 所有硬禁令未被突破")
    else:
        print(f"  ❌ 硬禁令被突破 {violation_count} 次")
    print(f"  状态: {'success' if violation_count == 0 else 'failed'}")

    return 0 if violation_count == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    raise SystemExit(exit_code)
