"""
最终测试 #03：边界情况

验证流水线在极端输入下不崩溃，优雅降级：
- 空代码
- 最小代码（仅 pass / x=1）
- 超长字符串字面量
- 深层嵌套函数
- 仅注释和空行
- Unicode 标识符（中文变量名）

用法：python tests/final/final_03_edge_cases.py
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

EDGE_CASES = {
    "空代码": "",
    "最小代码-pass": "pass",
    "最小代码-赋值": "x = 1",
    "仅注释和空行": """
# 这是注释
# 没有实际代码

""",
    "超长字符串": '''
MSG = """''' + "A" * 500 + '''"""
print(len(MSG))
''',
    "深层嵌套": '''
def outer():
    def mid1():
        def mid2():
            def inner(x):
                return x + 1
            return inner
        return mid2
    return mid1
''',
    "Unicode中文变量": '''
def 你好(名字: str) -> str:
    问候 = f"你好，{名字}"
    return 问候
''',
}


async def run_one(app, code, run_id):
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = code
    config = {"configurable": {"thread_id": f"final03-{run_id}"}}

    current_state = dict(initial_state)
    try:
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
    except Exception as e:
        current_state["_error"] = str(e)

    return current_state


async def main():
    app = build_graph()
    print(f"=== 最终测试 #03：边界情况 ===")
    print(f"  LLM: {LLM_MODEL}\n")

    errors = []
    total_start = time.time()

    for i, (name, code) in enumerate(EDGE_CASES.items(), 1):
        print(f"--- [{i}/{len(EDGE_CASES)}] {name} ---")
        t0 = time.time()
        state = await run_one(app, code.strip(), f"edge-{i}")
        elapsed = time.time() - t0

        # 检查 1：不崩溃（有 final_report 或错误信息）
        report = state.get("final_report")
        run_error = state.get("_error")

        if run_error:
            errors.append(f"{name}: 执行异常 → {run_error[:100]}")
            print(f"  ❌ 异常: {run_error[:100]}")
            print()
            continue

        if not report:
            errors.append(f"{name}: final_report 为 None")
            print(f"  ❌ final_report 为 None\n")
            continue

        # 检查 2：空代码应有合理处理
        if name == "空代码" and report.status not in ("success", "failed"):
            errors.append(f"{name}: 空代码 status 异常: {report.status}")

        # 检查 3：最小代码不应崩溃且评分应较高
        if "最小" in name:
            if not (0 <= report.score_before <= 100):
                errors.append(f"{name}: score 越界: {report.score_before}")

        print(f"  status={report.status}  score {report.score_before}→{report.score_after}")
        print(f"  action_items={len(report.action_items)}  sandbox_passed={report.sandbox_passed}")
        print(f"  elapsed={elapsed:.1f}s  ✅ 未崩溃")
        print()

    total_elapsed = time.time() - total_start

    print(f"{'='*60}")
    print(f"  边界样本: {len(EDGE_CASES)}  异常数: {len(errors)}")
    print(f"  总耗时: {total_elapsed:.1f}s")
    if errors:
        for e in errors:
            print(f"  ❌ {e}")
    print(f"  状态: {'success' if not errors else 'failed'}")

    return 0 if not errors else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    raise SystemExit(exit_code)
