"""
B04 验证脚本 #06：regression test —— reflect_node 条件边是否注册了 human_review 分支

在 retry_or_fail 返回 "human_review" 的前提下，确认编译后的图中
reflect_node 的出边包含到 human_review 的映射，不会触发 KeyError。

用法：python tests/bugfix/b04/test_b04_06_routing_edge_regression.py
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*allowed_objects.*")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from graph.builder import build_graph, retry_or_fail
from graph.state import INITIAL_STATE
from config import MAX_RETRY


if __name__ == "__main__":
    print(f"=== B04 验证 #06：reflect_node 条件边 regression test ===")
    print(f"  MAX_RETRY: {MAX_RETRY}")
    print()

    all_passed = True

    # --- 检测 1：retry_or_fail 返回值正确 ---
    print("--- 检测 1：retry_or_fail 返回值 ---")
    for rc in range(MAX_RETRY):
        state = dict(INITIAL_STATE)
        state["retry_count"] = rc
        result = retry_or_fail(state)
        ok = (result == "coder_agent")
        print(f"  {'✅' if ok else '❌'} retry_count={rc} → {result}")
        if not ok:
            all_passed = False

    for rc in [MAX_RETRY, MAX_RETRY + 1, MAX_RETRY + 5]:
        state = dict(INITIAL_STATE)
        state["retry_count"] = rc
        result = retry_or_fail(state)
        ok = (result == "human_review")
        print(f"  {'✅' if ok else '❌'} retry_count={rc} → {result}")
        if not ok:
            all_passed = False
    print()

    # --- 检测 2：编译后的图中 reflect_node → human_review 边存在 ---
    print("--- 检测 2：编译图边结构 ---")
    app = build_graph()
    graph = app.get_graph()
    edges = list(graph.edges)
    nodes = list(graph.nodes.keys())

    print(f"  节点: {nodes}")

    # 找所有从 reflect_node 出发的边
    reflect_targets = [e.target for e in edges if e.source == "reflect_node"]
    print(f"  reflect_node 出边目标: {reflect_targets}")

    # 必须存在 reflect_node → human_review 的边
    if "human_review" in reflect_targets:
        print(f"  ✅ 存在 reflect_node → human_review 边")
    else:
        print(f"  ❌ 缺失 reflect_node → human_review 边（运行时将 KeyError）")
        all_passed = False

    # 必须存在 reflect_node → coder_agent 的边
    if "coder_agent" in reflect_targets:
        print(f"  ✅ 存在 reflect_node → coder_agent 边")
    else:
        print(f"  ❌ 缺失 reflect_node → coder_agent 边")
        all_passed = False

    # 确认旧映射 output_node 已移除
    has_reflect_to_output = "output_node" in reflect_targets
    if has_reflect_to_output:
        print(f"  ⚠️  仍存在旧映射 reflect_node → output_node（非预期，但不会触发 KeyError）")
    else:
        print(f"  ✅ 旧映射 reflect_node → output_node 已移除")
    print()

    # --- 检测 3：sandbox_executor → human_review 边也正常工作（回归保护） ---
    print("--- 检测 3：sandbox_executor 条件边 ---")
    sandbox_targets = [e.target for e in edges if e.source == "sandbox_executor"]
    print(f"  sandbox_executor 出边目标: {sandbox_targets}")

    sandbox_to_human = "human_review" in sandbox_targets
    sandbox_to_reflect = "reflect_node" in sandbox_targets
    if sandbox_to_human and sandbox_to_reflect:
        print(f"  ✅ sandbox_executor 出边正常")
    else:
        print(f"  ❌ sandbox_executor 出边异常")
        all_passed = False
    print()

    print(f"=== B04 验证 #06 {'全部通过' if all_passed else '存在失败'} ===")
    sys.exit(0 if all_passed else 1)
