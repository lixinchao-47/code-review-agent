# Graph 流程设计

## 1. 整体流程图

> 标记说明：`(s)` = 来自/写入 AgentState 的字段，`→` 后为 Pydantic 模型类型（只写类名）

```
                          ┌──────────────────────────────────┐
                          │ code_parser                      │
                          │ ──────────────────────────────── │
                          │ in:  original_code (s)           │
                          │ out: code_analysis (s)           │
                          │      → CodeAnalysis              │
                          └────────────────┬─────────────────┘
                                           │
              ┌────────────────────────────┼────────────────────────────┐
              │                            │                            │
              ▼                            ▼                            ▼
  ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐
  │ security_reviewer    │  │performance_reviewer  │  │ style_reviewer       │
  │ ──────────────────── │  │ ──────────────────── │  │ ──────────────────── │
  │ in:  code_analysis(s)│  │ in:  code_analysis(s)│  │ in:  code_analysis(s)│
  │      original_code(s)│  │      original_code(s)│  │      original_code(s)│
  │out: review_results(s)│  │out: review_results(s)│  │out: review_results(s)│
  │      → ReviewResult  │  │      → ReviewResult  │  │      → ReviewResult  │
  └──────────┬───────────┘  └──────────┬───────────┘  └──────────┬───────────┘
             │                         │                         │
             └─────────────────────────┼─────────────────────────┘
                                       │
                          ┌────────────▼────────-────┐
                          │ critic_agent             │
                          │ ──────────────────────── │
                          │ in:  review_results (s)  │
                          │      original_code (s)   │
                          │ out: critic_summary (s)  │
                          │      → CriticSummary     │
                          └────────────┬─────────────┘
                                       │
                          ┌────────────▼─────────-───┐
                          │ coder_agent              │
                          │ ──────────────────────── │
                          │ in:  critic_summary (s)  │
                          │      original_code (s)   │
                          │      reflection_notes(s) │
                          │      human_feedback (s)  │
                          │ out: coder_result (s)    │
                          │      → CoderResult       │
                          └────────────┬─────────────┘
                                       │
                          ┌────────────▼─────────-───┐
                          │ sandbox_executor         │
                          │ ──────────────────────── │
                          │ in:  coder_result.       │
                          │      fixed_code (s)      │
                          │ out: sandbox_result (s)  │
                          │      → SandboxResult     │
                          └────────────┬─────────────┘
                                       │
                               ┌───────┴───────┐
                               │  passed ==    │
                               │  True?        │
                               └───────┬───────┘
                       ┌───────────────┘   └───────────────┐
                       ▼ 是                                 ▼ 否
          ┌────────────────────────┐    ┌────────────────────────┐
          │ human_review           │    │ reflect_node           │
          │ ────────────────────── │    │ ────────────────────── │
          │ in:  coder_result (s)  │    │ in:  sandbox_result(s) │
          │      sandbox_result(s) │    │      coder_result (s)  │
          │ out: human_feedback(s) │    │      original_code (s) │
          └───────────┬────────────┘    │out: reflection_notes(s)│
                      │                 │     retry_count (s)    │
             ┌────────┴────────┐        └───────────┬────────────┘
             │ human_feedback  │                    │
             │ == ""?          │            ┌───────┴────────┐
             └────────┬────────┘            │ retry_count    │
          ┌─────────-─┘    └──────────┐     │ < MAX_RETRY?   │
          ▼ 是 (确认)    ▼ 否 (修改意见)    └───────┬────────┘
┌──────────────────┐  ┌──────────────────┐  ┌───────┘    └───────┐
│ output_node      │  │ coder_agent      │  ▼ 是                ▼ 否
│ ──────────────── │  │ (重新修复)       │  ┌────────────────────────┐
│ in: 所有字段 (s) │  └──────────────────┘  │ output_node            │
│ out: final_report│                        │ ────────────────────── │
│      (s)         │                        │ in:  所有字段 (s)      │
│      →FinalReport│                        │ out: final_report (s)  │
│      status (s)  │                        │      → FinalReport     │
└──────────────────┘                        │      status = "failed" │
                                            └────────────────────────┘
```

## 2. 节点清单

| 序号 | 节点名 | 类型 | 职责 | 输入 | 输出 |
|------|--------|------|------|------|------|
| 1 | `code_parser` | Agent | 解析 Python 代码，识别函数/类/导入/关键语句 | `original_code` | `code_analysis` |
| 2 | `security_reviewer` | Agent | 审查安全问题（注入、硬编码、反序列化等） | `code_analysis` | `review_results`(追加) |
| 3 | `performance_reviewer` | Agent | 审查性能问题（复杂度、N+1、内存等） | `code_analysis` | `review_results`(追加) |
| 4 | `style_reviewer` | Agent | 审查风格问题（PEP8、命名、注释等） | `code_analysis` | `review_results`(追加) |
| 5 | `critic_agent` | Agent | 汇总三路审查结果，去重，按严重度排序，生成统一修复方案 | `review_results` + `original_code` | `critic_summary` |
| 6 | `coder_agent` | Agent | 根据修复方案修改代码 | `critic_summary` + `original_code` + `reflection_notes`(可选) + `human_feedback`(可选) | `coder_result` |
| 7 | `sandbox_executor` | Tool | 在 Docker 沙箱执行修复后代码，验证是否能跑通 | `coder_result.fixed_code` | `sandbox_result` |
| 8 | `reflect_node` | Agent | 分析沙箱失败原因，生成新修复思路 | `sandbox_result` + `coder_result` + `original_code` | `reflection_notes` + `retry_count` |
| 9 | `human_review` | HITL | 展示修复结果给用户，等待确认或修改意见 | `coder_result` + `sandbox_result` | `human_feedback` |
| 10 | `output_node` | Function | 组装最终报告 | 所有字段 | `final_report` + `status` |

## 3. 边与路由逻辑

### 3.1 普通边（固定路由）

```
code_parser → [Send API] → security_reviewer
                          → performance_reviewer
                          → style_reviewer

security_reviewer ─┐
performance_reviewer├→ critic_agent
style_reviewer ────┘

coder_agent → sandbox_executor
human_review(通过) → output_node
reflect_node(retry<3) → coder_agent
```

### 3.2 条件边

| 条件边 | 源节点 | 判断逻辑 | 分支 |
|--------|--------|----------|------|
| `sandbox_check` | `sandbox_executor` | `sandbox_result.exit_code == 0` | True → `human_review` / False → `reflect_node` |
| `human_check` | `human_review` | `human_feedback == ""` (空字符串=确认) | True → `output_node` / False → `coder_agent` |
| `retry_check` | `reflect_node` | `retry_count < MAX_RETRY` | True → `coder_agent` / False → `human_review` |

## 4. Send API 并行设计

三个审查 Agent 无数据依赖，使用 LangGraph Send API 实现真正并行。

```python
# builder.py 中的关键代码
from langgraph.types import Send

def continue_to_reviewers(state: AgentState):
    """将 code_analysis 同时发给三个审查 Agent"""
    return [
        Send("security_reviewer", {"code_analysis": state["code_analysis"]}),
        Send("performance_reviewer", {"code_analysis": state["code_analysis"]}),
        Send("style_reviewer", {"code_analysis": state["code_analysis"]}),
    ]
```

### 关键注意事项

- 调用 Send 时使用的是**节点名**(`"security_reviewer"`),不是函数名
- 三个审查节点返回 `{"review_results": [result]}` (单元素列表)
- `review_results` 声明为 `Annotated[list[ReviewResult], operator.add]`，LangGraph 自动拼接
- 三个节点都完成后，LangGraph 自动触发 `critic_agent`

## 5. 条件边函数签名

```python
def should_retry_or_human(state: AgentState) -> str:
    """沙箱验证后的路由判断"""
    if state["sandbox_result"].exit_code == 0:
        return "human_review"
    return "reflect_node"

def should_continue_or_output(state: AgentState) -> str:
    """人工确认后的路由判断"""
    if state["human_feedback"] == "":
        return "output_node"
    return "coder_agent"

def retry_or_fail(state: AgentState) -> str:
    """反思后的路由判断"""
    if state["retry_count"] < MAX_RETRY:
        return "coder_agent"
    return "output_node"
```

## 6. Graph 构建伪代码

```python
from langgraph.graph import StateGraph, END
from langgraph.types import Send

def build_graph() -> StateGraph:
    workflow = StateGraph(AgentState)

    # 注册节点
    workflow.add_node("code_parser", code_parser_node)
    workflow.add_node("security_reviewer", security_reviewer_node)
    workflow.add_node("performance_reviewer", performance_reviewer_node)
    workflow.add_node("style_reviewer", style_reviewer_node)
    workflow.add_node("critic_agent", critic_agent_node)
    workflow.add_node("coder_agent", coder_agent_node)
    workflow.add_node("sandbox_executor", sandbox_executor_node)
    workflow.add_node("reflect_node", reflect_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("output_node", output_node)

    # 入口
    workflow.set_entry_point("code_parser")

    # 普通边: code_parser → Send API 并行分发
    workflow.add_conditional_edges("code_parser", continue_to_reviewers, ["security_reviewer", "performance_reviewer", "style_reviewer"])

    # 三路审查收束到 critic
    workflow.add_edge("security_reviewer", "critic_agent")
    workflow.add_edge("performance_reviewer", "critic_agent")
    workflow.add_edge("style_reviewer", "critic_agent")

    # critic → coder → sandbox
    workflow.add_edge("critic_agent", "coder_agent")
    workflow.add_edge("coder_agent", "sandbox_executor")

    # 沙箱条件边
    workflow.add_conditional_edges("sandbox_executor", should_retry_or_human, {
        "human_review": "human_review",
        "reflect_node": "reflect_node",
    })

    # 人工确认条件边
    workflow.add_conditional_edges("human_review", should_continue_or_output, {
        "output_node": "output_node",
        "coder_agent": "coder_agent",
    })

    # 反思条件边
    workflow.add_conditional_edges("reflect_node", retry_or_fail, {
        "coder_agent": "coder_agent",
        "human_review": "human_review",
    })

    # 终点
    workflow.add_edge("output_node", END)

    return workflow.compile()
```

## 7. HITL (Human-in-the-Loop) 机制

`human_review` 节点在 LangGraph 中标记为 `interrupt_before`，在执行前暂停。

```python
# compile 时设置断点
graph = workflow.compile(
    checkpointer=checkpointer,  # MemorySaver 持久化
    interrupt_before=["human_review"],
)
```

用户确认后的 resume 流程：

```python
# Streamlit 前端调用 (后续实现)
if user_clicked_confirm:
    graph.update_state(config, {"human_feedback": ""})
    graph.invoke(None, config)

if user_provided_feedback:
    graph.update_state(config, {"human_feedback": feedback_text})
    graph.invoke(None, config)
```

## 8. Checkpointer 持持久化

使用 `MemorySaver` 作为 Checkpointer，每次 state 更新自动保存。

```python
from langgraph.checkpoint.memory import MemorySaver

checkpointer = MemorySaver()
graph = workflow.compile(checkpointer=checkpointer, interrupt_before=["human_review"])

# 每次调用带上 thread_id，同一会话共享 state
config = {"configurable": {"thread_id": "user-session-123"}}
graph.invoke(initial_state, config)
```

### MemorySaver 的作用

- HITL 暂停时 state 被持久化到内存，前端可随时 resume
- 同一 thread_id 多次 invoke 共享同一 state 链
- 可查询历史 state（用于历史记录功能）
- **生产环境可替换为 `SqliteSaver` 或 `PostgresSaver`**

## 9. 流式输出

使用 LangGraph 的 `astream_events()` 捕获全链路事件，相比 `stream_mode="updates"` 颗粒度更细，
可以区分"节点开始""LLM 生成 token""节点结束"等具体状态。

### 9.1 事件类型

| 事件名 | 触发时机 | 携带信息 | 前端用途 |
|--------|----------|----------|----------|
| `on_chain_start` | 节点开始执行 | `name`（节点名） | 显示"安全审查中..."等状态 |
| `on_chat_model_stream` | LLM 逐 token 输出 | `content`（token 文本） | 实时展示 LLM 思考过程 |
| `on_chain_end` | 节点执行完成 | `name` + `output` | 显示"审查完成"，获取结构化结果 |
| `on_tool_start` | 工具调用开始 | `name`（工具名） | 显示"沙箱执行中..." |
| `on_tool_end` | 工具调用结束 | `name` + `output` | 显示"沙箱验证完成" |

### 9.2 调用方式

```python
async for event in graph.astream_events(initial_state, config, version="v2"):
    kind = event["event"]
    name = event["name"]

    if kind == "on_chain_start":
        # 节点开始 -> 更新进度状态
        yield {"type": "node_start", "node": name}

    elif kind == "on_chat_model_stream" and event["data"]["chunk"].content:
        # LLM 逐 token 输出 -> 流式展示思考内容
        content = event["data"]["chunk"].content
        yield {"type": "token", "node": name, "content": content}

    elif kind == "on_chain_end":
        # 节点结束 -> 获取结构化输出
        output = event["data"].get("output", {})
        yield {"type": "node_end", "node": name, "data": output}
```

### 9.3 前端状态映射

根据事件的 `kind` 和 `name`，前端区分以下展示状态：

| 事件组合 | 前端展示 |
|----------|----------|
| `on_chain_start` + `code_parser` | "正在解析代码结构..." |
| `on_chat_model_stream` + 审查节点 | 实时 token 逐字输出（打字机效果） |
| `on_chain_start` + `security_reviewer` | "安全审查中..." |
| `on_chain_start` + `performance_reviewer` | "性能审查中..." |
| `on_chain_start` + `style_reviewer` | "风格审查中..." |
| `on_tool_start` + `sandbox_executor` | "沙箱执行中..." |
| `on_chain_start` + `human_review` | "等待您确认修复结果" |
| `on_chain_end` + `output_node` | "报告生成完成" |

### 9.4 关键注意

- **节点函数不用改**: 各节点内部仍然正常 `invoke` 返回结果，流式订阅只在图执行层做
- **`version="v2"` 必须加**: LangGraph 1.1.x 的 v2 事件模式才支持完整事件分类
- **事件过滤不可省**: `astream_events` 会产生大量内部事件（如 prompt 构建、聊天模型调用内部细节），消费端必须按 `kind` 过滤，不能全量推给前端
- **并行审查时的区分**: 三个审查节点并行执行时，`name` 字段各自不同（`security_reviewer` / `performance_reviewer` / `style_reviewer`），前端应按节点名分开显示

## 10. 错误处理

| 场景 | 处理方式 |
|------|----------|
| LLM 调用失败 (网络超时) | 节点内部 try/except，返回错误信息写入 state，由 output_node 输出失败报告 |
| 代码无法解析 (语法错误) | `code_parser` 返回 `parse_error`，直接路由到 `output_node` 输出错误 |
| Docker 沙箱不可用 | 降级为 subprocess 执行，日志警告 |
| 三路审查某一路超时 | Send API 自身无超时机制，需在 LLM 调用层设置 timeout，超时返回空 `ReviewResult` |
