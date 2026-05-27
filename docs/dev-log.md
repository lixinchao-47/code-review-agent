# 开发问题记录

> 全量开发日志：环境配置、设计决策、运行时 bug。踩一个记一个，供自己回溯。
> 深度 bug 分析精选见 [`dev-bug-dives.md`](./dev-bug-dives.md)。

## 目录

- [问题 #1：Python 包导入路径配置](#问题-1python-包导入路径配置)
- [问题 #2：流式输出方案选择](#问题-2流式输出方案选择)
- [问题 #3：`pip install -e .` 成功但 `import langgraph` 仍报 ModuleNotFoundError](#问题-3pip-install--e--成功但-import-langgraph-仍报-modulenotfounderror)
- [问题 #4：`python -c` 验证导入与 `python scripts/run.py` 的差异](#问题-4python--c-验证导入与-python-scriptsrunpy-的差异)
- [问题 #5：Send 第二个参数导致分支 state 缺少字段 —— `KeyError: 'original_code'`](#问题-5send-第二个参数导致分支-state-缺少字段--keyerror-original_code)
- [问题 #6：HITL `interrupt_before` 中断不抛异常 —— 流程静默走完但报告未生成](#问题-6hitl-interrupt_before-中断不抛异常--流程静默走完但报告未生成)
- [问题 #7：LLM 返回 `"issues": null` 导致 `AttributeError`](#问题-7llm-返回-issues-null-导致-attributeerror)
- [问题 #8：LLM 返回枚举非法值导致 `ValidationError` —— 系统性加固所有枚举字段](#问题-8llm-返回枚举非法值导致-validationerror--系统性加固所有枚举字段)
- [问题 #9：`with_structured_output` 返回 `None` 导致 `AttributeError` —— 全链路 None 保护](#问题-9with_structured_output-返回-none-导致-attributeerror--全链路-none-保护)
- [问题 #12：Windows 部署沙箱执行失败](#问题-12windows-部署沙箱执行失败)

---

## 问题 #1：Python 包导入路径配置

**日期**：2026-05-04

**问题描述**：
项目中 `src/config.py` 需要被其他模块导入，但 `src/` 不在 Python 默认搜索路径中，导致 `ModuleNotFoundError`。核心矛盾：代码放在 `src/` 目录下，但 Python 不知道去那里找。

**尝试过的方案**：

1. **直接在项目根目录用 `from src.config import ...`**
   - 结果：失败
   - 原因：在项目根目录时碰巧当前工作目录在 `sys.path` 里所以能找到，换到 `/tmp` 等目录立刻报错。不可移植，治标不治本。

2. **在 `config.py` 内部自动把 `src/` 加入 `sys.path`**
   - 结果：失败
   - 原因：先有鸡还是先有蛋——`config.py` 还没被导入时里面的代码不会执行，`sys.path` 没被修改，所以连 `import config` 本身都失败。

3. **`pip install -e .`（第一版 `pyproject.toml`，build-backend 配置错误）**
   - 结果：安装失败
   - 原因：`build-backend` 写成了 `setuptools.backends._legacy:_Backend`，Python 3.12 + 新版 pip 不支持该旧版后端。

4. **`pip install -e .`（第二版 `pyproject.toml`，修复了 build-backend）+ `from src.config import ...`**
   - 结果：安装成功，但 `/tmp` 下导入仍失败
   - 原因：`.pth` 文件把 `src/` 目录本身加入了 `sys.path`，导入时应该直接写模块名 `from config import ...`，而非带 `src.` 前缀。导入写法错误。

5. **创建 `scripts/run.py` 入口脚本，手动 `sys.path.insert`**
   - 结果：成功（在项目根目录下运行 `python scripts/run.py` 可正常导入）
   - 性质：辅助开发手段，不是 Python 包的标准安装方式，但有效。

**最终解决方案**（双轨制，互不冲突）：

- **方案 A（标准做法）**：`pip install -e .` 可编辑安装
  - `pyproject.toml` 使用 `build-backend = "setuptools.build_meta"`
  - `[tool.setuptools.package-dir]` 设置 `"" = "src"`
  - `src/` 及其所有子目录（`agents/`、`graph/`、`tools/`）都需要 `__init__.py`（空文件即可）
  - 安装后 `.pth` 文件自动把 `src/` 加入 `sys.path`
  - 导入写法统一为 `from config import ...`（不带 `src.` 前缀）

- **方案 B（开发辅助）**：`scripts/run.py` 入口脚本
  - 脚本开头手动 `sys.path.insert(0, SRC_DIR)`
  - 导入写法同样是 `from config import ...`
  - 适用于快速开发调试

- **两套机制互不冲突，导入写法完全统一。**

---

## 问题 #2：流式输出方案选择——`stream_mode="updates"` vs `astream_events`

**日期**：2026-05-05

**问题描述**：
设计文档 `graph-design.md` 最初使用 `graph.stream(stream_mode="updates")` 做流式输出。
该模式只能拿到节点**完成后的输出**，前端无法区分"节点开始执行""LLM 正在生成 token""节点执行完成"等中间状态，
颗粒度太粗。

**方案对比**：

| | `stream_mode="updates"` | `astream_events` |
|---|---|---|
| 颗粒度 | 仅节点完成后一个事件 | 节点开始/token生成/节点结束等多个事件 |
| 前端展示 | "正在审查..." → 突然完成 | "安全审查中..." → token逐字输出 → "审查完成" |
| LLM token 实时流 | 不支持 | 支持（`on_chat_model_stream`） |
| 实现复杂度 | 低 | 中（需按 `kind` 过滤事件） |
| 节点代码改动 | 无 | 无（只改图执行层） |
| 是否原生支持 | 是 | 是（LangGraph 自带） |

**最终决策**：采用 `astream_events(version="v2")`

**依据**：
1. 需求文档明确要求"实时看到三个 Agent 并行审查进度（流式输出）"，`astream_events` 才能真正实现
2. 节点函数内部不用任何改动，只在图调用层切换
3. LangGraph 原生支持，不引入额外依赖
4. `version="v2"` 是 LangGraph 1.1.x 的标准事件模式

---

## 问题 #3：`pip install -e .` 成功但 `import langgraph` 仍报 ModuleNotFoundError

**日期**：2026-05-11

**问题描述**：
执行 `pip install -e .` 成功，但运行 `python -c "from graph.builder import build_graph; build_graph()"` 时报错 `ModuleNotFoundError: No module named 'langgraph'`。同级路径的 `graph` 包能找到，但第三方依赖找不到。

**排查过程**：

1. **初步怀疑路径问题**：项目中导入使用裸模块名（`from graph.state import ...`），怀疑 `src/` 没加入 `sys.path`。回头查 `docs/dev-log.md` 问题 #1 已有解决方案——`pip install -e .` + `pyproject.toml` 中 `package-dir = { "" = "src" }`。
2. **排除路径问题**：`graph` 包能导入成功（`from graph.builder import build_graph` 未报路径错误），说明 `src/` 已在 `sys.path` 中，路径没问题。
3. **定位根因**：查看 `pyproject.toml`，发现 `[project]` 下**缺少 `dependencies` 列表**。`pip install -e .` 只注册了包路径（`.pth` 文件），但不会安装任何第三方依赖。

**根因**：

`pyproject.toml` 中 `[project]` 节点只声明了 `name`、`version`、`requires-python`，没有 `dependencies` 字段。`pip install -e .` 按声明做可编辑安装，缺少的依赖不装。

**解决方案**：

在 `pyproject.toml` 的 `[project]` 下补上 `dependencies` 列表：

```toml
[project]
dependencies = [
    "langgraph>=1.1,<2.0",
    "langchain>=1.2,<2.0",
    "langchain-deepseek>=1.0,<2.0",
    "pydantic>=2.0",
]
```

然后重新执行 `pip install -e .`，pip 自动安装全部依赖，问题解决。

---

## 问题 #4：`python -c` 验证导入与 `python scripts/run.py` 的差异

**日期**：2026-05-11

**问题描述**：
在未执行 `pip install -e .` 的情况下，用 `python -c "from graph.builder import ..."` 验证模块导入，报 `ModuleNotFoundError`。但运行 `python scripts/run.py` 却能正常导入。

**原因**：

`python -c` 直接执行代码，不经过 `scripts/run.py` 中的 `sys.path.insert(0, SRC_DIR)`，因此 `src/` 不在 `sys.path` 中。而 `python scripts/run.py` 会先执行脚本顶部的路径拼接代码，`src/` 被正确加入 `sys.path`。

**结论**：

两种验证方式各有前提：

| 验证方式 | 前提条件 | 适用场景 |
|----------|---------|---------|
| `python -c "from graph.builder import ..."` | 需先 `pip install -e .`（`.pth` 自动加路径） | 快速验证包是否可导入 |
| `python scripts/run.py` | 不需要 `pip install -e .`（脚本自己加路径） | 完整运行入口 |

---

## 问题 #5：Send 第二个参数导致分支 state 缺少字段 —— `KeyError: 'original_code'`

**日期**：2026-05-11

**错误信息**：
```
KeyError: 'original_code'
During task with name 'security_reviewer'
```

**触发位置**（`src/graph/nodes.py`，`security_reviewer` 内）：
```python
HumanMessage(content=f"原始代码：{state['original_code']}"),
```

**原因**：Send 分发函数只传了 `code_analysis`，`original_code` 未传导致 KeyError。

**错误代码**（`src/graph/builder.py`）：
```python
def fanout_to_reviewers(state: AgentState) -> list[Send]:
    return [
        Send("security_reviewer", {"code_analysis": state["code_analysis"]}),     # ← 只传了一个字段
        Send("performance_reviewer", {"code_analysis": state["code_analysis"]}),
        Send("style_reviewer", {"code_analysis": state["code_analysis"]}),
    ]
```

**错误理解**：认为 Send 第二个参数会叠加到主 state 上，分支能自动继承主 state 其他字段。

**正确理解**：**Send 第二个参数就是目标分支的全部 state 输入**。主 state 字段不会自动带过来，需要什么必须全部显式传入。

```
错误模型：分支 state = 主 state + Send 覆盖
正确模型：分支 state = Send 第二个参数（仅此而已）
```

**修复代码**（`src/graph/builder.py`）：
```python
def fanout_to_reviewers(state: AgentState) -> list[Send]:
    return [
        Send("security_reviewer", {
            "code_analysis": state["code_analysis"],
            "original_code": state["original_code"],   # ← 新增：必须显式传
        }),
        Send("performance_reviewer", {
            "code_analysis": state["code_analysis"],
            "original_code": state["original_code"],   # ← 同上
        }),
        Send("style_reviewer", {
            "code_analysis": state["code_analysis"],
            "original_code": state["original_code"],   # ← 同上
        }),
    ]
```

---

## 问题 #6：HITL `interrupt_before` 中断不抛异常 —— 流程静默走完但报告未生成

**日期**：2026-05-11

**错误现象**：运行 `python scripts/run.py`，无报错，但 `final_report` 为 `None`，`status` 为 `"running"`。

```
正在执行审查流程...

=== 最终审查报告 ===
报告未生成，请检查上游流程
```

**错误代码**（`scripts/run.py`）：
```python
# 错误：以为 interrupt_before 会抛异常
try:
    result = app.invoke(initial_state, config)
except Exception:                                          # ← 永远抓不到
    app.update_state(config, {"human_feedback": ""})
    result = app.invoke(None, config)
```

**根因**：LangGraph 的 `interrupt_before` 中断**不抛异常**，`invoke()` 在断点处静默返回当前 state。`except Exception` 抓不到，代码直接跑去读 `final_report`，此时 `output_node` 还没执行，自然是 `None`。

**核心机制**：`interrupt_before` 不是崩溃，而是把当前 state 写入 checkpointer（MemorySaver），然后让 `invoke()` 正常返回。调用方需要主动通过 `app.get_state(config).next` 检查是否真的完成：

| `next` 值 | 含义 |
|-----------|------|
| `()` 空元组 | 工作流已完全结束 |
| `('human_review',)` 等 | 中断在对应节点前，需 resume |

**修复代码**（`scripts/run.py`）：
```python
result = app.invoke(initial_state, config)

state_snapshot = app.get_state(config)
if state_snapshot.next:                                   # ← 新增：检查是否真完成
    app.update_state(config, {"human_feedback": ""})
    result = app.invoke(None, config)
```

---

## 问题 #7：LLM 返回 `"issues": null` 导致 `AttributeError: 'NoneType' object has no attribute 'issues'`

**日期**：2026-05-11

**错误信息**：
```
AttributeError: 'NoneType' object has no attribute 'issues'
During task with name 'critic_agent'
```

**触发代码**（`src/graph/nodes.py`，`critic_agent` 内）：
```python
for r in state['review_results']:
    for issue in r.issues:   # ← r.issues 是 None，遍历崩溃
```

**原因**：`ReviewResult` 中 `issues: list[Issue] = Field(default_factory=list)`，`default_factory` 只在"字段缺失"时生效。LLM 在 JSON 中写了 `"issues": null`，Pydantic 将 `null`（Python `None`）当作合法值直接赋值，跳过 default_factory。

```
不传 "issues"     → default_factory 生效 → issues = []      ✅
传 "issues": null  → Pydantic 赋 None   → issues = None     ❌
传 "issues": [...]  → Pydantic 赋列表    → issues = [...]    ✅
```

**修复代码**（`src/models.py`，`ReviewResult` 模型）：
```python
class ReviewResult(BaseModel):
    issues: list[Issue] = Field(default_factory=list)

    @field_validator("issues", mode="before")              # ← 新增
    @classmethod                                           # ← 新增
    def default_issues_to_empty(cls, v: list | None) -> list:  # ← 新增
        return v if v is not None else []                  # ← 新增：null → []
```

在模型层拦截 `null` 而非在下游节点的 `for` 循环处加 `if` 保护——模型是数据入口，脏数据从这里拦下，所有下游节点受益。

---

## 问题 #8：LLM 返回枚举非法值导致 `ValidationError` —— 系统性加固所有枚举字段

**日期**：2026-05-13

**错误信息**：
```
ValidationError: 2 validation errors for ReviewResult
issues.2.category  Input should be '注入', '敏感信息', ... [input_value='安全']
issues.3.category  Input should be '注入', '敏感信息', ... [input_value='资源管理']
During task with name 'style_reviewer'
```

**原因**：`style_reviewer` 节点中 LLM 给 `category` 返回了枚举外的值。`"安全"` 是 ReviewDimension 的值（LLM 搞混了），`"资源管理"` 是枚举里根本不存在的词。

**系统性排查**：LLM 通过 `with_structured_output` 输出的所有枚举字段都有越界风险，共 4 模型 7 字段：

| # | 模型 | 字段 | 枚举 | 输出节点 |
|---|------|------|------|---------|
| 1 | `Issue` | `severity` | `Severity`（4 种） | 三个审查员 |
| 2 | `Issue` | `category` | `IssueCategory`（17 种） | 三个审查员 |
| 3 | `ReviewResult` | `dimension` | `ReviewDimension`（3 种） | 三个审查员 |
| 4 | `ActionItem` | `severity` | `Severity`（4 种） | `critic_agent` |
| 5 | `ActionItem` | `category` | `IssueCategory`（17 种） | `critic_agent` |
| 6 | `ActionItem` | `dimension` | `ReviewDimension`（3 种） | `critic_agent` |
| 7 | `ReflectionResult` | `failure_type` | `FailureType`（4 种） | `reflect_node` |

**分两类处理**：

**第一类（5 个字段）— LLM 有判断权但可能写错，加 `field_validator` 兜底：**

| 字段 | fallback |
|------|----------|
| `Issue.severity`、`ActionItem.severity` | `MEDIUM` |
| `Issue.category`、`ActionItem.category` | `"其他"` |
| `ReflectionResult.failure_type` | `LOGIC_ERROR` |

**修改代码**（以 `Issue.severity` 为例）：

```python
class Issue(BaseModel):
    severity: Severity                      # 修改前：无保护

    @field_validator("severity", mode="before")  # ← 新增
    @classmethod                                 # ← 新增
    def unknown_severity_fallback(cls, v):       # ← 新增
        try:                                     # ← 新增
            return Severity(v) if isinstance(v, str) else v
        except ValueError:                       # ← 新增
            return Severity.MEDIUM               # ← 新增：非法值兜底
```

（`Issue.category`、`ActionItem`、`ReflectionResult` 同结构，fallback 分别为 `IssueCategory.OTHER`、`FailureType.LOGIC_ERROR`）

**第二类（2 个字段）— `dimension` 不应让 LLM 填：**

- `ReviewResult.dimension`：节点知自身身份，**节点内硬覆盖**：
  ```python
  # security_reviewer 内（修改后新增一行）
  result.dimension = ReviewDimension.SECURITY
  ```
  （`performance_reviewer` → `PERFORMANCE`，`style_reviewer` → `STYLE`）

- `ActionItem.dimension`：critic 去重合并后语义模糊且下游未消费，**直接删除该字段**。
  ```python
  class ActionItem(BaseModel):
      priority: int
      severity: Severity
      category: IssueCategory
      # dimension: ReviewDimension   ← 删除
      description: str
      lineno: int
      fix_instruction: str
  ```

---

## 问题 #9：`with_structured_output` 返回 `None` 导致 `AttributeError` —— 全链路 None 保护

**日期**：2026-05-13

**错误信息**：
```
AttributeError: 'NoneType' object has no attribute 'dimension'
During task with name 'style_reviewer' and id 'd0b1d507-533a-96a7-5478-d9c6fd93e976'
```

**原因**：`with_structured_output` 解析 LLM 返回的 JSON 完全失败时，不抛异常而是返回 `None`。问题 #3 和 #4 加的 `field_validator` 只保护"模型构造成功但字段值非法"，不保护"模型根本没构造出来"的情况。`style_reviewer` 的 `result.dimension = ...` 对 None 调用属性直接崩。

**攻击面**：`nodes.py` 中 7 个 `structured_llm.invoke()` 调用点全部没有 None 守卫：

| # | 节点 | 炸点 |
|---|------|------|
| 1 | `code_parser` | `analysis`=None → 下游 `.functions` AttributeError |
| 2 | `security_reviewer` | `result`=None → `.dimension` AttributeError |
| 3 | `performance_reviewer` | 同上 |
| 4 | `style_reviewer` | 同上 ← 本次触发 |
| 5 | `critic_agent` | `summary`=None → 下游 `.action_plan` AttributeError |
| 6 | `coder_agent` | `result`=None → 下游 `.fixed_code` AttributeError |
| 7 | `reflect_node` | `reflection`=None → `.new_strategy` AttributeError |

**修复**：两件事 ——（1）7 个调用点加 None 守卫，返回安全默认值；（2）3 个消费端（`coder_agent`、`sandbox_executor`、`reflect_node`）加输入守卫。

生产端 fallback 策略：

| 节点 | `invoke()` 返回 None 时 |
|------|----------------------|
| `code_parser` | 返回空 `CodeAnalysis()` |
| 三个审查员 | 返回 `{"review_results": []}` |
| `critic_agent` / `coder_agent` | 返回 `{}` |
| `reflect_node` | 返回默认反思文本 + `retry_count+1` |

消费端：`coder_agent` 检查 `critic_summary is None`、`sandbox_executor` 和 `reflect_node` 检查 `coder_result is None`。

**性质**：间歇性 bug，LLM 输出质量波动触发。同样代码跑两次，一次炸一次不炸。

---

## 问题 #12：Windows 部署沙箱执行失败

**日期**：2026-05-26

**场景**：在 Windows 新电脑上通过 `docker-compose.deploy.yml` 从 ACR 拉取镜像部署，Streamlit 正常运行，但沙箱验证一直失败。

---

### 阶段一：沙箱超时

**现象**：沙箱每次执行都超时（10 秒后返回失败），三次重试全部超时。

**排查**：`_docker_sandbox` 中 `docker run` 用的镜像名是硬编码的 `'code-review-sandbox'`（短名），但 ACR 拉下来的镜像以完整 registry 路径存储：`crpi-xxx.cn-shanghai.personal.cr.aliyuncs.com/lixinchao/code-review-sandbox:latest`。Docker 不会自动将短名匹配到完整名，`docker run code-review-sandbox` 找不到镜像，每次 pull 超时。

**修复**：
- `src/config.py`：新增 `SANDBOX_IMAGE` 环境变量，默认值 `code-review-sandbox`
- `src/graph/nodes/sandbox.py`：`_docker_sandbox` 中硬编码的 `'code-review-sandbox'` 改为读 `SANDBOX_IMAGE`
- `docker-compose.deploy.yml`：注入 `SANDBOX_IMAGE` 为完整 ACR 路径

---

### 阶段二：沙箱快速失败（`can't find '__main__' module`）

**现象**：超时修复后，沙箱不再超时，但执行秒失败，stderr 报：

```
/usr/local/bin/python3: can't find '__main__' module in '/sandbox/code.py'
```

**根因**：`_docker_sandbox` 通过 `-v {host_path}:/sandbox/code.py:ro` 挂载文件到沙箱容器。`host_path` 由 `SANDBOX_TMP_HOST` + 文件名拼成。在 Windows Docker Desktop + WSL2 环境下，宿主机的 `D:\code-review-agent\sandbox-tmp` 在 WSL2 中对应 `/mnt/d/...`，但 Docker Desktop 内部的路径映射不保证与 WSL2 的 `/mnt/` 完全一致。传给 `docker run -v` 的源路径实际不存在时，Docker 不会报错，而是在目标位置**创建一个空目录**。沙箱容器里的 `/sandbox/code.py` 变成了空文件夹而非 Python 脚本，Python 自然报 `can't find '__main__' module`。

**本质问题**：`-v` 文件挂载依赖宿主机的绝对路径，而跨平台（Linux/Windows/Mac）下该路径不可移植。`SANDBOX_TMP_HOST` 这个环境变量本身就是"让用户猜自己系统的正确路径"，本质上是脆弱的。

**修复（根本性）**：弃用 `-v` 文件挂载，改用 stdin 管道传代码。

```python
# 旧方案：写临时文件 → 构造 host_path → -v 挂载
'docker', 'run', '--rm',
'-v', f'{host_path}:/sandbox/code.py:ro',
'python3', '-W', 'error', '/sandbox/code.py',

# 新方案：stdin 直接传代码，零文件依赖
'docker', 'run', '--rm', '-i',
'python3', '-W', 'error', '-',
input=fixed_code,    # subprocess.run 的 input 参数
```

`_docker_sandbox` 签名从 `(script_path: str)` 改为 `(fixed_code: str)`，`sandbox_executor` 中 Docker 路径直接传代码字符串，subprocess 降级路径保留原来的临时文件方式。

**连带清理**：
- `docker-compose.deploy.yml`：删除 `SANDBOX_TMP_HOST`（不再需要）
- `docs/deploy-guide.md`：删除跨系统路径配置说明

**教训**：Docker 文件挂载的路径在跨平台环境下不可靠，能用 stdin/stdout 管道解决的问题就不要引入文件路径依赖。`docker run -v` 挂载不存在的源路径时**静默创建空目录**而非报错，这个行为非常坑。
