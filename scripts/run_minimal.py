"""
最简运行脚本 —— 只展示 HITL 核心流程，无多余输出
用法：python scripts/run_minimal.py
"""
import sys
import asyncio
import warnings
from pathlib import Path

# 抑制噪音
warnings.filterwarnings("ignore", message=".*allowed_objects.*")
import logging
logging.getLogger("langgraph.checkpoint.serde.jsonplus").setLevel(logging.ERROR)

# 项目路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from config import LLM_MODEL, DEEPSEEK_API_KEY, MAX_RETRY
from graph.builder import build_graph
from graph.state import INITIAL_STATE

SAMPLE_CODE = """
def compute(expression, x):
    return eval(expression)

def load_data(byte_str):
    import pickle
    return pickle.loads(byte_str)
"""


async def main():
    # 1. 构建图 —— 这一步已经写好 checkpointer + interrupt_before
    app = build_graph()

    # 2. 准备 config —— 你写的，标识会话
    config = {"configurable": {"thread_id": "demo-001"}}

    # 3. 准备初始 state
    state = dict(INITIAL_STATE)
    state["original_code"] = SAMPLE_CODE

    # 4. 首次执行 —— 跑到 human_review 前自动暂停
    print("首次 invoke(initial_state, config) → 开始执行...")
    async for event in app.astream_events(state, config, version="v2"):
        if event["event"] == "on_chain_end" and event.get("name"):
            print(f"  [{event['name']}] 完成")
    print("(框架自动暂停在 human_review 前，控制权回到你手里)\n")

    # 5. 检测是否真的暂停了
    snapshot = app.get_state(config)
    print(f"暂停确认: snapshot.next = {snapshot.next}")

    # 6. 模拟用户确认 —— 你写的
    print(">>> 模拟用户点击确认按钮")
    app.update_state(config, {"human_feedback": ""})

    # 7. resume —— 你写的，None 表示从断点继续
    print("resume: invoke(None, config) → 从 human_review 继续...\n")
    async for event in app.astream_events(None, config, version="v2"):
        if event["event"] == "on_chain_end" and event.get("name"):
            print(f"  [{event['name']}] 完成")

    # 8. 最终结果
    final = app.get_state(config)
    report = final.values.get("final_report")
    if report:
        print(f"\n状态: {report.status}, 问题数: {len(report.action_items)}")


if __name__ == "__main__":
    print(f"模型: {LLM_MODEL}, 最大重试: {MAX_RETRY}\n")
    asyncio.run(main())
