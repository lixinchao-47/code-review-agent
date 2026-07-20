"""
项目统一入口 —— 驱动完整代码审查工作流
用法：python scripts/run.py
"""
import sys
import asyncio
import time
import warnings
from pathlib import Path

# [2026-05-15] 抑制 LangGraph allowed_objects 弃用警告 + Deserializing 日志噪音
warnings.filterwarnings("ignore", message=".*allowed_objects.*")
import logging
logging.getLogger("langgraph.checkpoint.serde.jsonplus").setLevel(logging.ERROR)

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 把 src/ 加入 Python 搜索路径
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from config import LLM_MODEL, DEEPSEEK_API_KEY, MAX_RETRY
from graph.builder import build_graph
from graph.state import INITIAL_STATE

# 测试用示例代码 —— 故意放了几个问题（硬编码密钥、pickle 反序列化、裸 except、无类型注解）
SAMPLE_CODE = """
def compute(expression, x):
    return eval(expression)

def run_task(code_str):
    exec(code_str)

def load_data(byte_str):
    import pickle
    return pickle.loads(byte_str)
"""

async def run_with_timing(app, config, initial_state):
    """用 astream_events 运行工作流，记录每个节点的开始/结束时间"""
    node_times: dict[str, float] = {}   # 节点名 → 开始时间
    total_cost: dict[str, float] = {}   # 节点名 → 累计耗时

    async def stream_until_pause(state, cfg):
        """流式执行直到暂停或结束，返回最后 state（字典）"""
        current_state = dict(state) if state else {}
        async for event in app.astream_events(state, cfg, version="v2"):
            kind = event["event"]
            name = event.get("name", "")

            if kind == "on_chain_start" and name in [
                "code_parser", "security_reviewer", "performance_reviewer",
                "style_reviewer", "critic_agent", "coder_agent",
                "sandbox_executor", "reflect_node", "human_review", "output_node",
            ]:
                node_times[name] = time.time()
                print(f"  ⏵ [{name}] 开始...")

            elif kind == "on_chain_end" and name in node_times:
                elapsed = time.time() - node_times.pop(name)
                total_cost[name] = total_cost.get(name, 0) + elapsed
                print(f"  ⏹ [{name}] 完成 ({elapsed:.1f}s)")

                # 从 event output 收集 state 变更
                output = event["data"].get("output", {})
                if isinstance(output, dict):
                    for k, v in output.items():
                        if k in AgentState.__annotations__:
                            current_state[k] = v

        return current_state

    # 第一轮执行
    print("正在执行审查流程...")
    state = await stream_until_pause(initial_state, config)

    # [Bug #2] HITL 中断检测：interrupt_before 不抛异常，必须通过 checkpointer 的 .next 判断是否暂停
    state_snapshot = app.get_state(config)
    if state_snapshot.next: #interrupt_before声明过的节点才会出现在里面
        print(">>> 暂停在 human_review 节点，等待人工确认...")
        print(">>> (演示模式) 自动批准修复结果")
        app.update_state(config, {"human_feedback": ""})
        state = await stream_until_pause(None, config)

    return state, total_cost


if __name__ == "__main__":
    print(f"=== 代码审查 Agent 启动 ===")
    print(f"  LLM 模型: {LLM_MODEL}")
    print(f"  API Key 已加载: {bool(DEEPSEEK_API_KEY)}")
    print(f"  最大重试次数: {MAX_RETRY}")
    print()

    # 1. 构建编译图
    print("正在构建工作流图...")
    app = build_graph()
    print("图编译完成，10 个节点 + 条件边已就位")
    print()

    # 2. 准备输入
    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = SAMPLE_CODE

    config = {"configurable": {"thread_id": "demo-001"}}

    print("=== 待审查代码 ===")
    print(SAMPLE_CODE)
    print()

    # 3. 运行工作流 + 计时
    from graph.state import AgentState
    result, cost = asyncio.run(run_with_timing(app, config, initial_state))

    # 4. 打印并行计时总结
    print()
    print("=== 节点耗时统计 ===")
    for node_name in [
        "code_parser", "security_reviewer", "performance_reviewer",
        "style_reviewer", "critic_agent", "coder_agent",
        "sandbox_executor", "reflect_node", "human_review", "output_node",
    ]:
        if node_name in cost:
            print(f"  {node_name:25s} {cost[node_name]:.1f}s")
    # 并行检测：三个审查员耗时接近且小于各自之和，说明并行
    reviewers = ["security_reviewer", "performance_reviewer", "style_reviewer"]
    if all(r in cost for r in reviewers):
        r_times = [cost[r] for r in reviewers]
        total = sum(r_times)
        max_t = max(r_times)
        print()
        print(f"  三审查员耗时: {r_times[0]:.1f}s / {r_times[1]:.1f}s / {r_times[2]:.1f}s")
        print(f"  三路串行预估: {total:.1f}s  实际并行窗口: {max_t:.1f}s")
        if max_t <= total * 0.6:
            print(f"  ✅ 并行生效：三路几乎同时完成，省了 ~{total - max_t:.0f}s")

    # 5. 输出最终报告
    print()
    print("=== 最终审查报告 ===")
    report = result.get("final_report")
    if report:
        print(f"  状态: {report.status}")
        print(f"  修复前评分: {report.score_before}")
        print(f"  修复后评分: {report.score_after}")
        print(f"  沙箱通过: {report.sandbox_passed}")
        print(f"  重试次数: {report.retry_count}")
        print(f"  问题数: {len(report.action_items)}")
        print(f"  摘要: {report.summary}")
        if report.fixed_code:
            print()
            print("=== 修复后代码 ===")
            print(report.fixed_code)
    else:
        print("报告未生成，请检查上游流程")
