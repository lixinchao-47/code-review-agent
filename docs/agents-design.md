# Agents 角色与 Prompt 设计

## 1. Agent 全景

```
┌─────────────────────────────────────────────────────────┐
│                      10 个节点                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐ │
│  │code_parser│ │ security │  │   perf   │  │  style  │ │
│  │  解析代码  │ │  安全审查 │  │ 性能审查 │ │ 风格审查 │ │
│  └──────────┘  └──────────┘  └──────────┘  └─────────┘ │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐ │
│  │ critic   │  │  coder   │  │ sandbox  │  │ reflect │ │
│  │ 汇总排序  │  │ 自动修复  │ │  沙箱验证 │  │  反思分析  │ │
│  └──────────┘  └──────────┘  └──────────┘  └─────────┘ │
│  ┌──────────┐  ┌──────────┐                             │
│  │  human   │  │  output  │                             │
│  │  人工确认  │  │  输出报告  │                             │
│  └──────────┘  └──────────┘                             │
│                                                         │
│  其中 6 个是 LLM Agent，4 个是纯函数/Tool                  │
└─────────────────────────────────────────────────────────┘
```

## 2. Agent 分类

| 类型 | 节点 | 说明 |
|------|------|------|
| LLM Agent | code_parser, security_reviewer, performance_reviewer, style_reviewer, critic_agent, coder_agent, reflect_node | 调用 LLM 完成推理 |
| Tool/Function | sandbox_executor, output_node, human_review | 不调 LLM，执行系统操作 |

---

## 3. LLM 统一调用方式

所有 Agent 共享同一个 LLM 实例，定义在 `src/graph/nodes/_llm.py`：

```python
from langchain_deepseek import ChatDeepSeek
from config import DEEPSEEK_API_KEY, LLM_MODEL

llm = ChatDeepSeek(
    model=LLM_MODEL,              # deepseek-chat
    api_key=DEEPSEEK_API_KEY,
    temperature=0.1,              # 低温度保证输出稳定
)
```

各节点通过 `from graph.nodes._llm import llm` 导入共享实例。需要结构化输出时调用 `llm.with_structured_output(PydanticModel)`。

**设计决策:**
- temperature=0.1 而非 0，保留极轻微随机性避免卡死循环
- 审查类 Agent 使用 `with_structured_output` 强制返回 Pydantic 结构
- `reflect_node` 使用独立 `ChatDeepSeek(temperature=0.3)` 实例 —— 唯一 temperature != 0.1 的节点
- 所有 `invoke()` 调用点都有 None 守卫（LLM 结构化输出解析失败时兜底）

---

## 4. 各 Agent Prompt 设计

### 4.1 code_parser — 代码解析器

**定位:** 理解代码，不是审查代码。将原始文本提取为结构化摘要。

**System Prompt（简化版，靠 `with_structured_output(CodeAnalysis)` 强制 schema）:**

```
你是一个代码结构分析专家，只做客观的结构提取，不给审查意见。
```

**User Message:** 直接传入原始代码。

**输出模型:** `CodeAnalysis`（通过 `with_structured_output` 约束字段，Prompt 不再手写 JSON schema）

---

### 4.2 security_reviewer — 安全审查员

**定位:** 只关注安全，不管性能和风格。

**Prompt 策略（principle-driven，详见 `src/graph/nodes/reviewers.py`）:**

核心原则：只报告确认存在的安全漏洞，不推测潜在风险。

确认标准（双条件必须同时满足）：
1. 代码中存在危险操作（SQL 拼接/命令执行/路径拼接/硬编码凭据/反序列化/弱加密）
2. 该危险操作的输入来自不可信数据源

仅满足一条 → 不报告。硬编码凭据是唯一例外（凭据本身即是漏洞，不需要攻击面）。

每条问题附带 `cwe_id`（CWE 漏洞编号），增加报告专业性和可信度。

无确认漏洞 → `issues` 返回空列表 `[]`。

---

### 4.3 performance_reviewer — 性能审查员

**定位:** 只关注性能瓶颈和低效写法。

**Prompt 策略（principle-driven，详见 `src/graph/nodes/reviewers.py`）:**

核心原则：只报告从代码本身可直接确认的低效模式。无法确认数据规模 → 不报告，宁漏勿错。

按五个维度审查：时间复杂度、空间复杂度、I/O、数据结构、重复计算。

每条问题附带 `estimated_impact`（量化预估），帮助 Critic Agent 排序时量化优先级。

无确认问题 → `issues` 返回空列表 `[]`。

---

### 4.4 style_reviewer — 风格审查员

**定位:** 只关注代码可读性和规范性。

**Prompt 策略（principle-driven，详见 `src/graph/nodes/reviewers.py`）:**

核心原则：报告客观的风格违规，不报告个人偏好。

客观违规 = 违反 PEP 8 明确规定 OR 缺少必要文档/类型注解。命名品味差异、Pythonic 偏好等主观判断不报告。

按七个维度审查：命名、类型注解、格式、注释、函数设计、重复代码、异常处理。

每条问题附带 `pep8_ref`（PEP 8 条目编号，如 E501）。

无确认问题 → `issues` 返回空列表 `[]`。

---

### 4.5 critic_agent — 汇总仲裁者

**定位:** 不审查代码，只做"三合一"——去重、排序、生成修复方案。

**三分类判定系统（详见 `src/graph/nodes/critic_coder.py`）:**

对每个问题，critic 先判断是否影响正确性/安全性：
- **不影响**（纯风格/命名/docstring）→ 丢弃，不进入 action_plan
- **影响 + 需外部资源** → `[需人工]`（硬编码凭据、新建文件、新依赖、跨文件改动）
- **影响 + 单文件可修** → 普通修复指令，fix_instruction 含行号 + FROM → TO

**Guard 函数（确定性兜底，LLM 输出后执行）:**
- `_guard_credential_manual_tag()`: 凭据类问题 + 修复方案含外部化关键词 → 强制标 `[需人工]`
- `_strip_fake_tags()`: 剥离 LLM 自发造的 `[修复]` 标签，防止 coder 误入 skipped_items

**评分:** `score_before` 由 LLM 主观打分 0-100，`score_after` 在 output_node 按公式计算。

---

### 4.6 coder_agent — 修复执行者

**定位:** 忠实执行修复方案，不自行发挥。只修改有问题的地方。

**硬禁令（绝对禁止，fix_instruction 要求也不行，详见 `src/graph/nodes/critic_coder.py`）:**

1. 禁止改名 —— 函数名、类名、变量名、参数名一律不动
2. 禁止改签名 —— 不增删参数、不改返回类型
3. 禁止改作用域 —— 不得把局部变量提升为全局、或把全局降为局部

违反硬禁令的指令静默丢弃，不执行。

**优先级:** `human_feedback` > `reflection_notes` > `fix_instruction`

**Guard 函数（代码级兜底）:**
- `_detect_scope_violations()`: AST 对比检测 coder 是否将函数内语句提升到模块级，违规写入 `CoderResult.notes` 警告
- `[需人工]` 条目 + 同行条目在 coder 消费前被过滤到 `skipped_items`，不传给 LLM
- `critic_summary is None` → 返回 `CoderResult(fixed_code=original_code)` 兜底，避免级联空返回

### 4.7 reflect_node — 反思分析者

**定位:** 修复代码跑崩了，分析为什么崩，给下次修复提供思路。

**temperature:** 0.3（独立 `ChatDeepSeek` 实例，唯一高于 0.1 的节点）

**输出格式约束（详见 `src/graph/nodes/terminal.py`）:**
- `failure_type`: 语法错误 / 逻辑错误 / 引入新 bug / 环境问题
- `root_cause`: 点出具体哪处修改导致失败
- `should_revert`: 是否应回退（语法错误/新 bug → true）
- `new_strategy`: 必须含目标行号 + FROM → TO，禁止模糊表述（"重新检查""调整方案"等）

输出拆解存入 state：`reflection_notes` 只存 `new_strategy`，`retry_count += 1`

---

## 5. 非 LLM 节点设计

### 5.1 sandbox_executor（Tool 节点）

不是 Agent，是系统调用。双通道执行（详见 `src/graph/nodes/sandbox.py`）:

- **主通道（Docker）:** 检测到 `docker` 命令 → `docker run --network=none --memory=128m --cpus=0.5`，`-W error` 运行
- **降级通道（subprocess）:** Docker 不可用时 → `subprocess.run(['python3', '-W', 'error', script_path])`

`coder_result is None` 时返回 `SandboxResult(exit_code=-1, stderr='修复代码为空', passed=False)` 兜底。

### 5.2 human_review（HITL 节点）

不是 Agent，是 LangGraph 的 `interrupt` 断点。

```python
def human_review_node(state: AgentState) -> dict:
    """此节点在 interrupt 后执行，将用户反馈写入 state"""
    # human_feedback 在 resume 前已通过 update_state 写入
    # 这里只需做空操作，让流程继续
    return {}
```

### 5.3 output_node（Function 节点）

不是 Agent，是数据组装。详见 `src/graph/nodes/terminal.py`。

**score_after 计算公式:**
- 沙箱通过 + 有修改: `min(score_before + min(changes*2, (100-score_before)//2), 100)`
- 沙箱通过 + 无修改: `score_before`
- 沙箱失败: `max(score_before - 10, 0)`

**status 三态:**
- `success` — 沙箱通过 + 无 `[需人工]` 遗留
- `partial` — 沙箱通过 + 有 `[需人工]` 跳过项
- `failed` — 沙箱验证失败

**透传字段:** `skipped_items`（[需人工] 建议）、`notes`（审查警告，如作用域变更）

---

## 6. 各 Agent 关键参数汇总

| Agent | temperature | structured_output | 特殊性 |
|-------|-------------|-------------------|--------|
| code_parser | 0.1 | 是 | 共享 _llm 实例，只做客观描述 |
| security_reviewer | 0.1 | 是 | principle-driven，双条件确认 |
| performance_reviewer | 0.1 | 是 | principle-driven，五维度审查 |
| style_reviewer | 0.1 | 是 | principle-driven，客观违规 only |
| critic_agent | 0.1 | 是 | 三分类判定 + guard 函数兜底 |
| coder_agent | 0.1 | 是 | 硬禁令 + guard 函数 + None 守卫 |
| reflect_node | 0.3 | 是 | 独立实例，唯一高于 0.1，需要发散 |

---

## 7. Agent 与 Pydantic Model 的对应关系

| Agent | 输出的 Pydantic Model |
|-------|----------------------|
| code_parser | `CodeAnalysis` |
| security_reviewer | `ReviewResult` (单条) |
| performance_reviewer | `ReviewResult` (单条) |
| style_reviewer | `ReviewResult` (单条) |
| critic_agent | `CriticSummary` |
| coder_agent | `CoderResult` |
| reflect_node | `ReflectionResult` |
| sandbox_executor | `SandboxResult` |
| output_node | `FinalReport` |

> 以上 Model 定义见 `docs/models-design.md`（下一个文档）。
