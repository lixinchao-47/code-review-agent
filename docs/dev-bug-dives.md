# Bug 记录

> 记录从框架搭建完毕，开始测试以后出现的所有bug：错误现象 → 错误假设 → 根因 → 修复 → 经验。


## 目录

- [问题 #1：Send 第二个参数导致分支 state 缺少字段 —— `KeyError: 'original_code'`](#问题-1send-第二个参数导致分支-state-缺少字段--keyerror-original_code)
- [问题 #2：HITL `interrupt_before` 中断不抛异常 —— 流程静默走完但报告未生成](#问题-2hitl-interrupt_before-中断不抛异常--流程静默走完但报告未生成)
- [问题 #3：LLM 返回 `"issues": null` 导致 `AttributeError`](#问题-3llm-返回-issues-null-导致-attributeerror)
- [问题 #4：LLM 返回枚举非法值导致 `ValidationError` —— 系统性加固所有枚举字段](#问题-4llm-返回枚举非法值导致-validationerror--系统性加固所有枚举字段)
- [问题 #5：`with_structured_output` 返回 `None` 导致 `AttributeError` —— 全链路 None 保护](#问题-5with_structured_output-返回-none-导致-attributeerror--全链路-none-保护)
- [问题 #6：DooD 模式下容器与宿主机文件系统不互通 —— sandbox 容器挂载不到代码文件](#问题-6dood-模式下容器与宿主机文件系统不互通--sandbox-容器挂载不到代码文件)
- [问题 #7：`retry_or_human` 返回值与条件边映射不一致 —— `KeyError: 'human_review'`](#问题-7retry_or_human-返回值与条件边映射不一致--keyerror-human_review)
- [问题 #8：checkpointer 序列化后 Pydantic `isinstance` 失效 —— FinalReport ValidationError](#问题-8checkpointer-序列化后-pydantic-isinstance-失效--finalreport-validationerror)
- [问题 #9：[需人工] 标签不稳定 —— 硬编码凭据 60% 漏标，三次 prompt 改进无效，最终确定性兜底](#问题-9需人工-标签不稳定--硬编码凭据-60-漏标三次-prompt-改进无效最终确定性兜底)
- [问题 #10：coder 不遵循 [需人工] 标签 —— 代码级过滤从源头阻断假修复](#问题-10coder-不遵循-需人工-标签--代码级过滤从源头阻断假修复)
- [问题 #11：删除 [跳过] 标签 —— 判据不适合 LLM、与 [需人工] 无差异、审查员检测不到对应问题](#问题-11删除-跳过-标签--判据不适合-llm与-需人工-无差异审查员检测不到对应问题)
- [问题 #12：改作用域硬禁令被频繁突破 —— AST 级检测兜底 + critic prompt 约束反噬教训](#问题-12改作用域硬禁令被频繁突破--ast-级检测兜底--critic-prompt-约束反噬教训)

---

## 问题 #1：Send 第二个参数导致分支 state 缺少字段 —— `KeyError: 'original_code'`

**日期**：2026-05-11

**错误信息**：
```
KeyError: 'original_code'
During task with name 'security_reviewer' and id '0506ac5d-981d-50fc-c205-cfed1cf1bc59'
```

**触发位置**：`src/graph/nodes.py` 第 40 行，`security_reviewer` 节点内：

```python
HumanMessage(content=f"原始代码：{state['original_code']}"),
```

**错误代码**（`src/graph/builder.py` Send 分发函数）：

```python
def fanout_to_reviewers(state: AgentState) -> list[Send]:
    return [
        Send("security_reviewer", {"code_analysis": state["code_analysis"]}),
        Send("performance_reviewer", {"code_analysis": state["code_analysis"]}),
        Send("style_reviewer", {"code_analysis": state["code_analysis"]}),
    ]
```

**我们当时的错误理解**：

当时认为 Send 第二个参数是"覆盖/叠加到主 state 副本上"——即分支 state = 主 state（完整 13 个字段） + Send 覆盖的字段。按这个理解，只传 `code_analysis` 就够了，因为 `original_code` 会从主 state 继承过来。

这个理解是错的。

**排查过程**：

1. `code_parser` 节点执行成功，说明入口节点的 state 是完整的（`INITIAL_STATE` + 手动 set 的 `original_code`）
2. 错误发生在 `security_reviewer`，它是 Send 分发的目标
3. 检查 `fanout_to_reviewers` 函数，发现三个 `Send` 都只传了 `code_analysis`，没有传 `original_code`
4. 打印错误是 `KeyError: 'original_code'`，说明分支 state 里根本没有这个 key
5. 得出结论：Send 分支不会自动继承主 state 的其他字段

**正确理解**：

**Send 的第二个参数就是目标分支的全部 state 输入。** 主 state 的其他字段不会自动带过来。目标节点需要什么字段，Send 必须全部显式传入。

```
错误模型：分支 state = 主 state + Send 覆盖
正确模型：分支 state = Send 第二个参数（仅此而已）
```

| 假设 | Send("xx", {"code_analysis": ...}) | 分支能读到 original_code? |
|------|------|:---:|
| 错误理解 | 主state + code_analysis 覆盖 | ✅ 能（从主 state 继承） |
| 实际行为 | 分支 state **只有** code_analysis | ❌ 不能（没传就没有） |

**为什么会有错误理解**：

直觉上 LangGraph 的 state 是所有节点共享的，以为 Send 只是在共享 state 上临时修改一下传给分支。实际上 Send 是为每个分支创建独立的 state 副本，副本的初始内容由 Send 第二个参数决定，不继承主 state。

**修复后的代码**：

```python
def fanout_to_reviewers(state: AgentState) -> list[Send]:
    return [
        Send("security_reviewer", {
            "code_analysis": state["code_analysis"],
            "original_code": state["original_code"],   # 必须显式传
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
```

**`return` 的行为不受影响**：

节点 `return` 的字典仍然会合并回主 state。这个理解始终正确。

**经验教训**：

1. Send 第二个参数不是"覆盖"，是目标分支的**全部 state 输入**。一个字段都不少。
2. 设计阶段对 API 的理解必须经过实际运行验证，不能单靠直觉和推理。
3. 如果错误发生在 Send 目标节点中且是 KeyError，优先怀疑 Send 传参不完整。
4. `Send("xx", {})` 传空字典，目标分支拿到的就是空 state，读任何字段都会 KeyError。空字典不是"用主 state"的意思。
5. 语法笔记中保留错误记录 + 纠正记录，对比学习比覆盖更有价值。

---

## 问题 #2：HITL `interrupt_before` 中断不抛异常 —— 流程静默走完但报告未生成

**日期**：2026-05-11

**错误现象**：
运行 `python scripts/run.py`，控制台输出：

```
正在执行审查流程...

=== 最终审查报告 ===
报告未生成，请检查上游流程
```

没有报错，没有异常，但 `final_report` 为 `None`，`status` 为 `"running"`。

**当时的错误代码**（`scripts/run.py`）：

```python
# 错误：以为 interrupt_before 会抛异常
try:
    result = app.invoke(initial_state, config)
except Exception:
    # HITL 中断，注入审批意见后恢复
    print(">>> 暂停在 human_review 节点...")
    app.update_state(config, {"human_feedback": ""})
    result = app.invoke(None, config)
```

**排查过程**：

1. 加了调试日志打印 `result` 中所有 key 的值，发现 `code_analysis`、`review_results`、`critic_summary`、`coder_result`、`sandbox_result` 全部正常输出（说明 `code_parser` → 审查员 → `critic_agent` → `coder_agent` → `sandbox_executor` 全线跑通）
2. 但 `final_report` 是 `None`，`status` 是 `"running"`
3. `except` 里的打印没有出现，说明 `invoke` 没有抛异常
4. 即 `interrupt_before` 在 `human_review` 前暂停了，但**不抛异常**，`invoke` 静默返回当前 state
5. 代码没意识到已经中断，直接跑去读 `final_report`，此时 `output_node` 还没执行，当然是 `None`

**根因**：

**LangGraph 1.1.x 的 `interrupt_before` 中断不抛异常。** `app.invoke()` 在断点处静默返回当前 state（就像正常完成一样），不发出任何信号告诉你"我还没跑完"。`except Exception` 抓了个寂寞。

这与我们最初的假设相反。之前我们想当然地认为"中断 = 抛异常"，所以设计了 `try/except` 来捕获并处理。实际上 LangGraph 的中断机制是让 `invoke` 正常返回，然后通过 `app.get_state(config).next` 让调用方主动检查是否真的完成。

**正确做法**：

```python
# 1. 正常执行，不管是否中断都返回当前 state
result = app.invoke(initial_state, config)

# 2. 检查是否真的完成了（next 非空 = 还有节点待执行 = 中断了）
state_snapshot = app.get_state(config)
if state_snapshot.next:
    # 中断了，注入 human_feedback 后恢复
    print(">>> 暂停在 human_review 节点...")
    app.update_state(config, {"human_feedback": ""})
    result = app.invoke(None, config)

# 3. 现在 result 里 final_report 一定有值
```

**`app.get_state(config).next` 的含义**：

| `next` 值 | 含义 |
|-----------|------|
| `()` 空元组 | 工作流已完全结束，没有待执行节点 |
| `('human_review',)` | 中断在 `human_review` 前，该节点待执行 |
| 其他非空值 | 中断在其他位置 |

**详细解析**：

**第一层：`interrupt_before` 做了什么**

编译图时传了 `interrupt_before=["human_review"]`，LangGraph 在执行到 `human_review` 节点之前主动暂停。它不是崩溃、不是报错，而是把当前 state 写入 checkpointer（MemorySaver），然后让 `invoke()` 正常返回。所以 `invoke()` 不抛异常——对它来说"暂停"和"跑完"都是正常结束。

**第二层：`get_state(config).next` 怎么区分"暂停"和"跑完"**

`app.get_state(config)` 通过 `thread_id` 去 checkpointer 里查这个流程的快照（StateSnapshot），快照里有一个字段叫 `next`，记录的是还有哪些节点排队等着执行：

| `next` 值 | 含义 |
|-----------|------|
| `()` 空元组 | 所有节点都执行完了，没有排队 |
| `('human_review',)` | 有节点在排队 → 说明被 `interrupt_before` 拦住了 |

所以 `if state_snapshot.next` 等价于问："还有人在排队吗？"——有，就是中断了；没有，就是真跑完了。

**一句话总结**：`invoke()` 不告诉你"我暂停了"，它只把 state 写盘就下班。你得自己查 checkpointer 里的排队名单（`.next`），名单非空就说明流程被挂起了，需要注入 `human_feedback` 再 `invoke(None)` 继续跑。

**经验教训**：

1. **不要假设异常 = 中断。** LangGraph 的 `interrupt_before` 是静默暂停，`invoke` 正常返回当前 state。中断检测必须用 `app.get_state(config).next`。
2. **`interrupt_before` 和异常是完全不同的机制。** 前者是 LangGraph 设计的中断点，后者是代码执行错误。我们用 `except Exception` 去接中断点，根本对不上号。
3. 调试流程卡住时，优先 dump `result` 的完整 state，看哪些字段有值、哪些是 None。关键线索藏在 state 里。
4. `checkpointer=MemorySaver()` 让 `get_state(config)` 能通过 `thread_id` 找回中断的 state，没有 checkpointer 中断状态无法持久化。

---

## 问题 #3：LLM 返回 `"issues": null` 导致 `AttributeError: 'NoneType' object has no attribute 'issues'`

**日期**：2026-05-11

**错误信息**：
```
AttributeError: 'NoneType' object has no attribute 'issues'
During task with name 'critic_agent' and id 'b8100fee-6657-6995-6342-12a6664da40a'
```

**触发代码**（`src/graph/nodes.py` 第 69 行，`critic_agent` 内部）：

```python
for r in state['review_results']:
    for issue in r.issues:   # ← r.issues 是 None，遍历崩溃
        issues_text.append(...)
```

**根因分析**：

`ReviewResult` 的 `issues` 字段定义：

```python
class ReviewResult(BaseModel):
    issues: list[Issue] = Field(default_factory=list)
```

`default_factory=list` 的作用是：**当创建 ReviewResult 时未传 `issues` 字段，自动赋 `[]`。** 但这里的问题不是"不传"，而是 LLM 在 JSON 里写了 `"issues": null`。

```
Pydantic 的行为：
  不传 "issues"     → default_factory 生效 → issues = []      ✅
  传 "issues": null  → Pydantic 赋 None   → issues = None     ❌
  传 "issues": [...]  → Pydantic 赋列表    → issues = [...]    ✅
```

`null` 在 JSON 中是一个明确的值（等价于 Python 的 `None`），Pydantic 收到 `None` 后**跳过 default_factory**，直接赋值 `None`。`critic_agent` 遍历 `None` 就爆了 `AttributeError`。

**为什么 LLM 会返回 `null`**：

审查员可能认为代码没问题（no issues found），于是 JSON 输出：
```json
{"dimension": "performance", "issues": null}
```

LLM 的逻辑是"没有问题，所以省略列表"。但 Pydantic 把 `null` 当成合法值收下了。

**修复方案**：在 `ReviewResult` 模型层加 `field_validator`，任何输入（包括 `null`）强制转为空列表。

**修复代码**（`src/models.py`）：

```python
from pydantic import BaseModel, Field, field_validator

class ReviewResult(BaseModel):
    dimension: ReviewDimension
    issues: list[Issue] = Field(default_factory=list)

    @field_validator("issues", mode="before")
    @classmethod
    def default_issues_to_empty(cls, v: list | None) -> list:
        """LLM 返回 null 时自动转为空列表，防止下游遍历 None 爆 AttributeError"""
        return v if v is not None else []
```

- `mode="before"` — 在类型校验**之前**运行，拿到的是 LLM 返回的原始值（可能是 `None`）
- `v if v is not None else []` — 原始值有内容就直接用，是 `None` 就返回 `[]`
- `@classmethod` — `field_validator` 要求被装饰函数是类方法

**为什么改模型层而不是消费节点**：

| 方案 | 改动位置 | 影响范围 | 评价 |
|------|---------|---------|------|
| `critic_agent` 加 `if r.issues` 保护 | `nodes.py` | 仅 `critic_agent` | 治标，其他节点未来读 `issues` 也可能踩坑 |
| `field_validator` 在模型层拦截 | `models.py` | 所有下游节点 | 治本，数据进系统时已净化 |

**经验教训**：

1. **`default_factory=list` 不防 `null`。** 它的作用域是"字段缺失"，不是"字段为 null"。LLM 显式输出 `null` 会绕过 default_factory。
2. **不信任 LLM 的输出格式。** 即使 Pydantic 模型有默认值和类型声明，LLM 仍然可能返回不符合预期的值（`null` 代替空列表、数字写成字符串等）。关键字段加 `field_validator` 做防御。
3. **数据清洗放在模型层，不在业务节点。** 模型是数据入口，脏数据从这里拦下后所有下游节点都受益。
4. `field_validator` 的 `mode="before"` vs `mode="after"` 区别：`before` 在类型校验前运行，适合处理原始值转换；`after` 在校验后运行，适合对已确认类型的值做进一步约束。

---

## 问题 #4：LLM 返回枚举非法值（`"安全"`、`"资源管理"`）导致 `ValidationError` —— 系统性加固所有枚举字段

**日期**：2026-05-13

**错误信息**：
```
pydantic_core._pydantic_core.ValidationError: 2 validation errors for ReviewResult
issues.2.category
  Input should be '注入', '敏感信息', ... or '其他' [type=enum, input_value='安全', input_type=str]
issues.3.category
  Input should be '注入', '敏感信息', ... or '其他' [type=enum, input_value='资源管理', input_type=str]
During task with name 'style_reviewer'
```

**错误现象**：`style_reviewer`（风格审查员）节点中，LLM 返回的 Issue 中 `category` 写了 `"安全"` 和 `"资源管理"`。`"安全"` 是 ReviewDimension 的值（审查维度），`"资源管理"` 是我们枚举里根本没定义的词。Pydantic 校验时直接抛 `ValidationError`，整个流程炸停。

**从单一报错到系统性排查**：

这次报错引发了我们的警惕：**LLM 返回的不只在 `category` 这个字段会越界，所有枚举字段都可能被 LLM 自由发挥。**

梳理全系统 LLM 通过 `with_structured_output` 输出的所有枚举字段 —— 共 4 个模型、7 个枚举字段有风险：

| # | 模型 | 字段 | 枚举 | LLM 输出节点 |
|---|------|------|------|-------------|
| 1 | `Issue` | `severity` | `Severity`（4 种） | 三个审查员 |
| 2 | `Issue` | `category` | `IssueCategory`（17 种） | 三个审查员 |
| 3 | `ReviewResult` | `dimension` | `ReviewDimension`（3 种） | 三个审查员 |
| 4 | `ActionItem` | `severity` | `Severity`（4 种） | `critic_agent` |
| 5 | `ActionItem` | `category` | `IssueCategory`（17 种） | `critic_agent` |
| 6 | `ActionItem` | `dimension` | `ReviewDimension`（3 种） | `critic_agent` |
| 7 | `ReflectionResult` | `failure_type` | `FailureType`（4 种） | `reflect_node` |

**分类讨论：7 个字段分两类处理**

经过逐个分析，这 7 个字段的性质不同，不能一刀切全加 `field_validator + fallback`。

**第一类（5 个字段）**：可以用 fallback

LLM 对这些字段有合理的判断权，但可能写错。加 `field_validator`，非法值自动落到一个合理的默认值。

| 字段 | 枚举 | fallback | 理由 |
|------|------|----------|------|
| `Issue.severity` | `Severity` | `MEDIUM` | 猜不准取中间值 |
| `Issue.category` | `IssueCategory` | `OTHER`（"其他"） | 枚举自带 OTHER，"其他"语义通 |
| `ActionItem.severity` | `Severity` | `MEDIUM` | 同上 |
| `ActionItem.category` | `IssueCategory` | `OTHER`（"其他"） | 同上 |
| `ReflectionResult.failure_type` | `FailureType` | `LOGIC_ERROR` | 最常见的沙箱失败类型 |

**第二类（2 个字段）**：`dimension` 不应该让 LLM 填

`dimension` 表示"这个结果是哪个审查员产生的"。这个信息在当前节点是**确定已知的**——`security_reviewer` 的 dimension 一定是 `SECURITY`，不需要 LLM 来判断。

而且 `dimension` 枚举只有三个值（`SECURITY` / `PERFORMANCE` / `STYLE`），没有"其他"。如果 LLM 写错了，没有任何合法 fallback 可用。

更关键的是 `ActionItem.dimension`：它出现在 `critic_agent`（汇总节点）的输出中。critic 的工作是**去重合并**——如果 security 和 style 同时指出第 5 行有问题，合并后的 ActionItem 到底算 security 还是 style？在去重合并逻辑下，`dimension` 这个概念本身就模糊了。

进一步追踪发现，`coder_agent` 在展开 `action_plan` 时读的是 `priority`、`lineno`、`severity`、`category`、`fix_instruction`，**从未读取 `dimension`**。也就是说这个字段虽然定义了，但整个流程中没有下游节点消费它。critic 在生成 `fix_instruction` 时，关于维度/来源的信息已经融入了修复指令的措辞中。

**结论**：

| 字段 | 方案 | 原因 |
|------|------|------|
| `ReviewResult.dimension` | **节点硬覆盖**：审查员内直接 `result.dimension = ReviewDimension.SECURITY`（各自赋值） | 节点知自身身份，无需 LLM 猜，可信 |
| `ActionItem.dimension` | **直接删除** | 去重后语义模糊 + 无下游消费 + 无合法 fallback |

**最终改动清单**：

一、`models.py` — 5 个 `field_validator`：

```python
# Issue 模型
@field_validator("severity", mode="before")
@classmethod
def unknown_severity_fallback(cls, v):
    try:
        return Severity(v) if isinstance(v, str) else v
    except ValueError:
        return Severity.MEDIUM

@field_validator("category", mode="before")
@classmethod
def unknown_category_fallback(cls, v):
    try:
        return IssueCategory(v) if isinstance(v, str) else v
    except ValueError:
        return IssueCategory.OTHER

# ActionItem 模型 —— 同样两个 validator（代码结构一致）

# ReflectionResult 模型
@field_validator("failure_type", mode="before")
@classmethod
def unknown_failure_type_fallback(cls, v):
    try:
        return FailureType(v) if isinstance(v, str) else v
    except ValueError:
        return FailureType.LOGIC_ERROR
```

二、`models.py` — 删除 `ActionItem.dimension` 字段

三、`nodes.py` — 三个审查员各加一行硬赋值：

```python
# security_reviewer 内
result.dimension = ReviewDimension.SECURITY

# performance_reviewer 内
result.dimension = ReviewDimension.PERFORMANCE

# style_reviewer 内
result.dimension = ReviewDimension.STYLE
```

`ReviewResult.dimension` 已由节点硬赋值覆盖，不被 LLM 写入，不存在越界风险，**不需要 validator**。

**经验教训**：

1. **LLM 对枚举值的输出不可靠。** 即使 prompt 中暗示了枚举的取值范围，LLM 仍可能自由发挥（如本例中把 dimension 的值 `"安全"` 写到 category 里）。
2. **出现一个枚举报错时，系统性排查所有枚举字段。** 本次从 `Issue.category` 一个点出发，发现了 7 处潜在风险。只修报错那一处是治标，全量排查才是治本。
3. **不是所有枚举字段都适合加 fallback。** 像 `dimension` 这种"确定性已知"的信息，应该从源头硬赋值，不让 LLM 参与。加 fallback 是防守，让 LLM 不要填才是进攻。
4. **枚举越界和 `null`（问题 #3）同属"LLM 输出不可信"这一类问题。** 同一种防御方法（`field_validator(mode="before")`）可以解决，区别在于 null→空值的处理用 `or` 逻辑，枚举越界用 `try/except ValueError`。
5. **字段的价值要结合架构全局评估，不能只看注释。** `ActionItem.dimension` 注释说"coder 可据此调整修复侧重点"，但实际代码从未消费它，且 critic 去重后该字段语义天然模糊。这种"看起来有用、实际没用"的字段删掉比修更干净。

---

## 问题 #5：`with_structured_output` 返回 `None` 导致 `AttributeError` —— 全链路 None 保护

**日期**：2026-05-13

**错误信息**：
```
AttributeError: 'NoneType' object has no attribute 'dimension'
During task with name 'style_reviewer' and id 'd0b1d507-533a-96a7-5478-d9c6fd93e976'
```

**触发位置**：`src/graph/nodes.py` 第 62 行，`style_reviewer` 节点：

```python
result = structured_llm.invoke([...])
result.dimension = ReviewDimension.STYLE  # ← result 是 None，炸
```

**错误假设**：

问题 #3 和 #4 加完 `field_validator` 后，以为 LLM 结构化输出已经安全了。实际上 `field_validator` 只保护**模型构造成功后字段值非法**的情况，但 LLM 返回完全无法解析的 JSON 时，连模型都构造不出来，`invoke()` 直接返回 `None`。

两者是不同层级的防御：

```
LLM 返回 → with_structured_output 解析
  ├── 成功 → 模型对象 → field_validator 检查字段 ✅ (问题 #3, #4 已修)
  └── 失败 → 返回 None → 完全无保护 ❌ (本次炸点)
```

**根因**：`with_structured_output` 在以下情况会返回 `None` 而非抛异常：

1. LLM 返回了无法解析为 JSON 的文本（如纯自然语言拒绝回答）
2. LLM 返回了合法 JSON 但缺少必填字段，Pydantic 构造失败被吞掉
3. DeepSeek API 的 function calling 模式下，模型未返回 tool_call 而是返回了普通消息

LangChain 的 `with_structured_output` 在解析失败时不会抛异常，而是降级返回 `None`，调用方需自行判断。

**攻击面排查** — `nodes.py` 中 **所有 7 个 `structured_llm.invoke()` 调用点** 均无 None 保护：

| # | 节点 | 炸点 |
|---|------|------|
| 1 | `code_parser` | `analysis` 为 None → 下游 `state['code_analysis'].functions` AttributeError |
| 2 | `security_reviewer` | `result` 为 None → `result.dimension` AttributeError |
| 3 | `performance_reviewer` | 同上 |
| 4 | `style_reviewer` | 同上 ← 本次触发 |
| 5 | `critic_agent` | `summary` 为 None → 下游 `state['critic_summary'].action_plan` AttributeError |
| 6 | `coder_agent` | `result` 为 None → 下游 `state['coder_result'].fixed_code` AttributeError |
| 7 | `reflect_node` | `reflection` 为 None → `reflection.new_strategy` AttributeError |

此外，`coder_agent`、`sandbox_executor`、`reflect_node` 三个节点直接访问 `state['key'].field` 而不检查上游可能传入了 None，也需要补齐**消费端守卫**。

**修复**：

一、每个 LLM 调用点加调用后守卫（7 处），以 `style_reviewer` 为例：

```python
# 修改前
result = structured_llm.invoke([...])
result.dimension = ReviewDimension.STYLE  # ← 炸

# 修改后
result = structured_llm.invoke([...])
if result is None:
    return {"review_results": []}         # ← 空列表，adder reducer 安全
result.dimension = ReviewDimension.STYLE
```

不同节点的 fallback 策略：

| 节点 | LLM 返回 None 时的处理 |
|------|----------------------|
| `code_parser` | 返回空 `CodeAnalysis()` —— 所有字段有默认值，下游安全 |
| 三个审查员 | 返回 `{"review_results": []}` —— adder reducer 追加空列表无影响 |
| `critic_agent` | 返回 `{}` —— 不更新 state |
| `coder_agent` | 返回 `{}` —— 不更新 state |
| `reflect_node` | 返回默认反思文本 + `retry_count+1` |

二、消费端加输入守卫（3 处）：

```python
# coder_agent —— 上游 critic_agent 可能返回 {}
critic = state.get('critic_summary')
if critic is None:
    return {}

# sandbox_executor —— 上游 coder_agent 可能返回 {}
coder = state.get('coder_result')
if coder is None:
    return {'sandbox_result': SandboxResult(exit_code=-1, stderr='修复代码为空', passed=False)}

# reflect_node —— 上游 coder_agent 可能返回 {}
coder = state.get('coder_result')
if coder is not None:
    for ref in coder.changes:
        ...
```

**为什么是间歇性 bug**：

同样的代码（`import requests` + 硬编码密钥），第一次运行 `style_reviewer` 的 LLM 返回了无法解析的内容导致 `None`，第二次运行就正常。说明 LLM 输出质量本身就有随机性，None 保护不是"修一次就好"，而是"防止下一次随机发生"。

**经验教训**：

1. **`field_validator` 和 None 守卫是两层防御，缺一不可。** field_validator 防字段越界，None 守卫防模型构造失败。前者解决不了后者的问题，因为 None 根本就没有字段可供 validator 检查。

2. **所有 LLM 结构化输出调用点都要加 None 守卫，无一例外。** 7 个调用点中只要漏一个，那个就是定时炸弹。间歇性 bug 可能在 100 次运行中只触发 1 次，但触发时整条流水线崩溃。

3. **生产者加守卫不够，消费者也要加。** `critic_agent` 返回 `{}` 后，`coder_agent` 如果直接用 `state['critic_summary'].action_plan` 依然会炸。数据流的每一跳都要防御。

4. **间歇性 bug 才最危险。** 不是"会不会炸"而是"什么时候炸"——LLM 输出的随机性决定了这个 bug 随时可能在任何一次运行中出现。

---

## 问题 #6：DooD 模式下容器与宿主机文件系统不互通 —— sandbox 容器挂载不到代码文件

**日期**：2026-05-20

**错误现象**：

项目容器化（`docker compose up`）后，`sandbox_executor` 在 app 容器内写入临时文件，执行 `docker run -v` 时宿主机的 dockerd 找不到该文件，沙箱挂载失败。

**触发条件**：

app 容器通过挂载 `/var/run/docker.sock` 来启动 sandbox 容器（DooD 模式）。`docker run -v` 的路径由**宿主机 dockerd 解析**，而临时文件由 **app 容器内**的 Python 进程写入。两个文件系统各自独立。

**触发代码**（`src/graph/nodes.py`，最初的 `_docker_sandbox`）：

```python
# sandbox_executor 在 app 容器内写文件
with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
    f.write(fixed_code)
    tmp_path = f.name  # → /tmp/tmp_a1b2c3.py（容器文件系统）

# _docker_sandbox 直接用容器路径传给 docker run
'-v', f'{script_path}:/sandbox/code.py:ro',
# → docker run -v /tmp/tmp_a1b2c3.py:...  ← 宿主机 dockerd 去找 /tmp/tmp_a1b2c3.py
```

**我们当时的错误理解**：

`_docker_sandbox` 在宿主机裸跑时测试通过，就以为放进容器也能用。没有意识到容器化后 `docker.sock` 意味着 dockerd 是宿主机视角，`-v` 的路径必须宿主机可见。

这是我们只做了一件事（加 `docker.sock`），没追踪它对数据流的影响就以为完成了。

**排查过程**：

1. 宿主机裸跑 `_docker_sandbox`：正常 ✅ — 文件在宿主机的 `/tmp`，dockerd 能找到
2. `docker compose build` 构建 app 镜像：成功 ✅
3. `docker compose up` 启动后执行审查：`docker run` 不报错但沙箱内代码为空或不执行
4. 回到容器内手动测试：`docker run` 命令本身正常（docker CLI + docker.sock 工作），但挂载的文件内容为空
5. 推演路径：文件写在哪？→ 容器 `/tmp`。dockerd 在哪？→ 宿主机。宿主机能找到容器的 `/tmp` 吗？→ 不能。
6. 根因确认：两套文件系统不互通。

**修复**：共享目录方案

三步：

**① docker-compose.yml** — 挂载共享目录 + 传入宿主机路径：

```yaml
environment:
  - SANDBOX_TMP_HOST=${PWD}/sandbox-tmp  # 宿主机绝对路径，容器通过环境变量读取
volumes:
  - ./sandbox-tmp:/var/sandbox            # 双向绑定挂载
```

**② nodes.py `sandbox_executor`** — 写文件到共享目录：

```python
# 宿主机上 /var/sandbox 存在 → 写共享目录（DooD 模式）
# 宿主机上 /var/sandbox 不存在 → fallback /tmp（裸机模式）
with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False,
                    dir='/var/sandbox' if os.path.isdir('/var/sandbox') else None) as f:
```

**③ nodes.py `_docker_sandbox`** — 用宿主机路径传 `-v`：

```python
def _docker_sandbox(script_path: str) -> SandboxResult:
    host_dir = os.environ.get('SANDBOX_TMP_HOST', '/tmp')  # 宿主机路径
    filename = os.path.basename(script_path)                # 只取文件名
    host_path = os.path.join(host_dir, filename)            # 拼接宿主机完整路径
    # docker run -v {host_path}:/sandbox/code.py:ro         # dockerd 能找到
```

**修复后的数据流**：

```
宿主机 ./sandbox-tmp/                      app 容器 /var/sandbox/
     ↕ 双向同步（docker-compose volume）           ↕
   test.py ←──────── 同一个文件 ─────────→  test.py
     │                                              │
     │  docker -v .../test.py:...                  │  sandbox_executor 写
     ▼                                              │
  dockerd（宿主机执行）                              │
     │                                              │
     ▼                                              │
  sandbox 容器 /sandbox/code.py（只读挂载）
```

**设计要点**：

- `os.path.isdir('/var/sandbox')` 作为自动开关，无需手动切换裸机/容器模式
- `SANDBOX_TMP_HOST` 默认值 `/tmp`，裸机跑时不需要配任何环境变量
- `docker-compose.yml` 的 `${PWD}` 自动解析为宿主机项目目录

**经验教训**：

1. **DooD 模式下，所有 docker CLI 的路径参数必须以宿主机视角构造。** `docker run -v`、`docker run -e` 引用的文件路径、`docker cp` 的源/目标路径，全部是宿主机文件系统的路径。

2. **组件在裸机测通过后，搬进容器必须重新审计数据流。** 裸机跑时文件写 `/tmp` 和 dockerd 找 `/tmp` 是同一个文件系统，迁移后不成立。任何涉及"跨进程共享文件"的路径都要重新检查：这个路径是谁来读的？读的人看得见吗？

3. **`docker.sock` 是遥控线，不是文件传输管道。** 给了 app 容器 docker.sock 不等于给了它文件访问权限。dockerd 在宿主机干活，文件也得在宿主机上。

4. **用环境变量传递宿主机路径是标准做法。** `docker-compose.yml` 中 `${PWD}` 在 compose 解析时替换为宿主机当前目录，传给容器作为环境变量，容器代码读环境变量获取宿主机路径，三环节打通。

---

## 问题 #7：`retry_or_human` 返回值与条件边映射不一致 —— `KeyError: 'human_review'`

**日期**：2026-05-22

**错误信息**：
```
KeyError: 'human_review'
```
在 `retry_count >= MAX_RETRY` 时，`retry_or_human` 返回 `"human_review"`，但条件边映射表中没有这个 key，LangGraph 路由失败。

**触发条件**：重试次数达到上限（MAX_RETRY=3）后，沙箱仍然失败。

**触发代码**（`src/graph/builder.py`）：

路由函数（第 35-39 行）：
```python
def retry_or_human(state: AgentState) -> str:
    if state["retry_count"] >= MAX_RETRY:
        return "human_review"      # ← 返回这个值
    return "coder_agent"
```

条件边注册（第 117-124 行）：
```python
workflow.add_conditional_edges(
    "reflect_node",
    retry_or_human,
    {
        "output_node": "output_node",   # ← 映射表里没有 "human_review"
        "coder_agent": "coder_agent",
    }
)
```

**根因**：

这是一个**连带修改遗漏**。在优化重试节点时，`retry_or_human` 的返回值从 `"output_node"` 改成了 `"human_review"`（让重试耗尽后走人工介入而非直接输出失败报告），但条件边的映射表没有同步更新。返回值改了，映射表没改，LangGraph 收到一个映射表里不存在的目标，直接 KeyError。

**排查过程**：

1. B04 测试脚本 `test_b04_01_no_hitl_on_failure.py` 的检测 1-3 只测试了函数的返回值是否正确（`< MAX_RETRY → coder_agent`、`>= MAX_RETRY → human_review`），全部通过
2. 检测 4 只检查了图中是否存在 `human_review` 节点，没有检查条件边的映射是否包含 `human_review`
3. 端到端测试（#03-#05）依赖 LLM，重试到达上限的概率很低，未触发
4. 直到 Streamlit 前端开发时手动检查代码，才发现映射表遗漏

**修复**（`src/graph/builder.py`）：

```python
# 修复前
workflow.add_conditional_edges("reflect_node", retry_or_human, {
    "output_node": "output_node",
    "coder_agent": "coder_agent",
})

# 修复后
workflow.add_conditional_edges("reflect_node", retry_or_human, {
    "human_review": "human_review",
    "coder_agent": "coder_agent",
})
```

同时修复 `docs/graph-design.md` 中两处文档不一致：
- 第 130 行表格：`False → output_node(failed)` → `False → human_review`
- 第 227-230 行代码示例：`"output_node": "output_node"` → `"human_review": "human_review"`

**为什么 CLI 模式没暴露**：

CLI 模式（`run.py`）中 HITL 是演示模式自动批准的，流程走 `sandbox_executor` 的条件边 `should_retry_or_human`，当沙箱失败时直接进入 `reflect_node`。但如果重试达到上限后沙箱仍然失败，`retry_or_human` 同样会返回 `"human_review"` 触发 KeyError。只是测试用的示例代码比较简单，极少触发 3 次全部失败的情况。

**经验教训**：

1. **修改函数返回值时，必须同步检查所有消费者。** `retry_or_human` 的返回值由条件边映射表消费，二者是耦合的。改了返回值必须检查映射表、文档、测试。
2. **单元测试覆盖了函数行为但没覆盖集成的映射关系。** B04 #01 测试了函数返回值，但没有验证编译后的图中条件边是否包含该返回值的路由目标。测试的检测 4 只做了节点存在检查而非边映射检查。
3. **条件边映射表的 key 必须与路由函数的所有可能返回值一一对应。** 少一个就是 KeyError，多一个不影响（不会被路由到但也不报错）。原则是映射表的 key 集合 ⊇ 路由函数返回值集合。

---

## 问题 #8：checkpointer 序列化后 Pydantic `isinstance` 失效 —— FinalReport ValidationError

**日期**：2026-05-22

**错误信息**（Streamlit 前端）：
```
审查失败: 4 validation errors for FinalReport
action_items.0 Input should be a valid dictionary or instance of ActionItem [type=model_type, input_value=ActionItem(priority=1, se..., input_type=ActionItem]
action_items.1 ...（同上）
action_items.2 ...（同上）
action_items.3 ...（同上）
```

**触发条件**：Streamlit 前端 HITL 流程 — 用户点击「开始审查」→ 流程在 `human_review` 处暂停 → 用户点「确认」→ `update_state` → resume → `output_node` 创建 `FinalReport` 时报错。

**错误现象拆解**：

错误信息中有两个矛盾点：
- `input_type=ActionItem` — 输入值**确实是** ActionItem 实例
- `type=model_type` 错误 — Pydantic 却说"Input should be a valid dictionary or instance of ActionItem"

即：**对象是 ActionItem，但 Pydantic 的 `isinstance` 检查不认。**

**触发代码**（`src/graph/nodes.py` 第 427 行，`output_node`）：

```python
report = FinalReport(
    ...
    action_items=critic.action_plan if critic else [],
    ...
)
```

`critic` 来自 `state['critic_summary']`，`action_plan` 是 `list[ActionItem]`。放在 CLI 模式下正常，在 Streamlit HITL 模式下炸。

**根因**：

CLI 模式与 Streamlit 模式的关键差异 —— **checkpointer 序列化/反序列化**。

```
CLI 模式（一次 asyncio.run）：
  critic_agent 创建 CriticSummary
    └─ action_plan: [ActionItem#1, ActionItem#2, ...]  ← 原始 Python 对象
       ↓ 全程在内存中，无序列化
  output_node 读 critic.action_plan
    └─ isinstance(item, ActionItem) → ✅ True → 通过

Streamlit HITL 模式（两次 asyncio.run，中间有断点）：
  critic_agent 创建 CriticSummary
    └─ action_plan: [ActionItem#1, ActionItem#2, ...]
       ↓
  MemorySaver 将 state 序列化为 JSON 存盘
    └─ ActionItem 对象 → model_dump() → 纯字典
       ↓
  用户点按钮 → update_state → resume
       ↓
  JsonPlusSerializer 从 JSON 恢复 Python 对象
    └─ 纯字典 → model_validate() → "重建的" ActionItem
       ↓
  output_node 读 critic.action_plan
    └─ isinstance(item, ActionItem) → ❌ False → ValidationError
```

**为什么 `isinstance` 会失效**：

`JsonPlusSerializer`（LangGraph 的 JSON 序列化器）在反序列化 Pydantic 模型时，通过内部机制重建对象。重建后的 ActionItem 实例虽然在数据层面完全一致，但 **Python 解释器视其为不同的类对象**（类在内存中的 identity 不同）。`isinstance` 检查的是对象的 `__class__` 是否与给定类 **是同一个内存对象**，序列化绕一圈后这个判断失败了。

这是 Pydantic v2 与 LangGraph 1.1.x checkpointer 的兼容性边界情况。HITL 断点引入的序列化/反序列化是根本原因——**此前所有测试中 state 从未离开过内存，所以从未触发。**

**此前为何未发现**：

`run.py` 中的 HITL 是"伪中断"——`invoke()` → `update_state()` → `invoke(None)` 都在同一次脚本执行中完成，MemorySaver 虽然写了盘但从未在跨进程/跨线程的语境下被读取过。Streamlit 是第一个**真正跨 `asyncio.run()` 调用**的 HITL 实现。

**修复**（`src/models.py`，`FinalReport` 类）：

```python
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
            # isinstance 可能失效，model_dump 拆成纯字典再重建
            result.append(ActionItem.model_validate(item.model_dump()))
        else:
            result.append(item)
    return result
```

三种输入类型全覆盖：
- 纯字典（序列化后未重建）→ `model_validate` 直接验证
- ActionItem 但 `isinstance` 失效 → `model_dump()` 拆成纯字典 → `model_validate` 重建干净实例
- 干净的 ActionItem → 同样拆了重建，无实际影响的冗余操作

**为什么选 `field_validator` 方案而非修改 `output_node`**：

| 方案 | 改动位置 | 覆盖面 |
|------|---------|--------|
| `field_validator`（采用） | `models.py` FinalReport | 任何地方创建 FinalReport 都自动受保护 |
| `output_node` 手动清洗 | `nodes.py` | 只保护这一处，未来其他消费 `action_plan` 的地方还是可能炸 |

`field_validator(mode="before")` 是 Pydantic 专门为"上游数据类型不可控"场景设计的机制，不算打补丁。

**经验教训**：

1. **HITL 断点引入了隐式的序列化/反序列化路径。** 任何经过 checkpointer 持久化的 Pydantic 模型，恢复后 `isinstance` 检查都可能失效。这不是 bug，是序列化机制的本职工作——但调用方需要知道对象可能"换了身份"。
2. **"伪中断"和"真中断"的测试路径完全不同。** 同一次 `asyncio.run()` 内的 HITL 是伪中断，没有真正经过序列化。只有跨 `asyncio.run()` 调用（用户真正停下来再继续）才会触发真中断。真伪中断的行为差异需要刻意构造测试覆盖。
3. **Pydantic 的 `model_dump()` + `model_validate()` 是绕过 `isinstance` 问题的标准手段。** 把对象拆成纯 Python 数据结构再重建，等价于"数据原样、身份刷新"。
4. **`field_validator(mode="before")` 适合做数据净化层。** 不信任上游传入的 object 身份，只信任纯数据。拆→建的过程中任何数据层面的问题也会被 Pydantic 校验捕获。

---

## 问题 #9：[需人工] 标签不稳定 —— 硬编码凭据 60% 漏标，三次 prompt 改进无效，最终确定性兜底

**日期**：2026-05-22

**错误现象**：

对同一段含硬编码密码的代码跑 5 次审查，critic 对 `DB_PASSWORD = "admin123"` 的 [需人工] 标签命中率仅 40-60%。5 次中有 2-3 次判成了 [修复]，导致 coder 将密码改为 `os.environ.get('DB_PASSWORD', 'admin123')`——密码仍在代码中，只是换了个写法。

**触发条件**：任何包含硬编码凭据（密码/密钥/令牌/连接字符串）的代码。

**错误的假设和尝试**：

**尝试一：降 temperature。** 怀疑 temperature=0.1 的采样漂移导致 LLM 偶尔不走 [需人工] 路径。改为 0.0，跑 5 次——命中率无改善。

**尝试二：改 prompt 措辞。** 怀疑 [需人工] 的顶层判据"修复依赖当前文件之外的条件"把 LLM 推进了"先想修复方案再套规则"的推理路径。改为"问题涉及硬编码凭据/密钥/密码/令牌 → 凭据归宿必须在代码外"，让分类基于问题属性而非修复方案。跑 5 次——命中率无改善。

**尝试三：删除 `suggestion` 字段。** 怀疑安全审查员的 `suggestion`（"用 os.environ.get() 读取密码"）传入 critic 后与 [需人工] 规则产生冲突——审查员已经写好了一个一行改法，critic 看到后倾向于判 [修复]。删除 `Issue.suggestion` 字段，跑 5 次——[需人工] 命中率无改善，但评分稳定性大幅提升（score_before 波动从 20 降至 0）。

三次改动均失败后，我们对四个 [需人工] 标准做了逐条分析，发现一个结构性事实：**四条标准中，只有凭据类会被审查员实际触发。** 其余三条（新建文件、新依赖、跨文件改动）审查员根本不会报——审查员只审计代码，不审计文件系统、不追踪跨文件引用、不推荐第三方包。这意味着整个 [需人工] 机制只有一条活跃路径，而这条路径 LLM 恰好做不好。

**根因**：

不是 prompt 写得不够清楚，也不是 temperature 不够低。是 **LLM 的训练语料偏见**。

LLM 在预训练时见过几百万次这种问答：

```
Q: 密码硬编码了怎么办？
A: 用 os.environ.get() 读环境变量
```

`os.environ.get()` 就是互联网上公认的标准答案。当 critic 看到硬编码密码时，LLM 内部的概率天平是这样的：

```
[需人工] ← 我们的四行 prompt 规则（权重：几十个 token）
     vs
[修复]   ← 几百万条训练语料说 os.environ.get() 就是标准答案（权重：整个预训练）
```

四行 prompt 规则怎么可能掰得过整个预训练？这解释了为什么三次改动（调参、改措辞、删干扰信号）全部无效——不是在优化 prompt，是在跟 LLM 的肌肉记忆对抗。这场仗从一开始就赢不了。

同时，跨场景测试确认了问题边界：
- 硬编码 API Key：0% 命中（全部判 [修复]）
- 硬编码数据库 URL：0% 命中（全部判 [修复]）
- 硬编码 DB 密码：40% 命中

所有凭据变体全军覆没。且其他三条 [需人工] 标准在实战中不会被审查员触发，不是不可靠，是不活跃。

**修复**：

在 `critic_agent` 返回前加确定性兜底函数，不依赖 LLM 判定。

```python
# src/graph/nodes.py

CREDENTIAL_KEYWORDS = re.compile(
    r"os\.environ|getenv|环境变量|\.env|配置文件|外部存储|密钥管理|Secrets?\s*Manager",
    re.IGNORECASE,
)

def _guard_credential_manual_tag(summary: CriticSummary) -> None:
    """凭据类问题确定性兜底：category=SENSITIVE_INFO + 修复方案涉及外部化 → 强制 [需人工]"""
    if not summary.action_plan:
        return
    for item in summary.action_plan:
        if "[需人工]" in item.fix_instruction or "[跳过]" in item.fix_instruction:
            continue
        if item.category != IssueCategory.SENSITIVE_INFO:
            continue
        if CREDENTIAL_KEYWORDS.search(item.fix_instruction):
            item.fix_instruction = "[需人工] " + item.fix_instruction
```

双重条件确保不误杀：
1. **枚举判定**（确定性）：`item.category == IssueCategory.SENSITIVE_INFO` — 枚举比较，不依赖 LLM
2. **关键词匹配**（补充）：fix_instruction 含 `os.environ|getenv|环境变量|.env` 等外部化关键词

两个条件都满足才强制标 [需人工]。单独"敏感信息"但修复不涉及外部化（如日志泄露手机号）不会被误标。

调用位置：`critic_agent` 返回前，LLM 输出与 state 写入之间：

```python
if summary is None:
    return {}
_guard_credential_manual_tag(summary)  # ← 确定性兜底
return {"critic_summary": summary}
```

**修复后测试**：5/5 命中率 100%。

**经验教训**：

1. **LLM 不适合做确定性分类，即使任务看起来很简单。** "这是凭据问题吗？→ [需人工]"这个判断对人类来说是 0 秒反应，但对 LLM 来说每次都是概率采样。temperature=0、prompt 精确到原子级都改变不了这个本质。
2. **不要把 LLM 的训练语料常识等同于可控行为。** `os.environ.get()` 在训练数据里就是正确答案，LLM 真心认为自己修对了。它不是"不遵守规则"，是规则和常识冲突时常识赢了。
3. **当三次 prompt 改动全部无效时，停下来思考任务性质。** 如果 prompt 已经精确到"问题涉及硬编码凭据 → [需人工]"这种程度依然不行，说明不是 prompt 问题，是任务和工具的能力不匹配。确定性任务应该用确定性代码做。
4. **删除 `suggestion` 字段虽然没解决 [需人工]，但大幅提升了评分稳定性。** 审查员不应给 critic 提供修复建议——审查员的职责是发现问题，critic 的职责是决定怎么处理。suggestion 混淆了这两个角色的边界。
5. **枚举值是确定性判定的最佳锚点。** `IssueCategory.SENSITIVE_INFO` 是 LLM 填的枚举值，但一旦填好就是确定性的字符串比较对象。LLM 可能填错类别（降到 OTHER 的概率 ~5%），但它不能把 "SENSITIVE_INFO" 这个 Python 对象变成别的。字符匹配不可靠，枚举值可靠。
6. **问题边界比修复方案更重要。** 四条 [需人工] 标准中只有凭据类被实际触发，意味着只需要盯住这一个模式。如果四条都活跃且都不可靠，修复方案会复杂得多。先摸清边界再动手。

---

## 问题 #10：coder 不遵循 [需人工] 标签 —— 代码级过滤从源头阻断假修复

**日期**：2026-05-23

**错误现象**：

问题 #9 的确定性兜底保证了 critic 100% 给凭据类问题标 `[需人工]`。但 coder 收到 `[需人工]` 条目后，仍将 `DB_PASSWORD = "admin123"` 改为 `os.environ.get('DB_PASSWORD', 'admin123')`——做了假修复。critic 标对了，coder 没听。

**触发条件**：任何 `[需人工]` 条目到达 coder_agent，且 LLM 的预训练偏见覆盖了 prompt 指令。

**错误假设**：

问题 #9 修完后，认为 `[需人工]` 问题已经闭环。实际上只解决了**下游传递链条的第一个环节**（critic 标对），没检查**第二个环节**（coder 是否遵循标签）。

critic 和 coder 面临的是同一个 LLM、同一个预训练偏见。修复 critic 的方法是加代码级硬兜底，但 coder 端没有对等的硬防护——只有 prompt 里的几行文字指令：

```
判断规则：
- fix_instruction 含 [需人工] 或 [跳过] → 跳过，写入 skipped_items
```

**架构图的无声提示**：

```
critic_agent
  ├── LLM 四分类
  └── _guard_credential_manual_tag()  ← 代码级硬兜底 ✅

coder_agent
  ├── prompt: "含 [需人工] → 跳过"
  └── （无代码级防护）                  ← 空 ❌
```

两个节点的防护不对称。如果 LLM 在 critic 端压不住，在 coder 端同样压不住——面对的是同一个模型、同一种训练偏见。

**根因**：

两层原因，一深一浅。

**浅层根因（为什么 prompt 指令无效）**：与问题 #9 完全相同 —— LLM 预训练语料中 `os.environ.get()` 是硬编码凭据的"标准答案"。critic 的四行 prompt 规则掰不过整个预训练，coder 的三行 prompt 规则同样掰不过。事实上 coder 的偏见更重——它的角色是"修复者"，看到凭据问题后"修掉它"的本能比 critic 的"判断该不该修"更强。

**深层根因（为什么没有硬防护）**：`CoderResult.fixed_code` 是**整个文件的重写**，不是 patch 级别。LLM 在"生成整个文件"的过程中，凭据那一行触发了预训练修复本能，顺手就改了。即使 skipped_items 里写对了，fixed_code 里该行已经变了。prompt 指令控制的是 LLM 的"意图"，控制不了它在逐行生成代码时的"肌肉记忆"。

**排查过程**：

1. 检查 `test_st_01_5runs.py` 输出：5 轮中 `[需人工]` 命中率 100%（critic 端正确），但修复后密码行有 2-3 轮变成了 `os.environ.get()`
2. 确认问题 #9 的兜底函数正常工作——`critic_summary.action_plan` 中确实有 `[需人工]` 前缀
3. 追踪 coder_agent 代码：`for item in critic.action_plan` 遍历时**全部条目一视同仁**拼进 plan_text，无任何过滤
4. 确定根因：`[需人工]` 条目和普通条目一样传给了 coder LLM，LLM 看到后自行决定修不修
5. 验证假设：5 轮测试中密码行保持原样的概率为 0%（5/5 都被改了某些部分），确认 prompt 指令完全无效

**修复**：

代码级过滤 —— `[需人工]` / `[跳过]` 条目在进入 LLM 之前就从 action_plan 中剔除，coder 根本看不到这些条目。

改动位置：`src/graph/nodes.py` `coder_agent` 函数（第 230-301 行），三个改动点：

**① 过滤循环**（第 237-249 行）：

```python
# 修改前
plan_text = []
for item in critic.action_plan:
    plan_text.append(f"指令：{item.fix_instruction}")  # [需人工] 也传进去了

# 修改后
plan_text = []
skipped_from_critic = []  # 代码级拦截，不传给 coder
for item in critic.action_plan:
    if "[需人工]" in item.fix_instruction or "[跳过]" in item.fix_instruction:
        skipped_from_critic.append(f"行{item.lineno}: {item.fix_instruction}")
        continue  # ← 不进入 plan_text
    plan_text.append(f"指令：{item.fix_instruction}")
```

**② 空 plan_text 短路**（第 251-256 行）：

```python
if not plan_text:
    # 全部条目都是 [需人工] 或 [跳过]，无需调用 LLM
    return {"coder_result": CoderResult(
        fixed_code=state.get("original_code", ""),
        skipped_items=skipped_from_critic,
    )}
```

**③ 返回值合并**（第 299-300 行）：

```python
if skipped_from_critic:
    result.skipped_items = skipped_from_critic + (result.skipped_items or [])
```

**④ prompt 精简**：删除了 SystemMessage 中"fix_instruction 含 [需人工] 或 [跳过] → 跳过"的判定规则，以及 HumanMessage 末尾的跳过提醒。coder 不再需要理解标签语义，只看该修的条目。

**方案选择**：

| 方案 | 做法 | 评价 |
|------|------|------|
| 强化 prompt | 加倍强调 [需人工] 不可触碰 | 已验证无效（同问题 #9 的三次失败尝试） |
| coder 端硬兜底 | coder 返回后 diff，发现 [需人工] 行被改就用原行覆盖 | 可行但复杂：需要准确的 diff 逻辑，覆盖操作有风险 |
| **源头过滤（采用）** | 传给 coder 前剔除 [需人工] 条目 | 最简单、最可靠：coder 不知道这些条目存在，自然不会修 |

**修复后测试**（`tests/stability/test_st_01_5runs.py`）：

| 轮次 | [需人工] | 密码行修复后 |
|------|----------|-------------|
| #1 | 1/1 | `DB_PASSWORD = "admin123"` 未改动 |
| #2 | 1/1 | `DB_PASSWORD = "admin123"` 未改动 |
| #3 | 1/1 | `DB_PASSWORD = "admin123"` 未改动 |
| #4 | 1/1 | `DB_PASSWORD = "admin123"` 未改动 |
| #5 | 1/1 | `DB_PASSWORD = "admin123"` 未改动 |

假修复彻底消除。

**经验教训**：

1. **补了上游 bug 不等于修复了下游。** critic 标对了不等于 coder 会遵循。链条上的每个环节都可能被同一个 LLM 偏见独立攻破，需要各自独立加固。

2. **不对称防护是伪安全感。** critic 有 `_guard_credential_manual_tag()` 硬兜底，coder 只有 prompt 指令。看起来"prompt 写清楚了就行"，实际上 LLM 的预训练偏见对两个节点是等价的。两边都要有代码级防护。

3. **`CoderResult.fixed_code` 是全文件重写的设计隐含了风险。** LLM 生成整个文件时，任何一行都可能被"顺手改掉"，不受 fix_instruction 的约束。设计上 patch 级修改更安全，但 LangChain structured output 不支持逐行 patch 的可靠输出。当前方案（源头过滤）是这个约束下的最优解。

4. **`skipped_items` 的收集应该由确定性代码完成，不依赖 LLM。** coder 的 `skipped_items` 之前依赖 LLM 自觉填写——和 [需人工] 标注一样，不可靠。改为代码层直接从 critic 收集跳过条目，LLM 只负责填充自己跳过的硬禁令违规项。

5. **一个 bug 的修复方式往往暗示了同类 bug 的修复方式。** 问题 #9（代码兜底替代 prompt 规则）的解决模式直接适用于问题 #10——同样的 LLM 偏见 → 同样的"prompt 掰不过预训练" → 同样的代码级硬拦截。

---

## 问题 #11：删除 [跳过] 标签 —— 判据不适合 LLM、与 [需人工] 无差异、审查员检测不到对应问题

**日期**：2026-05-23

**背景**：

critic 最初设计为四分类：丢弃 / [需人工] / [跳过] / 修复。`[跳过]` 的原始意图是"问题真实，但自动修复风险高于收益"，让用户自己判断修不修。判据有三条：

- 修复涉及 3 行以上代码变更
- 修复会改变函数签名/类接口
- 修复涉及核心算法/状态机/并发逻辑

随着系统演进，`[跳过]` 标签一直不稳定（同一条问题有时标有时不标），被标记为"暂缓"处理。在本次会话中，用户要求重新评估 `[跳过]` 是否还有存在价值。

**决策过程**：

**第一步：追踪 [跳过] 的完整生命周期**

`[需人工]` 和 `[跳过]` 在当前架构中经过的代码路径完全一致：

```
critic 标签 → coder 代码级过滤 → skipped_items → status=partial → 前端展示
```

两个标签无任何功能差异。用户看到的是同一份 `skipped_items` 列表，同一个 `partial` 状态，同一个 `render_skipped_items` 组件。前端没有按条目审批的能力，用户无法对 `[跳过]` 项单独说"修"或"不修"。

**第二步：评估判据是否适合 LLM**

| 判据 | 问题 |
|------|------|
| 3 行以上代码变更 | LLM 不会精确数行，一个改变量名的 fix 可能 1 行但影响 5 个调用点 |
| 改变函数签名/类接口 | 与 coder 硬禁令完全重叠，coder 已有代码级拦截 |
| 核心算法/状态机/并发逻辑 | LLM 无法可靠判断什么是"核心"，三行计数器可能标 [跳过]，真正的竞态问题可能标修复 |

三条判据的共同特征：需要精确的代码分析能力，LLM 做不到稳定。

**第三步：评估审查员的检测边界**

关键发现：三位审查员本质是模式匹配器，不是语义分析器。它们的检测范围限于：

| 审查员 | 能检测的 | 不能检测的 |
|--------|---------|-----------|
| security | SQL 注入、pickle、硬编码凭据 | — |
| performance | 重复 import、未关闭连接 | 算法复杂度、缓存策略 |
| style | docstring、命名、格式 | — |

`[跳过]` 三条判据面向的"核心算法变更"、"并发逻辑改变"、"接口修改"——**审查员根本检测不到这类问题**。`[跳过]` 想保护的那个场景，审查员不会触及。

**结论**：`[跳过]` 的设计意图是好的（给用户决策权），但三个事实使它没有存在价值：
1. 判据天然不适合 LLM，导致标签不稳定
2. 与 `[需人工]` 无功能差异，混在一起降低 `[需人工]` 的纯度
3. 审查员检测不到需要 `[跳过]` 保护的问题

**修复**：

删除整个 `[跳过]` 分类，critic 从四分类简化为三分类（丢弃 / [需人工] / 修复）。

涉及文件：`src/graph/nodes.py`，6 处改动：

**① critic prompt**：删除 `[跳过]` 整段定义，`[需人工]` 下直接接 `修复`：

```
# 改前：如果是 → 按以下三类处理：
# 改后：如果是 → 按以下两类处理：
#
# [需人工] — ...
# [跳过] — ...        ← 删除整段
# 修复 — ...
```

**② `_guard_credential_manual_tag`**：删除 `[跳过]` 判断分支：

```python
# 改前
if "[需人工]" in item.fix_instruction or "[跳过]" in item.fix_instruction:
    continue

# 改后
if "[需人工]" in item.fix_instruction:
    continue
```

**③ `_strip_fake_tags`**：同上。

**④ `coder_agent` 过滤条件**：只剩 `[需人工]`：

```python
# 改前
if "[需人工]" in item.fix_instruction or "[跳过]" in item.fix_instruction:

# 改后
if "[需人工]" in item.fix_instruction:
```

**⑤ `coder_agent` 注释**：`skipped_from_critic` 注释、空 plan_text 注释更新。

**⑥ `output_node` 注释**：两处注释删掉 `[跳过]` 引用。

**经验教训**：

1. **一个功能的消亡不一定是它被设计错了，而是系统演进让它失去了土壤。** `[跳过]` 的原始意图（按条目审批）在架构中从未实现，当系统选择了"全量 HITL 暂停"而非"逐条审批"的交互模式后，`[跳过]` 就失去了行动意义。

2. **审查员的能力边界决定了下游设计的上限。** 如果审查员是语义分析器（能指出"这个算法该用二分查找"），[跳过] 的"核心算法变更"判据就有意义。但审查员是模式匹配器，[跳过] 保护的是审查员检测不到的问题——这本身就是一个逻辑悖论。

3. **判据设计要考虑执行者的能力。** "3 行以上"对人类是明确的，对 LLM 不是。"改变函数签名"对代码级检查是明确的，对 LLM 的文字推理不是。判据的可操作性由执行者的精度决定，不由设计者的意图决定。

4. **删除是合法的优化手段。** 不是所有问题都需要修——有时候最优解是承认这个设计在当前约束下没有价值，删掉比修更干净。删掉一个不稳定标签，系统简单了，用户看到的列表也清晰了。

5. **`[跳过]` 和 `[需人工]` 功能同质化是渐进发生的。** 最初设计时它们有语义差异（必看 vs 参考），但随着 coder 代码级过滤的加入、前端展示的统一，两者退化为同一行为。设计评审应该周期性地检查功能是否还坚守着最初的设计意图。

---

## 问题 #12：改作用域硬禁令被频繁突破 —— AST 级检测兜底 + critic prompt 约束反噬教训

**日期**：2026-05-23

**背景**：

coder 硬禁令有三条，全部在 SystemMessage 中以 prompt 指令形式实现：

```
1. 禁止改名 —— 函数名、类名、变量名、参数名一律不动
2. 禁止改签名 —— 不增删参数、不改返回类型
3. 禁止改作用域 —— 不得把局部变量提升为全局、或把全局降为局部
```

用户要求评估硬禁令是否必要、是否有效、是否需要优化。

**第一步：创建测量脚本，量化违规率**

新建 `tests/stability/test_st_04_hard_ban_risk.py`，设计三个场景，每个跑 5 次：

| 场景 | 类型 | 代码特征 | 后果 |
|------|------|---------|------|
| 事务隔离 | scope | conn/cursor 在函数内，模块级共享 TRANSACTIONS | 多调用共享状态，事务隔离被打破 |
| 改签名 | signature | `get_report(user_id, report_type="summary")` 有外部调用方 | 参数变化导致调用方报错 |
| 改名 | rename | `authenticate()` / `handle_request()` 公开 API | 外部引用断裂 |

**测试脚本开发中的两个小 bug**：

1. **`KeyError: 'original_sig'`**（test_st_04 初版）：`check_fixed_code()` 函数签名从 `(original, fixed, checks)` 重构为 `(original, fixed, scenario)` 以支持 type 字段路由不同检测逻辑，但函数体内 `scenario["checks"]["original_sig"]` 写错——`original_sig` 在 scenario 根级别不在 checks 内。修复：将 `original_sig` 和 `original_names` 放在 scenario 根级别，与 checks 平级。

2. **`NameError: 'checks' is not defined`**（test_st_04 第二版）：重构后函数体内还有遗留的裸 `checks` 引用（如 `checks.get("danger_keywords", [])`），应全改为 `scenario["checks"].get(...)`。修复：逐行检查所有引用点。

**第二步：分析测试结果**

| 场景 | 违规轮次 | 详情 |
|------|---------|------|
| 改作用域 | **5/5** | conn/cursor 被提升到模块级，事务隔离减弱 |
| 改签名 | 1/5 | 增加了一个类型注解参数，运行时无害 |
| 改名 | 0/5 | 从未被触发 |

结论：三条硬禁令中，改名和改签名几乎不被突破（coder prompt 中的禁令对它们足够有效），但**改作用域 100% 被突破**。

**第三步：逐场景全范围评估，判断能否一刀切拦截**

对"改作用域"做了场景枚举分析：

| 改作用域场景 | 后果严重程度 | 是否可自动拦截 |
|-------------|------------|:---:|
| 数据库连接从函数内移到模块级 | 事务隔离减弱（多调用共享连接） | 可检测 |
| import 从函数内移到文件顶部 | PEP 8 推荐写法，运行时无害 | **不应拦截** |
| 缓存变量从函数内移到模块级 | 多次调用共享引用，可能累积错误 | 可检测 |
| 常量定义从函数内移到模块级 | 合理重构，无副作用 | 不应拦截 |

关键发现：**import 移到模块顶部是 PEP 8 推荐的标准做法**，拦截它是错误的。所以不能一刀切"任何作用域变化都拒绝"——需要区分 import 和其他语句。

**第四步：尝试 critic prompt 添加禁令约束（反噬事件）**

讨论后决定在 critic prompt 的"修复"分类下加一行约束：

```
修复 — 不属于 [需人工] 的其余问题：
· fix_instruction 必须包含行号 + FROM → TO
· 修复限于当前函数/代码块内部，不涉及改名、改签名、改作用域  ← 新增
· 禁用"建议""考虑""可改为"等模糊词
```

**逻辑**：如果 critic 不发出涉及改作用域的 fix_instruction，coder 自然就不会执行。

**结果**——这个改动导致了比原始问题更严重的后果。重新跑 `test_st_05`（网页示例代码），5 轮中有 2 轮 critic 对 `DB_PASSWORD = "admin123"` 的修复建议变成了**"删除该行"**。

**为什么会这样**：

prompt 中的"不涉及改作用域"让 LLM 认为"把密码移到环境变量里会导致作用域变化，那既然不让改作用域，干脆建议把这行删了"。LLM 的推理路径是：

```
硬编码密码 → 标准做法是 os.environ.get()
  → 但 os.environ.get() 涉及模块级 import/常量 → 违反"不涉及改作用域"
    → 既然不能改作用域，删除该行是最"安全"的选择
```

prompt 约束没有消除 LLM 的修改冲动，只是把冲动导向了更危险的方向。删除凭据行比改作用域严重得多——前者导致功能缺失，后者只是事务隔离减弱。

用户当即指出必须回退。回退只删 critic prompt 中新增的那一行，不涉及其他改动。

**教训（当场）**：

> **prompt 约束不是免费的。** 加一个约束可能解决一个问题，但 LLM 会在约束的边界上找到更差的行为来满足它。这不是 LLM "反叛"，是它在约束空间中搜索合法输出的必然结果。约束越多，合法输出空间越小，LLM 被迫挤向边缘——而边缘行为往往是设计者没预期到的。

**第五步：设计方案，选择检测打回而非自动拦截**

critic prompt 路线失败后，回到 coder 端做代码级检测。

**方案对比**：

| 方案 | 做法 | 评价 |
|------|------|------|
| AST 检测 + 自动回退 | 检测到作用域变更，把 fixed_code 相关行用原代码覆盖 | 最安全，但覆盖逻辑复杂（行号偏移、多次修改叠加），且会误杀 import 移到顶部 |
| AST 检测 + 打回重试 | 检测到变更后设置 sandbox 失败状态，触发 reflect_node 重试 | 可能死循环——coder 每次重试都倾向于做同样的修改 |
| **AST 检测 + 标注警告（采用）** | 检测到变更后追加 `[作用域变更]` 到 `result.notes`，前端展示但不自动回退 | 不改动 coder 输出，用户可见，严重程度匹配（事务隔离减弱非代码崩溃） |

选择方案三的理由：改作用域 5/5 被突破但后果是"事务隔离减弱"不是"代码跑崩"，没有到必须全自动拦截的严重程度。检测 + 标注让用户知道"这处被 coder 改了作用域，建议关注"，已经比现状好得多。

**第六步：实现 `_detect_scope_violations()`**

```python
def _detect_scope_violations(original_code: str, fixed_code: str) -> list[str]:
    """检测 coder 是否将函数内语句提升到模块级（改作用域硬禁令兜底）

    只放行 import/from import，其余从函数内→模块级的移动一律标记。
    """
    if not original_code or not fixed_code:
        return []
    try:
        orig_tree = ast.parse(original_code)
        fixed_tree = ast.parse(fixed_code)
    except SyntaxError:
        return []

    def _non_import_stmts_in_funcs(tree):
        stmts = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for stmt in node.body:
                    if not isinstance(stmt, (ast.Import, ast.ImportFrom)):
                        try:
                            stmts.add(ast.unparse(stmt))
                        except Exception:
                            pass
        return stmts

    func_stmts = _non_import_stmts_in_funcs(orig_tree)

    violations = []
    for stmt in fixed_tree.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            continue
        try:
            unparsed = ast.unparse(stmt)
        except Exception:
            continue
        if unparsed in func_stmts:
            violations.append(f"[作用域变更] L{stmt.lineno}: {unparsed[:100]}")

    return violations
```

**设计要点**：

- 用 `ast.unparse()` 做语义比较而非字符串匹配——避免空格/缩进差异误报
- **import 白名单**：`isinstance(stmt, (ast.Import, ast.ImportFrom))` → 直接跳过，不报告。import 移到顶部是 PEP 8 标准写法
- `SyntaxError` → 返回空列表。语法错误不是此函数的检测目标
- `ast.unparse()` 本身抛异常 → 单条跳过，不影响其余遍历

**集成位置**（`coder_agent`）：

```python
# 代码级硬禁令兜底：检测 coder 是否将函数内语句提升到模块级
scope_violations = _detect_scope_violations(
    state.get("original_code", ""), result.fixed_code
)
if scope_violations:
    result.notes = ("[警告] 以下语句被从函数内提升到模块级，可能改变程序行为（如共享连接/状态）：\n"
                    + "\n".join(scope_violations)
                    + ("\n" + result.notes if result.notes else ""))
```

放在 `result.skipped_items = skipped_from_critic` 之后、`return` 之前——所有代码级防护集中在 coder_agent 的同一个代码块。

**第七步：验证**

**单元测试**（AST 检测逻辑）：

| 场景 | 原始代码 | 修复后代码 | 预期 |
|------|---------|-----------|------|
| 作用域违规 | conn 在函数内 | conn 移到模块级 | ✅ 检测到 2 处违规 |
| 正常修复 | conn 在函数内 | conn 留在函数内 | ✅ 0 误报 |
| import 移到顶部 | import 在函数内 | import 移到模块级 | ✅ 0 误报（白名单放行） |

**端到端测试**（实际 LLM 行为）：

`test_st_04` 重新跑，三个场景中 coder 当次没有实际触发作用域违规（coder prompt 中的硬禁令在这轮起了作用），`_detect_scope_violations` 正确判定为 0 违规。AST 检测作为兜底只在 coder 实际违规时才追加 notes，不影响正常流程。

**test_st_05 边角问题**：同行动态锁定的误报

`test_st_05` 第 5 轮报了一个"skipped_items 污染"错误。排查后发现是测试脚本的检测逻辑过于严格——"同行动态锁定"机制将 L8 的 `[修复]` 条目（异常处理）连带跳过（因为同行的 `[需人工]` 条目锁定了该行），该条目不包含 `[需人工]` 字符串，被测试脚本的污染检查 `"[需人工]" not in s` 误判为污染。

**这是测试的误判，不是 bug。** 同行动态锁定是故意设计的防护——防止 coder 通过同行的非需人工条目绕过行级保护。用户确认不需要修改测试脚本。

**经验教训**：

1. **prompt 约束是双刃剑，可能引发比原问题更差的行为。** critic prompt 加"不涉及改作用域"后，LLM 把"修密码"变成了"删密码行"。约束没有消除修改冲动，只是把冲动导向了更危险的方向。在 prompt 中加入新的否定性约束前，必须考虑 LLM 在约束空间中搜索到的次优解是什么。

2. **一个约束的反噬，往往比它想解决的问题更严重。** 改作用域是事务隔离减弱，删密码行是功能缺失。而这两个后果的严重程度完全不对等——prompt 约束没有"危害等级"的概念，它只知道在禁令边界内找一个合法输出。

3. **import 移到模块顶部是 PEP 8 推荐做法，硬禁令不能一刀切。** AST 检测通过 `isinstance(stmt, (ast.Import, ast.ImportFrom))` 白名单放行 import 移动，避免了误杀合理的代码组织优化。

4. **"检测打回"与"自动拦截"的选择要匹配后果严重程度。** 改作用域的后果是"事务隔离减弱"不是"代码崩溃"，检测+标注已经足够。如果是"代码必崩"级别的后果（如删除关键行），才值得做自动回退。

5. **`ast.unparse()` + `ast.parse()` 是代码语义比较的可靠工具。** 字符串级别的 diff 会被空格/缩进/注释干扰，AST 级别的比较只关注语义结构。Python 3.9+ 的 `ast.unparse()` 提供了稳定一致的单行输出格式。

6. **测试脚本本身的 bug 也需要记录。** `test_st_04` 开发中的两个小 bug（KeyError: 'original_sig' 和 NameError: 'checks'）虽然与主问题无关，但它们是开发过程的正常组成部分。记录它们有助于理解：重构函数签名时必须同步更新所有调用方和内部引用。

7. **同行动态锁定是一个正确但容易被误解的机制。** 它保护了 [需人工] 行的完整性，但产生的 skipped_items 条目不含 [需人工] 标签，容易被视为"污染"。注释和文档需要明确说明这个机制的存在。

8. **硬禁令的可靠边界是通过实践测量出来的，不是设计出来的。** 三条硬禁令中，改名 0/5、改签名 1/5、改作用域 5/5——只有实际跑过才知道哪条需要代码级兜底。设计阶段预设"三条都需要同等加固"会浪费工程资源，数据驱动的优先级排序更高效。
