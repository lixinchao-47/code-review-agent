"""反思 + HITL + 输出节点"""

from config import DEEPSEEK_API_KEY, LLM_MODEL
from langchain_deepseek import ChatDeepSeek
from langchain_core.messages import SystemMessage, HumanMessage
from graph.state import AgentState
from models import ReflectionResult, FinalReport


def reflect_node(state: AgentState)->dict:
    """反思节点：分析沙箱失败原因，生成新的修复思路，retry_count+1"""

    reflect_llm = ChatDeepSeek(
        api_key=DEEPSEEK_API_KEY,
        model=LLM_MODEL,
        temperature=0.3,  #反思需要一点发散
    )
    structured_llm = reflect_llm.with_structured_output(ReflectionResult)

    #将上一轮修改记录展开成可读文本
    # [Bug #5] 消费端守卫：上游 coder_agent 可能返回 {}
    coder = state.get('coder_result')
    changes_text = []
    if coder is not None:
        for ref in coder.changes:
            changes_text.append(
                f"行{ref.lineno}: {ref.original} ->{ref.fixed}（{ref.reason}）"
            )

    reflection = structured_llm.invoke([
        SystemMessage(content=(
            "你是一个调试专家。修复后的代码在沙箱中执行失败了。\n\n"
            "请按以下步骤分析：\n"
            "1. 判断失败类型（syntax_error / logic_error / new_bug / env_issue）\n"
            "2. root_cause：指出具体哪处修改导致了失败，引用修改记录中的行号和内容\n"
            "3. should_revert：该修改是否需要回退（语法错误/引入新 bug → true；逻辑错误可换方向 → false）\n"
            "4. new_strategy：给出新的修复策略。必须包含：目标行号 + 具体修改方向（FROM → TO）\n"
            "   禁止模糊表述如\"重新检查\"\"调整修复方案\"\"仔细分析\"——coder 需要可执行的指令。"
        )),
        HumanMessage(content=(
            f"原始代码：\n\n```{state['original_code']}```\n\n"
            f"上一轮修改：\n"+"\n".join(changes_text)+"\n\n"
            f"沙箱执行结果：exit_code={state['sandbox_result'].exit_code}\n"
            f"stdout={state['sandbox_result'].stdout}\n"
            f"stderr={state['sandbox_result'].stderr}\n"
        )),
    ])
    # [Bug #5] LLM 返回 None 时兜底
    if reflection is None:
        return {
            'reflection_notes': 'LLM 返回为空，无法分析失败原因',
            'retry_count': state['retry_count']+1,
        }
    return {
        'reflection_notes': reflection.new_strategy,
        'retry_count': state['retry_count']+1,
    }


def human_review(state: AgentState) -> dict:
    """HITL 节点：LangGraph 在进入前自动暂停，等待用户确认或输入修改意见

    用户确认（无意见）：human_feedback = ""  → 路由到 output_node
    用户有修改意见：human_feedback = "xxx" → 路由回 coder_agent
    human_feedback 在 graph.update_state() 时已写入，此节点直接透传。
    """
    return {}


def output_node(state: AgentState) -> dict:
    """输出节点：组装 FinalReport，不调 LLM"""
    coder = state.get('coder_result')
    sandbox = state.get('sandbox_result')
    critic = state.get('critic_summary')

    fixed_code = coder.fixed_code if coder else ""
    changes = coder.changes if coder else []
    sandbox_passed = sandbox.passed if sandbox else False
    # [B01-#04] 收集 [需人工] 的建议
    skipped = coder.skipped_items if coder else []

    score_before = critic.score_before if critic else 100
    if sandbox_passed:
        if changes:
            # [B03] 每处修复 +2，提升上限为剩余空间的一半（不过度膨胀）
            improvement = min(len(changes) * 2, (100 - score_before) // 2)
            score_after = min(score_before + improvement, 100)
        else:
            score_after = score_before
    else:
        # [B03] 沙箱失败扣 10 分
        score_after = max(score_before - 10, 0)

    # [B01-#04] 状态判定：有跳过项 → partial（沙箱通过但含 [需人工] 建议）
    if not sandbox_passed:
        status = "failed"
    elif skipped:
        status = "partial"
    else:
        status = "success"

    report = FinalReport(
        original_code=state['original_code'],
        fixed_code=fixed_code,
        action_items=critic.action_plan if critic else [],
        score_before=score_before,
        score_after=score_after,
        sandbox_passed=sandbox_passed,
        retry_count=state['retry_count'],
        summary=critic.summary if critic else "",
        status=status,
        skipped_items=skipped,  # [B01-#04] 透传需人工介入的建议
        notes=coder.notes if coder else "",  # 透传审查警告（作用域变更等）
    )
    return {
        'final_report': report,
        'status': report.status,
    }
