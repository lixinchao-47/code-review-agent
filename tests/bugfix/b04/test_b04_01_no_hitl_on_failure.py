"""
B04 验证脚本 #01：重试耗尽后路由检测（直接测路由函数，不依赖 LLM）

旧行为：retry_or_human 在 retry_count >= MAX_RETRY 时返回 "output_node"
新行为：retry_or_human 在 retry_count >= MAX_RETRY 时返回 "human_review"

用法：python tests/bugfix/b04/test_b04_01_no_hitl_on_failure.py
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*allowed_objects.*")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from config import MAX_RETRY
from graph.state import INITIAL_STATE, AgentState
from graph.builder import retry_or_human


def build_mock_state(retry_count):
    """构造最小 state，仅填充 retry_or_human 需要的字段"""
    state = dict(INITIAL_STATE)
    state["retry_count"] = retry_count
    return state


if __name__ == "__main__":
    print(f"=== B04 验证 #01：retry_or_human 路由检测 ===")
    print(f"  MAX_RETRY: {MAX_RETRY}")
    print()

    all_passed = True

    # 检测 1：retry_count < MAX_RETRY → 继续 coder_agent
    print("--- 检测 1：未达上限，继续自动重试 ---")
    for rc in range(MAX_RETRY):
        state = build_mock_state(rc)
        result = retry_or_human(state)
        status = "✅" if result == "coder_agent" else "❌"
        if result != "coder_agent":
            all_passed = False
        print(f"  retry_count={rc} → {result}  {status}")
    print()

    # 检测 2：retry_count >= MAX_RETRY → human_review（不是 output_node）
    print(f"--- 检测 2：达到上限，进入人工介入 ---")
    for rc in [MAX_RETRY, MAX_RETRY + 1, MAX_RETRY + 5]:
        state = build_mock_state(rc)
        result = retry_or_human(state)
        if result == "human_review":
            print(f"  ✅ retry_count={rc} → {result}")
        elif result == "output_node":
            print(f"  ❌ B04 Bug：retry_count={rc} → {result}（应为 human_review）")
            all_passed = False
        else:
            print(f"  ❌ 意外路由：retry_count={rc} → {result}")
            all_passed = False
    print()

    # 检测 3：确认不会返回 output_node
    print("--- 检测 3：retry_or_human 永远不返回 output_node ---")
    returns_output = False
    for rc in range(MAX_RETRY + 5):
        state = build_mock_state(rc)
        if retry_or_human(state) == "output_node":
            returns_output = True
            print(f"  ❌ retry_count={rc} 返回了 output_node")
            all_passed = False
    if not returns_output:
        print(f"  ✅ retry_or_human 在所有 retry_count 值下均不返回 output_node")
    print()

    # 检测 4：图结构中 retry_or_human 的条件边注册正确
    print("--- 检测 4：条件边注册了 human_review 分支 ---")
    from graph.builder import build_graph
    app = build_graph()
    # 获取编译后图的节点列表
    nodes = app.get_graph().nodes
    node_names = list(nodes.keys())
    print(f"  图节点: {node_names}")
    if "human_review" in node_names and "reflect_node" in node_names:
        print(f"  ✅ reflect_node 和 human_review 都在图中")
    else:
        print(f"  ❌ 缺少必要节点")
        all_passed = False
    print()

    print(f"=== B04 验证 #01 {'全部通过' if all_passed else '存在失败'} ===")
    sys.exit(0 if all_passed else 1)
