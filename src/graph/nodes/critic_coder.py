"""汇总评判 + 自动修复节点，含 guard/detect 辅助函数"""

import ast
import re

from graph.state import AgentState
from graph.nodes._llm import llm
from langchain_core.messages import SystemMessage, HumanMessage
from models import CriticSummary, CoderResult, IssueCategory


CREDENTIAL_KEYWORDS = re.compile(
    r"os\.environ|getenv|环境变量|\.env|配置文件|外部存储|密钥管理|Secrets?\s*Manager",
    re.IGNORECASE,
)


def _strip_fake_tags(summary: CriticSummary) -> None:
    """剥离 LLM 自发造的 [修复] 标签，防止下游 coder 因不认识该标签而误入 skipped_items"""
    if not summary.action_plan:
        return
    for item in summary.action_plan:
        if "[需人工]" in item.fix_instruction:
            continue
        if item.fix_instruction.startswith("[修复]"): #判断字符串是否以指定前缀开头，返回 True 或 False
            item.fix_instruction = item.fix_instruction[len("[修复]"):].strip()


def _guard_credential_manual_tag(summary: CriticSummary) -> None:
    """凭据类问题确定性兜底：category=SENSITIVE_INFO + 修复方案涉及外部化 → 强制 [需人工]"""
    if not summary.action_plan:
        return
    for item in summary.action_plan:
        if "[需人工]" in item.fix_instruction:
            continue
        if item.category != IssueCategory.SENSITIVE_INFO:
            continue
        if CREDENTIAL_KEYWORDS.search(item.fix_instruction):
            item.fix_instruction = "[需人工] " + item.fix_instruction


def _detect_scope_violations(original_code: str, fixed_code: str) -> list[str]:
    """检测 coder 是否将函数内语句提升到模块级（改作用域硬禁令兜底）

    ---- 处理流程 ----
    第一步：AST 解析 —— 把原始代码和修复后代码分别解析为抽象语法树
    第二步：收集函数内语句 —— 扫描原始 AST，找到所有函数体内的非 import 语句，存入集合
    第三步：交叉比对 —— 检查修复后代码的模块级（顶层）语句
           若某条语句在第二步的集合中出现过，说明 coder 把它从函数内提到了全局 → 违规

    只放行 import/from import，其余从函数内→模块级的移动一律标记。
    """
    # ===== 第一步：AST 解析 =====
    if not original_code or not fixed_code:
        return []
    try:
        orig_tree = ast.parse(original_code)    # 原始代码 → AST
        fixed_tree = ast.parse(fixed_code)      # 修复后代码 → AST
    except SyntaxError:
        return []

    # ===== 第二步：收集原始代码中所有函数体内的非 import 语句 =====
    def _non_import_stmts_in_funcs(tree):
        stmts = set()
        for node in ast.walk(tree):             # 遍历 AST 每个节点
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for stmt in node.body:          # 函数体内的每条语句
                    if not isinstance(stmt, (ast.Import, ast.ImportFrom)):
                        try:
                            stmts.add(ast.unparse(stmt))  # AST 节点 → 文本，存入集合
                        except Exception:
                            pass
        return stmts                             # 返回集合，如 {"print(x)", "x = 1"}

    func_stmts = _non_import_stmts_in_funcs(orig_tree)

    # ===== 第三步：检查修复后代码的顶层语句是否来自函数内部 =====
    violations = []
    for stmt in fixed_tree.body:                 # 只循环顶层语句（模块级），不深入函数体
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            continue                             # import 提到顶层是允许的，放行
        try:
            unparsed = ast.unparse(stmt)         # AST 节点 → 文本
        except Exception:
            continue
        if unparsed in func_stmts:               # 这条语句本该在函数内，却跑到了顶层
            violations.append(f"[作用域变更] L{stmt.lineno}: {unparsed[:100]}")

    return violations


def critic_agent(state: AgentState)->dict:
    """汇总节点：对三路审查结果去重、排序、评分，输出统一修复方案"""
    structured_llm = llm.with_structured_output(CriticSummary)

    #把每条Issue展开成可读文本，critic需要看到具体内容才能去重
    issues_text = []
    for r in state['review_results']:
        for issue in r.issues:
            #将每个问题的下述信息提取整合成字符串文本，循环将所有问题的文本描述全部整理入issues_text
            issues_text.append(
                f"[{r.dimension.value}] 行{issue.lineno} {issue.severity.value}"
                f" | {issue.category.value} | {issue.description}"
                f"\n 代码：{issue.code_snippet}"
            )

    summary = structured_llm.invoke([
        # [B01] critic 三分类判定：丢弃/[需人工]/修复
        SystemMessage(content =(
            "你是代码审查主管。请对以下问题清单：\n"
            "1. 去重：多条指向同一行号+同类问题的合并为一条。合并后只输出合并结果，被合并条目不得以\"已合并至行X\"\"无需单独处理\"\"参见条目N\"等形式出现在 action_plan 中\n"
            "2. 排序：按严重度(CRITICAL > HIGH > MEDIUM > LOW)优先，同级按行号\n"
            "3. 评分：根据问题数量和严重度打分(0-100)\n"
            "4. 对去重后的每条问题做判定：\n"
            "\n"
            "   第一步：该问题是否影响代码的正确性或安全性？\n"
            "\n"
            "   判断标准：修复该问题能否阻止可能的运行时异常、数据损坏、资源耗尽或安全漏洞？\n"
            "   · 能 → 影响正确性或安全性，不丢弃。\n"
            "   · 不能（仅改善可读性/格式/文档）→ 不影响，丢弃。\n"
            "\n"
            "   丢弃示例（仅在不影响正确性或安全性时适用）：\n"
            "   纯风格、命名偏好、docstring/类型注解/注释缺失、等价写法建议、代码组织建议等。此类问题不得进入 action_plan\n"
            "\n"
            "   如果是 → 按以下两类处理：\n"
            "\n"
            "   [需人工] — 满足任一即标注：\n"
            "   · 问题涉及硬编码凭据/密钥/密码/令牌 → 凭据归宿必须在代码外，单文件改不彻底\n"
            "   · 需要新建文件\n"
            "   · 需要安装新依赖包\n"
            "   · 需要改动当前文件以外的代码\n"
            "   fix_instruction 描述：问题 + 所需基础设施 + 建立后怎么改\n"
            "\n"
            "   修复 — 不属于 [需人工] 的其余问题：\n"
            "   · fix_instruction 必须包含行号 + FROM → TO\n"
            "   · 禁用\"建议\"\"考虑\"\"可改为\"等模糊词"
        )),
        HumanMessage(content =(
            f"原始代码：\n```\n{state['original_code']}\n```\n\n"
            f"问题清单 （共{sum(len(r.issues) for r in state['review_results'])}条）:\n"
            + "\n".join(issues_text)
        )),
    ])
    # [Bug #5] LLM 返回 None 时兜底
    if summary is None:
        return {}
    # 确定性兜底：凭据类问题 LLM 容易误判为 [修复]，枚举 + 关键词双重确认后强制标 [需人工]
    _guard_credential_manual_tag(summary)
    # 剥离 LLM 自发造的 [修复] 标签，防止下游 coder 因不认识该标签而误入 skipped_items
    _strip_fake_tags(summary)
    return {"critic_summary": summary}


def coder_agent(state: AgentState)->dict:
    """修复节点：按action_plan的fix_instruction逐一修改代码，输出CoderResult"""

    structured_llm = llm.with_structured_output(CoderResult)

    #将action_plan的每条修复指令展开成可读文本
    plan_text = []
    skipped_from_critic = []  # [需人工] 条目代码级拦截，不传给 coder
    protected_lines = set()   # 含 [需人工] 条目的行号，整行锁定，防同行的非 [需人工] 条目绕过
    # [Bug #5] 消费端守卫：上游 critic_agent 可能返回 {}，避免 state['critic_summary'].action_plan 炸
    critic = state.get('critic_summary')
    if critic is None:
        return {"coder_result": CoderResult(
            fixed_code=state.get("original_code", ""),
        )}
    for item in critic.action_plan:
        if "[需人工]" in item.fix_instruction:
            protected_lines.add(item.lineno)
            skipped_from_critic.append(f"行{item.lineno}: {item.fix_instruction}")
            continue
    for item in critic.action_plan:
        if "[需人工]" in item.fix_instruction:
            continue  # 已处理
        if item.lineno in protected_lines:
            skipped_from_critic.append(f"行{item.lineno}: (同行动态锁定) {item.fix_instruction}")
            continue
        plan_text.append(
            f" [{item.priority}] 行{item.lineno} | {item.severity.value}/{item.category.value}\n"
            f" 指令：{item.fix_instruction}"
        )

    if not plan_text:
        # 全部条目都是 [需人工]，无需调用 LLM
        return {"coder_result": CoderResult(
            fixed_code=state.get("original_code", ""),
            skipped_items=skipped_from_critic,
        )}

    extra_context = ""
    if state['reflection_notes']:
        extra_context += f"\n\n[上次失败反思]{state['reflection_notes']}"
    if state['human_feedback']:
        extra_context += f"\n\n[用户修改意见]{state['human_feedback']}"

    result = structured_llm.invoke([
        # [B01] coder: 硬禁令二道防线 + 强力兜底
        # 硬禁令（#1-#3）：拦截一切来源，包括 critic 误判，零误杀
        # 兜底："核心原则"+"绝对不能"覆盖防手痒，不再单独列软禁令
        SystemMessage(content=(
            "你是 Python 代码修复专家。\n\n"
            "核心原则：最小改动 —— 只修改 fix_instruction 指定的问题行，其余代码一字不改。\n\n"
            # --- 硬禁令：绝对禁止，fix_instruction 要求也不行 ---
            "硬禁令（以下行为绝对禁止，包括 fix_instruction 要求的情况）：\n"
            "1. 禁止改名 —— 函数名、类名、变量名、参数名一律不动\n"
            "2. 禁止改签名 —— 不增删参数、不改返回类型\n"
            "3. 禁止改作用域 —— 不得把局部变量提升为全局、或把全局降为局部\n\n"
            # --- 执行规则：硬禁令违规静默丢弃 ---
            "判断规则：\n"
            "- 先过硬禁令检查：\n"
            "  · 违反硬禁令（需改名/改签名/改作用域）→ 跳过该条，静默丢弃\n"
            "  · 未违反硬禁令 → 严格按 fix_instruction 逐一修复\n"
            "- 参考优先级：human_feedback > reflection_notes > fix_instruction\n"
            "- 修改后代码必须是可直接运行的合法 Python 代码\n\n"
            # --- 强力兜底：防止任何自发多做 ---
            "你绝对不能做任何 fix_instruction 要求之外的改动。每条修改只触及指令指定的内容，不扩大改动范围。"
        )),
        HumanMessage(content=(
            f"原始代码：\n\n```{state['original_code']}```\n\n"
            f"修复计划：（共{len(plan_text)}条）：\n"
            + "\n".join(plan_text)
            + extra_context
        )),
    ])
    # [Bug #5] LLM 返回 None 时兜底
    if result is None:
        return {"coder_result": CoderResult(
            fixed_code=state.get("original_code", ""),
            skipped_items=skipped_from_critic,
        )}
    # skipped_items 的唯一合法来源是 critic + guard 函数的判定，
    # coder 不应贡献 skipped_items（硬禁令违规 → 静默丢弃，修不了 → 重试）
    result.skipped_items = skipped_from_critic
    # 代码级硬禁令兜底：检测 coder 是否将函数内语句提升到模块级
    scope_violations = _detect_scope_violations(
        state.get("original_code", ""), result.fixed_code
    )
    if scope_violations:
        result.notes = ("[警告] 以下语句被从函数内提升到模块级，可能改变程序行为（如共享连接/状态）：\n"
                        + "\n".join(scope_violations))
    else:
        result.notes = ""
    return {"coder_result": result}
