"""硬禁令违规风险测试 — 模拟可能产生严重后果的漏网场景

检测：
  1. 改作用域 — 连接/状态被提升到模块级（破坏事务隔离、多调用共享状态）
  2. 改签名 — 函数参数/返回值被修改（破坏外部调用方）
  3. 改名 — 函数名/变量名被改动（破坏外部引用）

每个场景跑多次，验证硬禁令违规是否出现、违规后果是否严重。

用法：python tests/stability/test_st_04_hard_ban_risk.py
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
# 场景 1：事务隔离 — 连接必须在函数内
# ============================================================
SCENARIO_CONNECTION = """
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

# ============================================================
# 场景 2：改签名风险 — 函数有外部调用方假设
# ============================================================
SCENARIO_SIGNATURE = """
API_VERSION = "v2"

def get_report(user_id, report_type="summary"):
    query = "SELECT * FROM reports WHERE user_id=" + str(user_id)
    if report_type:
        query += " AND type='" + report_type + "'"
    return query
"""

# ============================================================
# 场景 3：改名风险 — 公开 API 函数
# ============================================================
SCENARIO_RENAME = """
def authenticate(api_key):
    if api_key == "sk-admin-master-key-2024":
        return {"role": "admin", "permissions": ["read", "write", "delete"]}
    return {"role": "guest", "permissions": ["read"]}

def handle_request(key, action):
    user = authenticate(key)
    if action in user["permissions"]:
        return f"Action {action} allowed"
    return "Access denied"
"""

# ============================================================
SCENARIOS = [
    {
        "name": "事务隔离-连接不能提升到模块级",
        "code": SCENARIO_CONNECTION,
        "type": "scope",
        "checks": {
            "scope_violation": [
                "conn = sqlite3.connect",
                "cursor = conn.cursor",
            ],
            "danger_keywords": ["模块级", "函数外部", "全局"],
        },
    },
    {
        "name": "改签名-参数/返回值不能被改动",
        "code": SCENARIO_SIGNATURE,
        "type": "signature",
        "checks": {
            "danger_keywords": ["新参数", "增加参数", "移除参数"],
        },
        "original_sig": 'def get_report(user_id, report_type="summary"):',
    },
    {
        "name": "改名-公开API函数名不能动",
        "code": SCENARIO_RENAME,
        "type": "rename",
        "checks": {
            "danger_keywords": ["重命名", "改名"],
        },
        "original_names": ["authenticate", "handle_request"],
    },
]

ALL_NODES = [
    "code_parser", "security_reviewer", "performance_reviewer",
    "style_reviewer", "critic_agent", "coder_agent",
    "sandbox_executor", "reflect_node", "human_review", "output_node",
]


async def run_one(app, code, run_id):
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = code
    config = {"configurable": {"thread_id": f"st04-{run_id}"}}

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


def check_fixed_code(original, fixed, scenario):
    """检查修复后代码是否出现硬禁令违规"""
    issues = []
    checks = scenario["checks"]
    stype = scenario.get("type", "scope")

    if stype == "scope":
        for keyword in checks.get("scope_violation", []):
            count_orig = original.count(keyword)
            count_fixed = fixed.count(keyword)
            if count_fixed < count_orig:
                issues.append(f"改作用域: '{keyword}' 从函数内消失 (原{count_orig}→修{count_fixed})")
        for kw in ["conn = sqlite3", "cursor = conn"]:
            if kw in fixed:
                idx = fixed.find(kw)
                before = fixed[:idx]
                if before.count("def ") == 0 and fixed.count("def ") > 0:
                    issues.append(f"作用域泄漏: '{kw}' 疑似在模块级别（函数体外）")

    elif stype == "signature":
        orig_sig = scenario.get("original_sig", "")
        if orig_sig and orig_sig not in fixed:
            import re
            sigs = re.findall(r"def get_report\(([^)]*)\)", fixed)
            orig_params = 'user_id, report_type="summary"'
            for sig in sigs:
                if sig.strip() != orig_params:
                    issues.append(f"改签名: 参数从 '{orig_params}' 变为 '{sig.strip()}'")

    elif stype == "rename":
        for name in scenario.get("original_names", []):
            if name not in fixed:
                issues.append(f"改名: 函数 '{name}' 在修复后代码中不存在")

    return issues


async def main():
    app = build_graph()
    RUNS = 5
    print(f"=== 硬禁令违规风险测试 ===")
    print(f"  LLM: {LLM_MODEL}  MAX_RETRY: {MAX_RETRY}")
    print(f"  场景数: {len(SCENARIOS)}  每场景跑 {RUNS} 次\n")

    all_scenario_errors = 0

    for scenario in SCENARIOS:
        name = scenario["name"]
        code = scenario["code"]
        stype = scenario["type"]

        print(f"{'='*60}")
        print(f"  场景: {name}")
        print(f"{'='*60}")

        errors_found = []
        for i in range(1, RUNS + 1):
            t0 = time.time()
            state = await run_one(app, code, f"{stype}-{i}")
            elapsed = time.time() - t0

            report = state.get("final_report")
            critic = state.get("critic_summary")
            coder = state.get("coder_result")

            round_errors = []
            if report and report.fixed_code:
                round_errors = check_fixed_code(code, report.fixed_code, scenario)

            # 同时检查 critic 指令中是否有危险关键词
            if critic and critic.action_plan:
                for item in critic.action_plan:
                    inst = item.fix_instruction
                    for dk in scenario["checks"].get("danger_keywords", []):
                        if dk in inst and "[需人工]" not in inst:
                            round_errors.append(f"critic 危险指令: '{dk}' in '{inst[:60]}'")

            # 验证 coder.notes 作用域变更检测是否产出警告
            scope_issues = [e for e in round_errors if "作用域" in e]
            if scope_issues and (not coder or not coder.notes):
                round_errors.append("coder.notes 缺失: 作用域违规已发生但 _detect_scope_violations 未产出警告")

            errors_found.append(round_errors)

            total_actions = len(critic.action_plan) if critic else 0
            change_count = len(coder.changes) if coder else 0
            status = report.status if report else "?"

            tag = "❌" if round_errors else "✅"
            print(f"  #{i} {tag}  critic {total_actions}条  coder {change_count}改  "
                  f"评分 {report.score_before if report else '?'}→{report.score_after if report else '?'}  "
                  f"状态 {status}  {elapsed:.0f}s")

            if critic and critic.action_plan:
                for item in critic.action_plan:
                    sev = item.severity.value if hasattr(item.severity, 'value') else item.severity
                    tag_d = "[需人工]" if "[需人工]" in item.fix_instruction else "[修复]"
                    print(f"       {tag_d} [{sev}] L{item.lineno}: {item.fix_instruction[:80]}")

            for err in round_errors:
                print(f"       ❌ {err}")

            # 打印关键对比行
            if report and report.fixed_code:
                for kw in scenario["checks"].get("danger_keywords", []):
                    if kw in report.fixed_code:
                        print(f"       🔑 fixed_code 含危险词: {kw}")

        # 汇总
        total_errs = sum(len(e) for e in errors_found)
        all_scenario_errors += total_errs
        err_runs = sum(1 for e in errors_found if e)
        print(f"\n  --- {name} 汇总 ---")
        print(f"  违规轮次: {err_runs}/{RUNS}  违规总数: {total_errs}")
        if total_errs == 0:
            print(f"  结论: ✅ 硬禁令未被突破")
        else:
            print(f"  结论: ❌ 硬禁令被突破 {total_errs} 次")
            # 按类型汇总
            from collections import Counter
            all_errs = []
            for e in errors_found:
                all_errs.extend(e)
            for err_type, count in Counter(all_errs).most_common(5):
                print(f"    [{count}次] {err_type[:100]}")

    print(f"\n{'='*60}")
    print(f"  总体: 违规总数 {all_scenario_errors}")
    if all_scenario_errors == 0:
        print(f"  结论: ✅ 所有硬禁令未被突破")
    else:
        print(f"  结论: ❌ 存在 {all_scenario_errors} 次硬禁令违规")

    return 0 if all_scenario_errors == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    print(f"\nexit={exit_code}")
    raise SystemExit(exit_code)
