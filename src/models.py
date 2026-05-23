# Pydantic 数据模型与枚举 —— 定义整个系统的数据结构（Issue、ReviewResult、FinalReport 等 9 个模型 + 4 个枚举）

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

class Severity(str, Enum):
    """问题严重程度，决定 critic 排序优先级"""
    CRITICAL = "critical"   # 严重漏洞/导致系统崩溃
    HIGH = "high"           #重要问题
    MEDIUM = "medium"       #一般问题
    LOW = "low"             #轻微/建议性

class ReviewDimension(str, Enum):
    """审查维度，标记审查结果来自哪个审查员"""
    SECURITY = "security"
    PERFORMANCE = "performance"
    STYLE = "style"

class IssueCategory(str, Enum):
    """问题分类，比 dimension 更细一层，用于 critic 去重判断"""
    # 安全
    INJECTION = "注入"
    SENSITIVE_INFO = "敏感信息"
    ENCRYPTION = "加密"
    PERMISSION = "权限"
    DESERIALIZATION ="反序列化"
    #性能
    TIME_COMPLEXITY = "时间复杂度"
    SPACE_COMPLEXITY = "空间复杂度"
    IO = "I/O"
    DATA_STRUCTURE = "数据结构"
    DUPLICATE_COMPUTATION = "重复计算"
    #风格  
    NAMING = "命名"
    FUNCTION_DESIGN ="函数设计"
    COMMENT ="注释"
    DUPLICATE_CODE = "重复"
    EXCEPTION = "异常"                                                                                 
    TYPE_HINT = "类型"                                                                                 
    FORMAT = "格式"                                                                                    
    OTHER = "其他"

class FailureType(str, Enum):
    """沙箱失败类型，reflect_node 分析失败原因时使用"""                                                                          
    SYNTAX_ERROR = "syntax_error"    # 修出了语法错误                                                  
    LOGIC_ERROR = "logic_error"      # 代码能跑但结果不对                                              
    NEW_BUG = "new_bug"              # 修复引入了新 bug                                                
    ENV_ISSUE = "env_issue"          # 沙箱环境问题，与代码无关


# ============================================================
# code_parser 输出
# ============================================================

class FunctionInfo(BaseModel):
    """单个函数的结构化描述 —— code_parser 提取每个函数的基本信息"""
    name: str                                         # 函数名
    lineno: int                                       # 起始行号，审查员按此定位源码
    params: list[str] = Field(default_factory=list)   # 参数名列表
    decorators: list[str] = Field(default_factory=list)  # 装饰器，如 @staticmethod
    docstring: Optional[str] = None                   # 文档字符串，可能不存在
    body_summary: str = ""                            # 函数体做什么的一句话摘要

    # [B00] LLM 遗漏 name/lineno 时兜底
    @field_validator("name", mode="before")
    @classmethod
    def missing_name_fallback(cls, v):
        return v if v is not None else ""

    @field_validator("lineno", mode="before")
    @classmethod
    def missing_lineno_fallback(cls, v):
        return v if v is not None else 0


class ClassInfo(BaseModel):
    """单个类的结构化描述 —— code_parser 提取每个类的基本信息"""
    name: str                                         # 类名
    lineno: int                                       # 起始行号
    methods: list[str] = Field(default_factory=list)  # 方法名列表
    base_classes: list[str] = Field(default_factory=list)  # 父类列表
    docstring: Optional[str] = None                   # 文档字符串，可能不存在

    # [B00] LLM 遗漏 name/lineno 时兜底
    @field_validator("name", mode="before")
    @classmethod
    def missing_name_fallback(cls, v):
        return v if v is not None else ""

    @field_validator("lineno", mode="before")
    @classmethod
    def missing_lineno_fallback(cls, v):
        return v if v is not None else 0


class CodeAnalysis(BaseModel):
    """代码解析结果 —— 只做客观描述，不给审查意见，供所有审查员共享"""
    functions: list[FunctionInfo] = Field(default_factory=list)   # 所有函数
    classes: list[ClassInfo] = Field(default_factory=list)        # 所有类
    imports: list[str] = Field(default_factory=list)              # 导入语句，如 "import os"
    global_statements: list[str] = Field(default_factory=list)    # 模块级关键操作描述
    overview: str = ""                                            # 一句话总结代码功能


# ============================================================
# 审查 Agent 输出 — 三个审查员共用---（其实就是问题模板）
# ============================================================

class Issue(BaseModel):
    """单个审查发现的问题，安全/性能/风格三个审查员共用"""
    severity: Severity                         # 严重程度，只能是 Severity 枚举的 4 种值
    category: IssueCategory                    # 问题分类，对应 IssueCategory 的 17 种分类
    lineno: int                                # 问题所在行号，审查员用它定位源码
    code_snippet: str                          # 问题代码原文，critic 去重时对比此字段
    description: str                           # 自然语言描述：这里为什么有问题
    cwe_id: Optional[str] = None               # [安全专用] CWE 漏洞编号，如 "CWE-89"=SQL注入
    estimated_impact: Optional[str] = None     # [性能专用] 预估性能影响，如 "从 3s 降至 0.1s"
    pep8_ref: Optional[str] = None             # [风格专用] 违反的 PEP 8 条目，如 "E501"=行太长

    # [Bug #4] LLM 返回非法枚举值（如"安全"）时 fallback 到 MEDIUM
    @field_validator("severity", mode="before")
    @classmethod
    def unknown_severity_fallback(cls, v):
        try:
            return Severity(v) if isinstance(v, str) else v
        except ValueError:
            return Severity.MEDIUM

    # [Bug #4] LLM 返回非法枚举值（如"资源管理"）时 fallback 到 OTHER
    @field_validator("category", mode="before")
    @classmethod
    def unknown_category_fallback(cls, v):
        try:
            return IssueCategory(v) if isinstance(v, str) else v
        except ValueError:
            return IssueCategory.OTHER

    # [B02] LLM 偶发遗漏字段导致 ValidationError，兜底为空字符串/0
    @field_validator("description", "code_snippet", mode="before")
    @classmethod
    def missing_string_fallback(cls, v):
        return v if v is not None else ""

    @field_validator("lineno", mode="before")
    @classmethod
    def missing_lineno_fallback(cls, v):
        return v if v is not None else 0


class ReviewResult(BaseModel):
    """单个审查员的完整输出，打包所有 Issue 并标记来自哪个维度"""
    dimension: ReviewDimension                           # 来自哪个审查员
    issues: list[Issue] = Field(default_factory=list)   # 该审查员发现的所有问题

    # [Bug #3] LLM 返回 "issues": null 时兜底为空列表，防下游遍历 None 爆 AttributeError
    @field_validator("issues", mode="before")
    @classmethod
    def default_issues_to_empty(cls, v: list | None) -> list:
        return v if v is not None else []


# ============================================================
# critic_agent 输出 — 汇总去重排序后的修复方案
# ============================================================

class ActionItem(BaseModel):
    """单条修复指令 — critic 去重排序后写给 coder 看的执行清单"""
    priority: int                    # 修复优先级，从 1 开始编号，1 = 最先修
    severity: Severity               # 严重程度，来源 Issue 原样保留
    category: IssueCategory          # 问题分类，来源 Issue 原样保留
    description: str                 # 需要修改什么，用自然语言说清楚
    lineno: int                      # 问题所在行号
    fix_instruction: str             # 具体怎么改的指令，必须能让 coder 直接执行

    # [Bug #4] LLM 枚举越界 fallback
    @field_validator("severity", mode="before")
    @classmethod
    def unknown_severity_fallback(cls, v):
        try:
            return Severity(v) if isinstance(v, str) else v
        except ValueError:
            return Severity.MEDIUM

    # [Bug #4] LLM 枚举越界 fallback
    @field_validator("category", mode="before")
    @classmethod
    def unknown_category_fallback(cls, v):
        try:
            return IssueCategory(v) if isinstance(v, str) else v
        except ValueError:
            return IssueCategory.OTHER

    # [B00] LLM 遗漏 priority/description/lineno 时兜底
    @field_validator("priority", "lineno", mode="before")
    @classmethod
    def missing_int_fallback(cls, v):
        return v if v is not None else 0

    @field_validator("description", mode="before")
    @classmethod
    def missing_description_fallback(cls, v):
        return v if v is not None else ""

    # [B01-#05] LLM 偶发遗漏 fix_instruction 字段导致 ValidationError 崩溃，兜底为空字符串
    @field_validator("fix_instruction", mode="before")
    @classmethod
    def missing_fix_instruction_fallback(cls, v):
        return v if v is not None else ""


class CriticSummary(BaseModel):#一份原始代码文件对应一份CriticSummary，但又三份ReviewResult
    """critic_agent 的完整输出 — 去重、排序后的统一修复方案"""
    score_before: int = Field(ge=0, le=100)              # 修复前评分 0-100，Pydantic 自动校验范围
    total_issues: int = Field(ge=0)                      # 去重后问题总数，不能为负数
    by_severity: dict[str, int] = Field(                 # 按严重度统计数量,各个程度问题分别有几个
        default_factory=lambda: {"critical": 0, "high": 0, "medium": 0, "low": 0}
    )
    action_plan: list[ActionItem] = Field(default_factory=list)  # 按优先级排列的修复清单
    summary: str = ""                                    # 自然语言总结：主要风险 + 优先处理建议

    # [B00] LLM 遗漏 score_before/total_issues 时兜底
    @field_validator("score_before", mode="before")
    @classmethod
    def missing_score_fallback(cls, v):
        return v if v is not None else 0

    @field_validator("total_issues", mode="before")
    @classmethod
    def missing_total_issues_fallback(cls, v):
        return v if v is not None else 0


# ============================================================
# coder_agent 输出 — 修复结果
# ============================================================

class ChangeItem(BaseModel):
    """单处修改的 before / after 对比记录"""
    lineno: int          # 修改所在行号
    original: str        # 修改前的代码片段
    fixed: str           # 修改后的代码片段
    reason: str          # 为什么这样改，一句话说清

    # [B00] LLM 遗漏字段时兜底
    @field_validator("lineno", mode="before")
    @classmethod
    def missing_lineno_fallback(cls, v):
        return v if v is not None else 0

    @field_validator("original", "fixed", "reason", mode="before")
    @classmethod
    def missing_string_fallback(cls, v):
        return v if v is not None else ""


class CoderResult(BaseModel):
    """coder_agent 的完整输出 — 修复后的完整代码 + 所有修改记录"""
    fixed_code: str                                          # 修复后的完整代码，整个文件传给 sandbox 执行
    changes: list[ChangeItem] = Field(default_factory=list)  # 所有修改记录，每条是一个 ChangeItem
    notes: str = ""                                          # 备注：无法自动修复的问题在这里说明
    # [B01-#04] 因 [需人工] 跳过的条目，每条格式 "行{N}: {问题简述} — {人工需要做什么}"
    skipped_items: list[str] = Field(default_factory=list)

    # [B00] LLM 遗漏 fixed_code 时兜底
    @field_validator("fixed_code", mode="before")
    @classmethod
    def missing_fixed_code_fallback(cls, v):
        return v if v is not None else ""

    # [B03] LLM 返回 "changes": null / "skipped_items": null 兜底为空列表
    @field_validator("changes", "skipped_items", mode="before")
    @classmethod
    def default_list_to_empty(cls, v):
        return v if v is not None else []


# ============================================================
# sandbox_executor + reflect_node 输出
# ============================================================

class SandboxResult(BaseModel):
    """沙箱执行验证结果 — Docker 容器跑完修复代码后的输出"""
    exit_code: int            # 进程退出码，0 = 正常退出（通过），非 0 = 报错
    stdout: str = ""          # 标准输出，程序 print 了什么
    stderr: str = ""          # 标准错误，程序报了哪些错
    passed: bool              # 是否通过验证，True 表示 exit_code == 0


class ReflectionResult(BaseModel):
    """反思分析结果 — reflect_node 分析沙箱失败原因后输出，不直接存 state 而是拆解后存入"""
    failure_type: FailureType      # 失败类型：语法错 / 逻辑错 / 引入新 bug / 环境问题
    root_cause: str                # 哪处修改导致了失败，点出具体的 ChangeItem
    new_strategy: str              # 调整后的修复思路，coder_agent 重试时参考
    should_revert: bool = False    # 是否应该回退某处修改

    # [B00] LLM 遗漏 root_cause/new_strategy 时兜底
    @field_validator("root_cause", "new_strategy", mode="before")
    @classmethod
    def missing_string_fallback(cls, v):
        return v if v is not None else ""

    # [Bug #4] LLM 返回非法 failure_type 时 fallback 到 LOGIC_ERROR
    @field_validator("failure_type", mode="before")
    @classmethod
    def unknown_failure_type_fallback(cls, v):
        try:
            return FailureType(v) if isinstance(v, str) else v
        except ValueError:
            return FailureType.LOGIC_ERROR


# ============================================================
# output_node 输出 — 最终报告
# ============================================================

class FinalReport(BaseModel):
    """最终审查报告 — output_node 组装，整个流程的最终产出，前端直接拿它展示"""
    original_code: str                                      # 用户提交的原始代码，保留用于对比
    fixed_code: str                                         # 修复后的代码，失败时可能为空
    action_items: list[ActionItem] = Field(default_factory=list)  # 去重排序后的修复清单，复用 ActionItem
    score_before: int = 0                                   # 修复前综合评分 0-100
    score_after: int = 0                                    # 修复后预估评分，阶段四再实现
    sandbox_passed: bool = False                            # 沙箱验证是否通过
    retry_count: int = 0                                    # 修复经历了几次重试
    summary: str = ""                                       # 自然语言总结：整体评价 + 关键建议
    # [B01-#04] 状态新增 partial —— 沙箱通过但存在需人工处理的问题（如硬编码密钥需建 .env）
    status: str = "running"                                 # running / success / partial / failed
    skipped_items: list[str] = Field(default_factory=list)  # [B01-#04] 需人工介入的建议列表，从 CoderResult 透传

    # [Streamlit HITL] checkpointer 序列化后 isinstance 可能失效，强制 model_dump 再 model_validate
    @field_validator("action_items", mode="before")
    @classmethod
    def coerce_action_items(cls, v):
        if v is None:
            return []
        result = []
        for item in v:
            if isinstance(item, dict):
                result.append(ActionItem.model_validate(item))
            elif hasattr(item, 'model_dump'):
                result.append(ActionItem.model_validate(item.model_dump()))
            else:
                result.append(item)
        return result