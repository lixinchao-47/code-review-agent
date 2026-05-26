# Pydantic 数据模型设计

## 1. 模型全景

```
AgentState (TypedDict)          ← LangGraph 流程唯一数据载体
├── original_code: str
├── code_analysis: CodeAnalysis
├── review_results: Annotated[list[ReviewResult], operator.add]
├── critic_summary: CriticSummary
├── coder_result: CoderResult
├── sandbox_result: SandboxResult
├── retry_count: int
├── reflection_notes: str
├── human_feedback: str
├── final_report: FinalReport
└── status: str

Pydantic BaseModel (12个)
├── CodeAnalysis        ← code_parser 输出
├── ReviewResult        ← 任一审查 Agent 输出（含 list[Issue]）
│   └── Issue           ← 单个问题（安全/性能/风格共用）
├── CriticSummary       ← critic_agent 输出
│   └── ActionItem      ← 单条修复指令
├── CoderResult         ← coder_agent 输出
│   └── ChangeItem      ← 单处修改记录
├── SandboxResult       ← sandbox_executor 输出
├── FinalReport         ← output_node 输出
└── ReflectionResult    ← reflect_node 输出
```

---

## 2. 共享枚举

```python
from enum import Enum

class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class ReviewDimension(str, Enum):#用于表示审查结果文件（RviewResult类）来自哪个审查员
    SECURITY = "security"
    PERFORMANCE = "performance"
    STYLE = "style"

class IssueCategory(str, Enum):#三审查共用，每个审查由对应问题
    # 安全
    INJECTION = "注入"
    SENSITIVE_INFO = "敏感信息"
    ENCRYPTION = "加密"
    PERMISSION = "权限"
    DESERIALIZATION = "反序列化"
    # 性能
    TIME_COMPLEXITY = "时间复杂度"
    SPACE_COMPLEXITY = "空间复杂度"
    IO = "I/O"
    DATA_STRUCTURE = "数据结构"
    DUPLICATE_COMPUTATION = "重复计算"
    # 风格
    NAMING = "命名"
    FUNCTION_DESIGN = "函数设计"
    COMMENT = "注释"
    DUPLICATE_CODE = "重复"
    EXCEPTION = "异常"
    TYPE_HINT = "类型"
    FORMAT = "格式"
    OTHER = "其他"
```

**为什么用 `str, Enum` 继承:** 序列化后直接是字符串值，存 state 和 JSON 输出都方便，不用 `.value`。

---

## 3. 输入层模型

### 3.1 AgentState（TypedDict，非 Pydantic）

LangGraph state 必须是 TypedDict，Pydantic BaseModel 不兼容。

```python
from typing import Annotated, TypedDict, Optional
import operator
from models import (
    CodeAnalysis, ReviewResult, CriticSummary,
    CoderResult, SandboxResult, FinalReport,
)


class AgentState(TypedDict):
    # 输入
    original_code: str

    # 解析结果
    code_analysis: Optional[CodeAnalysis]        # 初始为 None

    # 三路审查结果（并行累积）
    review_results: Annotated[list[ReviewResult], operator.add]

    # 汇总
    critic_summary: Optional[CriticSummary]       # 初始为 None

    # 修复
    coder_result: Optional[CoderResult]           # 初始为 None

    # 沙箱验证
    sandbox_result: Optional[SandboxResult]       # 初始为 None

    # 反思控制
    retry_count: int                              # 初始为 0
    reflection_notes: str                         # 初始为 ""

    # 人工审核
    human_feedback: str                           # 初始为 ""

    # 最终输出
    final_report: Optional[FinalReport]           # 初始为 None
    status: str                                   # 初始为 "running"
```

**为什么 `Optional` 字段不用 `| None` 写法:** TypedDict 类型检查器对 `Optional[X]` 支持更稳定，避免 IDE 误报。Python 3.12 支持 `X | None` 语法，但 TypedDict 的 type checker 行为有差异。

---

## 4. 节点输出模型

### 4.1 CodeAnalysis — code_parser 输出

```python
from pydantic import BaseModel, Field


class FunctionInfo(BaseModel):
    """单个函数的结构化描述"""
    name: str
    lineno: int
    params: list[str] = Field(default_factory=list)
    decorators: list[str] = Field(default_factory=list)
    docstring: Optional[str] = None
    body_summary: str = ""


class ClassInfo(BaseModel):
    """单个类的结构化描述"""
    name: str
    lineno: int
    methods: list[str] = Field(default_factory=list)
    base_classes: list[str] = Field(default_factory=list)
    docstring: Optional[str] = None


class CodeAnalysis(BaseModel):
    """代码解析结果——只做客观描述，不给意见"""
    functions: list[FunctionInfo] = Field(default_factory=list)
    classes: list[ClassInfo] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    global_statements: list[str] = Field(default_factory=list)
    overview: str = ""
```

**`default_factory=list` 的作用:** 确保每个实例有独立列表，避免 Python 默认参数共享陷阱（所有实例共享同一个 list 对象）。

---

### 4.2 Issue + ReviewResult — 审查 Agent 输出

三个维度的审查 Agent 共用同一套模型，通过 `dimension` 字段区分来源。

```python
class Issue(BaseModel):
    """单个审查发现的问题"""
    severity: Severity
    category: IssueCategory
    lineno: int
    code_snippet: str
    description: str
    suggestion: str

    # 安全专用字段
    cwe_id: Optional[str] = None           # CWE-89

    # 性能专用字段
    estimated_impact: Optional[str] = None  # "输入 10000 条时从 3s 降至 0.1s"

    # 风格专用字段
    pep8_ref: Optional[str] = None          # "E501"


class ReviewResult(BaseModel):
    """单个审查 Agent 的完整输出"""
    dimension: ReviewDimension              # security | performance | style
    issues: list[Issue] = Field(default_factory=list)
```

**为什么三个维度共用 Issue 而不是拆成 SecurityIssue / PerfIssue / StyleIssue:**
- critic_agent 汇总时需要统一处理，三个类型会增加分支判断
- 通过 `dimension` 字段区分，critic 可以按维度筛选
- 可选字段（cwe_id / estimated_impact / pep8_ref）各维度各取所需，互不干扰

**为什么 ReviewResult 包一层 `dimension` 而不是直接 `list[Issue]`:**
- `review_results` 是三个并行节点各自写入一条，每条必须带标签才能知道是谁写的
- critic 去重时需要知道两个 issue 是否来自同一维度

---

### 4.3 ActionItem + CriticSummary — critic_agent 输出

```python
class ActionItem(BaseModel):
    """单条修复指令——写给 coder_agent 看的"""
    priority: int                          # 从 1 开始编号
    severity: Severity
    category: IssueCategory
    description: str                       # 需要修改什么
    lineno: int
    fix_instruction: str                   # 具体修改指令，必须可执行


class CriticSummary(BaseModel):
    """汇总结果——去重、排序后的统一修复方案"""
    score_before: int = Field(ge=0, le=100)
    total_issues: int = Field(ge=0)
    by_severity: dict[str, int] = Field(
        default_factory=lambda: {"critical": 0, "high": 0, "medium": 0, "low": 0}
    )
    action_plan: list[ActionItem] = Field(default_factory=list)
    summary: str = ""
```

**`Field(ge=0, le=100)` 的作用:** Pydantic 自动校验分数在 0-100 范围内，超出抛 ValidationError，避免代码 bug 导致评分异常。

**`default_factory=lambda: {...}` 的写法:** dict 是可变对象，不能用 `Field(default_factory={"critical": 0})`（语法错误）。`lambda` 是创建可变默认值的标准做法。

---

### 4.4 ChangeItem + CoderResult — coder_agent 输出

```python
class ChangeItem(BaseModel):
    """单处修改的 before/after 对比"""
    lineno: int
    original: str                          # 修改前代码片段
    fixed: str                             # 修改后代码片段
    reason: str                            # 为什么这样改（一句话）


class CoderResult(BaseModel):
    """修复执行结果"""
    fixed_code: str                        # 修复后的完整代码
    changes: list[ChangeItem] = Field(default_factory=list)
    fixed_count: int = 0
    notes: str = ""                        # 审查警告（如作用域变更检测）
    skipped_items: list[str] = Field(default_factory=list)  # [需人工] 跳过的条目
```

---

### 4.5 SandboxResult — sandbox_executor 输出

```python
class SandboxResult(BaseModel):
    """沙箱执行验证结果"""
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    passed: bool                           # exit_code == 0
```

**为什么存 `passed` 而不是让调用方判断 `exit_code == 0`:**
- 后续可能加入更多验证规则（如输出内容校验、内存限制命中），不只是看 exit_code
- 条件边函数直接读 `passed` 比写 `exit_code == 0` 语义更清晰

---

### 4.6 ReflectionResult — reflect_node 输出

```python
from enum import Enum


class FailureType(str, Enum):
    SYNTAX_ERROR = "syntax_error"
    LOGIC_ERROR = "logic_error"
    NEW_BUG = "new_bug"                    # 修复引入了新 bug
    ENV_ISSUE = "env_issue"               # 沙箱环境问题，与代码无关


class ReflectionResult(BaseModel):
    """反思分析结果"""
    failure_type: FailureType
    root_cause: str                        # 哪处修改导致了失败
    new_strategy: str                      # 调整后的修复思路
    should_revert: bool = False            # 是否应回退某处修改
```

**为什么 reflect_node 的结构化输出和 state 中的 `reflection_notes` 不同:**
- `ReflectionResult` 是 LLM 输出的完整结构（Pydantic 校验用）
- `reflection_notes`（state 字段）只存 `new_strategy` 字符串（coder 只关心这个）
- 节点函数负责拆解：`{"reflection_notes": result.new_strategy, "retry_count": old_count + 1}`

---

### 4.7 FinalReport — output_node 输出

```python
class FinalReport(BaseModel):
    """最终审查报告——展示给用户的完整输出"""
    original_code: str
    fixed_code: str
    action_items: list[ActionItem] = Field(default_factory=list)  # 复用 ActionItem
    score_before: int = 0
    score_after: int = 0                    # 修复后评分，output_node 计算
    sandbox_passed: bool = False
    retry_count: int = 0
    summary: str = ""
    status: str = "running"                 # running / success / partial / failed
    skipped_items: list[str] = Field(default_factory=list)  # [需人工] 条目，透传自 CoderResult
    notes: str = ""                         # 审查警告（如作用域变更检测）
```

**为什么 `action_items` 用 `ActionItem` 而不是 `Issue`:**
- 最终报告展示的是经过 critic 去重排序后的版本
- `ActionItem` 已经包含 `fix_instruction`，用户可以看到每条问题怎么修的
- 避免数据冗余，不重复存两套

---

## 5. 模型 ↔ State 字段映射

| Pydantic Model | State 字段 | 写入节点 | 说明 |
|---------------|-----------|---------|------|
| `CodeAnalysis` | `code_analysis` | code_parser | 完整存入 |
| `ReviewResult` | `review_results` (list) | security/perf/style | 每个追加一条 |
| `CriticSummary` | `critic_summary` | critic_agent | 完整存入 |
| `CoderResult` | `coder_result` | coder_agent | 完整存入，重试时覆盖 |
| `SandboxResult` | `sandbox_result` | sandbox_executor | 完整存入 |
| `ReflectionResult` | `reflection_notes` + `retry_count` | reflect_node | 拆解后分别存入 |
| `FinalReport` | `final_report` | output_node | 完整存入 |

---

## 6. Optional 字段初始化策略

AgentState 初始值时，可选字段如何处理：

```python
INITIAL_STATE: AgentState = {
    "original_code": "",
    "code_analysis": None,
    "review_results": [],          # operator.add 从空列表开始累积
    "critic_summary": None,
    "coder_result": None,
    "sandbox_result": None,
    "retry_count": 0,
    "reflection_notes": "",
    "human_feedback": "",
    "final_report": None,
    "status": "running",
}
```

**为什么 `review_results` 用 `[]` 而不是 `None`:**
- `operator.add` 是 `list + list`，`None + list` 会抛 TypeError
- 初始空列表 + 第一个结果 = 一个元素列表，刚好是期望行为

---

## 7. 文件组织

```python
# src/models.py — 所有 Pydantic 模型集中于此

# 导入顺序：
# 1. 标准库 (enum, typing)
# 2. 第三方 (pydantic)
# 3. 项目内部 (无——models 是最底层，不依赖其他项目模块)
```

单一 `models.py` 文件，不拆分为 `models/` 包。12 个模型 + 4 个枚举大约 350 行，一个文件足够，拆开反而增加导入复杂度。

---

## 8. 模型结构总览

```
models.py (12 个 Pydantic 模型 + 4 个枚举)
├── Enum
│   ├── Severity           ← 严重程度
│   ├── ReviewDimension    ← 审查维度
│   ├── IssueCategory      ← 问题分类（17 种）
│   └── FailureType        ← 沙箱失败类型（4 种）
├── 解析层
│   ├── FunctionInfo       ← 函数描述
│   ├── ClassInfo          ← 类描述
│   └── CodeAnalysis       ← 代码解析总结果
├── 审查层
│   ├── Issue              ← 单条问题
│   └── ReviewResult       ← 单个审查员产出
├── 汇总修复层
│   ├── ActionItem         ← 单条修复指令
│   ├── CriticSummary      ← 汇总修复方案
│   ├── ChangeItem         ← 单处修改记录
│   └── CoderResult        ← 修复完整结果
├── 验证反思层
│   ├── SandboxResult      ← 沙箱执行结果
│   └── ReflectionResult   ← 反思分析结果
└── 输出层
    └── FinalReport        ← 最终报告
```
