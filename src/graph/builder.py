# 图构建器 —— build_graph() 组装 10 节点 + 条件边，编译成可运行的 LangGraph 应用

from langgraph.graph import StateGraph, END
from langgraph.types import Send
from langgraph.checkpoint.memory import MemorySaver
from graph.state import AgentState
from graph.nodes import (
    code_parser,
    security_reviewer,
    performance_reviewer,
    style_reviewer,
    critic_agent,
    coder_agent,
    sandbox_executor,
    reflect_node,
    human_review,
    output_node,
)
from config import MAX_RETRY


#三条条件边的路由函数
def should_retry_or_human(state:AgentState)->str:
    """沙箱验证后的路由：通过->人工确认，失败->反思分析"""
    if state["sandbox_result"] and state["sandbox_result"].exit_code == 0:
        return "human_review"
    return "reflect_node"

def should_continue_or_output(state:AgentState)->str:
    """人工确认后的路由：无意见->输出报告，有意见->重新修复"""
    if state["human_feedback"] == "":
        return "output_node"
    return "coder_agent"

def retry_or_fail(state:AgentState)->str:
    """反思后的路由：未达上限->重新修复，已达上限->人工介入"""
    if state["retry_count"] >= MAX_RETRY:
        return "human_review"
    return "coder_agent"

def build_graph():
    """构建并编译代码审查工作流图"""
    #1.创建状态图，AgentState定义了图中流转的数据结构
    workflow = StateGraph(AgentState)

    #2.注册十个节点
    workflow.add_node("code_parser",code_parser)
    workflow.add_node("security_reviewer",security_reviewer)
    workflow.add_node("performance_reviewer",performance_reviewer)
    workflow.add_node("style_reviewer",style_reviewer)
    workflow.add_node("critic_agent",critic_agent)
    workflow.add_node("coder_agent",coder_agent)
    workflow.add_node("sandbox_executor",sandbox_executor)
    workflow.add_node("reflect_node",reflect_node)
    workflow.add_node("human_review",human_review)
    workflow.add_node("output_node",output_node)

    #3.设置入口节点
    workflow.set_entry_point("code_parser")

    #4.并行分发：code_parser -> Send API ->三路审查员
        # 4.1 Send 分发函数
	    # [Bug #1] Send 第二个参数是目标分支的全部 state，必须显式传 original_code，否则分支 KeyError
    def fanout_to_reviewers(state: AgentState) -> list[Send]:
        return [
            Send("security_reviewer", {
                "code_analysis": state["code_analysis"],
                "original_code": state["original_code"],
            }),
            Send("performance_reviewer", {
                "code_analysis": state["code_analysis"],
                "original_code": state["original_code"],
            }),
            Send("style_reviewer", {
                "code_analysis": state["code_analysis"],
                "original_code": state["original_code"],
            }),
        ]

    # 4.2 添加 Send 分发边
    workflow.add_conditional_edges(
        "code_parser",
        fanout_to_reviewers,
        ["security_reviewer","performance_reviewer","style_reviewer"],
    )

    # 5. Reduce 汇聚：三个审查员全部完成后 → critic_agent
    workflow.add_edge("security_reviewer", "critic_agent")
    workflow.add_edge("performance_reviewer", "critic_agent")
    workflow.add_edge("style_reviewer", "critic_agent")

    # 6. 直线推进：汇总 → 修复 → 沙箱验证
    workflow.add_edge("critic_agent", "coder_agent")
    workflow.add_edge("coder_agent", "sandbox_executor")

    # 7. 沙箱条件路由：通过 → 人工确认，失败 → 反思分析
    workflow.add_conditional_edges(
        "sandbox_executor",
        should_retry_or_human,
        {
            "human_review": "human_review",
            "reflect_node": "reflect_node",
        }
    )

    # 8. 人工确认条件路由：无意见 → 输出报告，有意见 → 重新修复
    workflow.add_conditional_edges(
        "human_review",
        should_continue_or_output,
        {
            "output_node": "output_node",
            "coder_agent": "coder_agent",
        }
    )

    # 9. 反思条件路由：未达上限 → 重新修复，已达上限 → 输出失败报告
    workflow.add_conditional_edges(
        "reflect_node",
        retry_or_fail,
        {
            "human_review": "human_review",
            "coder_agent": "coder_agent",
        }
    )

    # 10. 编译图：设置 HITL 断点 + 内存检查点
    app = workflow.compile(
    interrupt_before=["human_review"],
    checkpointer=MemorySaver(), #每次节点执行完后自动保存 state 快照(存于内存)
    )

    return app