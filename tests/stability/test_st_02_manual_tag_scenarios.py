"""测试不同场景下 [需人工] 标签的稳定性 — 每场景跑 5 次"""
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
# 测试场景定义
# ============================================================

SCENARIOS = [
    {
        "name": "硬编码 API Key",
        "code": """
API_KEY = "sk-proj-abc123def456ghi789"

def call_api(endpoint: str):
    headers = {"Authorization": f"Bearer {API_KEY}"}
    return headers
""",
        "expect_manual": True,
        "reason": "凭据/密钥类问题，必须在代码外存储，单文件改不彻底",
    },
    {
        "name": "硬编码数据库 URL（含密码）",
        "code": """
DATABASE_URL = "postgresql://admin:secret123@db.internal:5432/production"

def get_connection():
    return DATABASE_URL
""",
        "expect_manual": True,
        "reason": "连接字符串含密码，属于凭据类问题",
    },
    {
        "name": "MD5 密码哈希（需 bcrypt）",
        "code": """
import hashlib

def hash_password(password: str) -> str:
    return hashlib.md5(password.encode()).hexdigest()
""",
        "expect_manual": True,
        "reason": "MD5 做密码哈希不安全，bcrypt 需要 pip install",
    },
    {
        "name": "干净代码（对照组）",
        "code": """
def add(a: int, b: int) -> int:
    return a + b
""",
        "expect_manual": False,
        "reason": "无问题代码，不应出现 [需人工]",
    },
]


ALL_NODES = [
    "code_parser", "security_reviewer", "performance_reviewer",
    "style_reviewer", "critic_agent", "coder_agent",
    "sandbox_executor", "reflect_node", "human_review", "output_node",
]


async def run_one(app, code, run_id):
    """跑一次完整审查（自动批准 HITL）"""
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = code
    config = {"configurable": {"thread_id": f"st-sc-{run_id}"}}

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


async def test_scenario(app, scenario: dict, runs: int = 5):
    """对单个场景跑 N 次，返回 [需人工] 统计"""
    name = scenario["name"]
    code = scenario["code"]
    expect = scenario["expect_manual"]

    print(f"\n{'='*60}")
    print(f"  场景: {name}")
    print(f"  预期: {'应标 [需人工]' if expect else '不应标 [需人工]'}")
    print(f"  原因: {scenario['reason']}")
    print(f"{'='*60}")

    manual_counts = []
    details = []

    for i in range(1, runs + 1):
        t0 = time.time()
        state = await run_one(app, code, i)
        elapsed = time.time() - t0

        critic = state.get("critic_summary")
        report = state.get("final_report")

        manual_items = []
        all_actions = []
        if critic and critic.action_plan:
            for item in critic.action_plan:
                sev = item.severity.value if hasattr(item.severity, 'value') else str(item.severity)
                cat = str(item.category) if hasattr(item, 'category') else "?"
                desc = item.description[:50]
                has_manual = "[需人工]" in item.fix_instruction
                tag = "[需人工]" if has_manual else "[修复]"
                all_actions.append(f"{tag} [{sev}] {cat}: {desc}")
                if has_manual:
                    manual_items.append(desc)

        manual_counts.append(len(manual_items))

        print(f"  #{i}: [需人工] {len(manual_items)} 条  "
              f"评分 {report.score_before if report else '?'}→{report.score_after if report else '?'}  "
              f"用时 {elapsed:.0f}s")
        for a in all_actions[:5]:
            print(f"       {a}")

        # 打印修复后的关键行
        if report and report.fixed_code:
            for line in report.fixed_code.split("\n"):
                stripped = line.strip()
                if any(kw in stripped.upper() for kw in ["PASSWORD", "API_KEY", "SECRET", "DATABASE_URL", "HASH", "MD5", "GETENV", "ENVIRON"]):
                    print(f"       🔑 {stripped}")

        details.append({
            "run": i,
            "manual_count": len(manual_items),
            "score_before": report.score_before if report else 0,
            "score_after": report.score_after if report else 0,
            "total_actions": len(critic.action_plan) if critic else 0,
        })

    print()
    print(f"  --- {name} 汇总 ---")
    print(f"  各轮 [需人工] 数: {manual_counts}")
    hit_count = sum(1 for c in manual_counts if c > 0)
    print(f"  至少标 1 个: {hit_count}/{runs} ({hit_count/runs*100:.0f}%)")

    if expect:
        if hit_count == runs:
            print(f"  结论: ✅ 全部命中，标签稳定")
        elif hit_count >= runs * 0.8:
            print(f"  结论: 🟡 大部分命中，有小幅波动")
        else:
            print(f"  结论: ❌ 波动较大，标签不稳定")
    else:
        if hit_count == 0:
            print(f"  结论: ✅ 无 [需人工]，符合预期")
        else:
            print(f"  结论: ❌ 出现了非预期的 [需人工]")

    return {
        "name": name,
        "expect": expect,
        "manual_counts": manual_counts,
        "hit_rate": hit_count / runs,
    }


async def main():
    app = build_graph()
    print(f"=== [需人工] 标签稳定性测试 ===")
    print(f"  LLM: {LLM_MODEL}  MAX_RETRY: {MAX_RETRY}  temperature: 0.0")
    print(f"  每场景跑 5 次，共 {len(SCENARIOS)} 个场景")

    all_results = []
    for scenario in SCENARIOS:
        result = await test_scenario(app, scenario, runs=5)
        all_results.append(result)

    print(f"\n{'='*60}")
    print(f"  总体汇总")
    print(f"{'='*60}")
    for r in all_results:
        status = "✅" if (r["expect"] and r["hit_rate"] >= 0.8) or (not r["expect"] and r["hit_rate"] == 0) else "❌"
        print(f"  {status} {r['name']}: 命中率 {r['hit_rate']*100:.0f}%  {r['manual_counts']}")


if __name__ == "__main__":
    asyncio.run(main())
