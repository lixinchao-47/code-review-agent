"""入口解析 + 三路并行审查节点"""

from graph.state import AgentState
from graph.nodes._llm import llm
from langchain_core.messages import SystemMessage, HumanMessage
from models import CodeAnalysis, ReviewResult, ReviewDimension


def code_parser(state: AgentState)->dict:
    """入口节点：用llm提取原始代码的结构化信息，输出CodeAnalysis"""
    structured_llm = llm.with_structured_output(CodeAnalysis)
    analysis = structured_llm.invoke([
        SystemMessage(content = "你是一个代码结构分析专家，只做客观的结构提取，不给审查意见。"),
        HumanMessage(content = f"请分析一下代码结构：\n\n\n```{state['original_code']}```\n"),
    ])
    # [Bug #5] LLM 结构化输出解析失败时返回 None，兜底为空 CodeAnalysis
    if analysis is None:
        analysis = CodeAnalysis()
    return {"code_analysis" : analysis}


def security_reviewer(state: AgentState)->dict:
    """安全审查员:从注入/加密/权限等角度审查代码"""
    structured_llm = llm.with_structured_output(ReviewResult)
    result = structured_llm.invoke([
        SystemMessage(content = (
            "你是资深安全审计专家。只报告确认存在的安全漏洞，不推测潜在风险。\n\n"
            "确认标准 —— 必须同时满足：\n"
            "1. 代码中存在危险操作（具体函数/模式见下方）\n"
            "2. 该危险操作的输入来自不可信数据源（用户输入/外部请求/文件读取）\n"
            "仅满足一条 → 不报告。\n\n"
            "危险操作清单：\n\n"
            "· SQL 拼接执行 — cursor.execute(sql_string) / raw() / extra() 且 sql_string 含用户输入\n"
            "  排除：参数化查询 cursor.execute(sql, (user_input,))\n\n"
            "· 命令执行 — os.system() / subprocess.run(shell=True) / subprocess.Popen(shell=True) / eval() / exec() / compile()\n"
            "  排除：subprocess 调用使用参数列表且无 shell=True 的情况\n\n"
            "· 路径拼接 — open(user_input) / open(path + user_input) 无校验\n"
            "  排除：open(\"config.json\") / open(os.path.join(BASE, x)) 路径前缀固定的\n\n"
            "· 硬编码凭据 — 代码中出现 password=\"xxx\" / api_key=\"sk-xxx\" / secret=\"xxx\" 等固定字符串\n"
            "  这是唯一不需要攻击面的条目 —— 凭据本身即是漏洞\n\n"
            "· 不安全反序列化 — pickle.load() / yaml.load() / marshal.load()\n"
            "  排除：json.load/loads（安全，不构成反序列化漏洞）\n\n"
            "· 不安全加密 — MD5/SHA1 做密码哈希 / 硬编码加密盐或 IV\n\n"
            "严重度：\n"
            "  CRITICAL — sql拼接/命令注入/硬编码生产凭据/pickle.load(用户输入)\n"
            "  HIGH — 其他确认漏洞\n"
            "  MEDIUM — 确认存在但危害低（如只读路径遍历，无法写入或执行）\n\n"
            "无确认漏洞 → issues 返回空列表 []\n"
            "不确定 → 不报告\n"
            "禁止\"可能\"\"潜在\"\"建议加强\"等推测措辞"
        )),
        HumanMessage(content = f"代码结构：{state['code_analysis']}\n\n原始代码：{state['original_code']}"),
    ])
    # [Bug #5] LLM 返回 None 时兜底为空列表
    if result is None:
        return {"review_results": []}
    # [Bug #4] 节点硬赋值 dimension，防止 LLM 把維度值写错
    result.dimension = ReviewDimension.SECURITY
    return {"review_results" : [result]}


def performance_reviewer(state: AgentState)->dict:
    """性能审查员：从时间复杂度/IO/重复计算等角度审查代码"""
    structured_llm = llm.with_structured_output(ReviewResult)
    result = structured_llm.invoke([
        SystemMessage(content = (
            "你是资深 Python 性能优化专家。审查代码中的性能问题，遵循以下原则：\n\n"
            "核心原则：只报告从代码本身可直接确认的低效模式。\n"
            " - 确定：循环内字符串 += 拼接 → 无论数据多大，都比 join 差。可以确认。\n"
            " - 不确定：如果数据量大可能会慢 → 无法从代码确认规模，不报告。\n"
            " - 不确定：建议用多线程加速 → 无法从代码确认是否有 IO 等待，不报告。\n"
            " - 如果无法确认是否存在性能问题 → 不报告，宁漏勿错。\n\n"
            "按以下五个维度审查：\n\n"
            "1. 时间复杂度 — 嵌套循环可用扁平化替代、O(n) 操作误写成 O(n²)\n"
            "2. 空间复杂度 — 不必要的中介列表/字典、一次性加载大文件可用迭代器\n"
            "3. I/O — 循环内重复打开文件、重复查询数据库\n"
            "4. 数据结构 — 列表查成员(if x in list)、list.pop(0) 等 O(n) 误用\n"
            "5. 重复计算 — 循环内重复调用同一纯函数、循环内不变的属性反复访问\n\n"
            "严重度：\n"
            " - CRITICAL — 循环内创建数据库连接 / 循环内 N+1 查询\n"
            " - HIGH — 循环内重复 IO / 不必要的大对象拷贝 / O(n) 操作套 O(n)\n"
            " - MEDIUM — 低效数据结构(list 查成员) / 一般重复计算\n"
            " - LOW — 微小优化点，不改也可\n\n"
            "estimated_impact：尽量给出量化预估，格式如\"O(n²)→O(n)，n=10000 时提升约 100 倍\"。无法预估则留空。\n\n"
            "无确认问题 → issues 返回空列表 []。"
        )),
        HumanMessage(content = f"代码结构：{state['code_analysis']}\n\n原始代码：{state['original_code']}"),
    ])
    # [Bug #5] LLM 返回 None 时兜底为空列表
    if result is None:
        return {"review_results": []}
    # [Bug #4] 节点硬赋值 dimension
    result.dimension = ReviewDimension.PERFORMANCE
    return {"review_results" : [result]}


def style_reviewer(state: AgentState) -> dict:
    """风格审查员：从命名/格式/PEP 8等角度审查代码"""
    structured_llm = llm.with_structured_output(ReviewResult)
    result = structured_llm.invoke([
        SystemMessage(content=(
            "你是资深 Python 代码规范专家。审查代码中的风格问题。\n\n"
            "核心原则：报告客观的风格违规，不报告个人偏好。\n\n"
            "什么算客观违规：\n"
            " - 确定。违反 PEP 8 明确规定（行太长、命名格式、空白符等）\n"
            " - 确定。缺少必要的文档字符串或类型注解\n"
            " - 不确定。这个命名不够好 → 仅当命名明显误导时报告，品味差异不报\n"
            " - 不确定。这里可以更 Pythonic → 有明确 PEP 8/社区惯例支持则报，否则不报\n"
            " - 无法确认是否为客观违规 → 不报告，宁漏勿错\n\n"
            "按以下维度审查：\n"
            "1. 命名 — snake_case / CamelCase 约定\n"
            "2. 类型注解 — 公开函数是否缺少类型注解\n"
            "3. 格式 — 行长度、空行、缩进\n"
            "4. 注释 — 文档字符串缺失、注释与代码矛盾\n"
            "5. 函数设计 — 函数过长、参数过多\n"
            "6. 重复代码 — copy-paste 重复\n"
            "7. 异常处理 — bare except / 捕获过宽\n\n"
            "严重度：\n"
            "- HIGH — 可能导致 bug 的风格问题（bare except、注释与代码矛盾）\n"
            "- MEDIUM — 命名/格式违反 PEP 8\n"
            "- LOW — 建议性改进\n\n"
            "pep8_ref：违反 PEP 8 时填写条目编号如 \"E501\"。非 PEP 8 问题留空。\n\n"
            "无确认问题 → issues 返回空列表 []。"
        )),
        HumanMessage(content=f"代码结构：{state['code_analysis']}\n\n原始代码：{state['original_code']}"),
    ])
    # [Bug #5] LLM 返回 None 时兜底为空列表
    if result is None:
        return {"review_results": []}
    # [Bug #4] 节点硬赋值 dimension
    result.dimension = ReviewDimension.STYLE
    return {"review_results": [result]}
