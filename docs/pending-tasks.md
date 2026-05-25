# 待办清单与技术债

临时记录文件，阶段八收尾时逐项清理。

## 1. Checkpoint 持久化（SqliteSaver → AsyncSqliteSaver 迁移失败）

### 目标
将 `InMemorySaver` 替换为 `AsyncSqliteSaver`，实现跨进程重启后的 checkpoint 恢复。

### 尝试过程

1. **第一轮（SqliteSaver sync）**
   - 替换 `InMemorySaver` → `SqliteSaver(sqlite3.connect("checkpoints.db"))`
   - 图编译成功，`run.py` 运行时报错：`NotImplementedError: The SqliteSaver does not support async methods`
   - 原因：前端通过 `astream_events(v2)` 异步流式执行，需要 async 版本的 checkpointer

2. **第二轮（AsyncSqliteSaver + asyncio.run）**
   - 在 `build_graph()` 内通过 `asyncio.run(_init())` 创建 `aiosqlite.Connection` → `AsyncSqliteSaver(conn)`
   - 现象：`run_until_complete` 在 WSL2 下死锁，进程挂起无响应
   - 隔离测试确认：同样的代码在纯 Python 脚本中可以执行，但与 `build_graph()` 的导入链组合后触发死锁

3. **第三轮（AsyncSqliteSaver + 持久事件循环）**
   - 改用 `asyncio.new_event_loop()` + `loop.run_until_complete()` 替代 `asyncio.run()`，避免循环关闭
   - 结果相同：WSL2 环境下依然死锁

### 根因分析
- `build_graph()` 是同步函数（Streamlit 前端直接调用），无法使用 `async with AsyncSqliteSaver.from_conn_string()` 上下文管理器
- `asyncio.run()` 关闭事件循环会导致 `aiosqlite.Connection` 失效
- 手动管理事件循环在 WSL2 下存在兼容性问题（可能与 WSL2 内核的 epoll/事件循环实现有关）

### 绕过方向（未尝试）
- **方案 A**：Streamlit 启动时预先创建 `AsyncSqliteSaver`（此时 async 环境可用），注入给 `build_graph()` 的可选参数
- **方案 B**：用 `langgraph-checkpoint-postgres` 替换 sqlite，Postgres 的 async 驱动可能兼容
- **方案 C**：不改 checkpointer，手动在 HITL 暂停时把关键 state dump 到 JSON 文件，重启后从文件恢复

### 影响评估
- HITL 会话内中断/恢复完全正常（`InMemorySaver` 覆盖）
- 唯一失效场景：HITL 暂停期间进程崩溃 + 用户在确认页停留（窗口极小，概率极低）
- 面试可一句话兜住："InMemorySaver 入门，升级路径已预留，LangGraph 原生支持替换"

## 2. 改作用域检测 → 自动回退

- 当前：`_detect_scope_violations()` 检测到函数内语句被提升到模块级后，仅写入 `CoderResult.notes` 警告
- 缺失：未做自动回退（将 fix 后的代码恢复为原始代码或拒绝该修改）
- 底层原因：仅做检测是阶段二快速闭环的 trade-off，自动回退需要重跑一轮 coder fix（复杂度较高）
- 轻量方案：检测到违规时，在 `CoderResult.notes` 写入警告（2026-05-24 已完成 notes → FinalReport → 前端 的透传链路）

## 3. test_st_04 补 notes 断言 ✅ 已修复（2026-05-24）

- 在作用域违规检测到时间，验证 `coder.notes` 非空
- 防止 `_detect_scope_violations()` 静默失效

## 4. Docker 镜像分发

- 路线图阶段六提到 "Docker 沙箱 + 主应用容器化 → 镜像分发"
- 当前 sandbox Dockerfile 已在 `sandbox/` 下，镜像只在本地构建
- 分发方案待定（Docker Hub / 私有 Registry / 打包到代码仓库）

## 5. test_st_05 同行动态锁定误判

- 用户已确认不用修（误判在可接受范围内）
- 后续若发现真正误拦截案例再回来看

## 6. 前端细节 ✅ 已修复（2026-05-24）

- ~~`streamlit_app.py` 第 511 行 `f2` 变量未使用~~ → 移除无用列布局
- ~~`streamlit_app.py` 第 24 行 `DEEPSEEK_API_KEY` 导入未使用~~ → 移除无用导入

## 7. reflect_node prompt 优化 ✅ 已修复（2026-05-24）

- 补了 root_cause / should_revert / new_strategy 的格式约束
- new_strategy 必须含目标行号 + FROM → TO，禁止模糊表述
- 记忆功能评估后砍掉：3 轮重试 + 沙箱反馈已覆盖，加记忆过度设计

## 8. 评分系统优化

- 当前：`score_before` 由 critic LLM 主观打分，`score_after` 按固定公式（每处修复 +2，失败扣 10）
- 问题：
  - score_before 依赖 LLM 判断，缺少客观锚点（如按问题数量/严重度加权公式）
  - score_after 固定 +2 不区分修复难度和影响范围
- 优化方向：
  - score_before：按 `COUNT(CRITICAL)*15 + COUNT(HIGH)*8 + COUNT(MEDIUM)*3 + COUNT(LOW)*1` 从 100 扣减
  - score_after：结合沙箱结果 + 实际修复覆盖率 + 剩余 [需人工] 项加权
- 优先级：低（当前评分趋势正确、前后对比有区分度，优化后可更客观但不影响核心功能）

## 9. coder LLM 自发写入 notes 污染前端警告

- 现象：前端弹出「**【警告】** 所有修改均按 fix_instruction 执行，未做任何额外改动」
- 根因：`CoderResult.notes` 被两个来源共用 —— 代码级 `_detect_scope_violations()` 写真正的警告，LLM 也自发往里写合规声明
- 绕过链路：coder_agent → output_node → FinalReport.notes → render_notes()，无过滤
- 修复方向：`output_node` 组装时过滤 LLM 自述类文本，或拆分为 `notes`（代码警告）和 `llm_notes`（LLM 备注）两个字段

## 10. critic LLM 返回 None → 级联导致沙箱"修复代码为空"

- 现象：前端跑示例代码一（SQL 注入 + 硬编码密码）时，沙箱验证失败 `exit_code=-1, stderr=修复代码为空`，偶发两次
- 根因：`critic_agent` → LLM 结构化输出解析失败返回 `None` → `return {}` → `critic_summary` 保留 `None` → `coder_agent` 检测 `critic_summary is None` → `return {}` → `coder_result` 保留 `None` → `sandbox_executor` 检测 `coder_result is None` → 返回错误
- 关键链路：三次 `return {}` 级联，中间缺少兜底 `CoderResult(fixed_code=original_code)` 断点
- 修复方向：`coder_agent` 中 `critic_summary is None` 时，不返回 `{}`，改为返回 `CoderResult(fixed_code=state['original_code'])`，让沙箱至少能执行原始代码

## 11. 同行动态锁定误伤 —— critic 误标 [需人工] + 锁定连坐

- 现象：示例代码一第 8 行，critic 同时产出两条指令：
  1. `[需人工]` — 「数据库连接复用需跨函数共享连接对象」
  2. 正常修复 — `conn = sqlite3.connect(...)` → `with sqlite3.connect(...) as conn:`
  第一条锁住第 8 行 → 第二条被同行动态锁定，显示 `(同行动态锁定) 行8-9：...→ with sqlite3.connect...`
- 根因 1：critic 把「用连接池」标为 `[需人工]`，但单文件内用 `with` 即可解决连接管理，不涉及其余 `[需人工]` 判据（新建文件/新依赖/跨文件改动）
- 根因 2：同行动态锁定的设计假设是「同行 [需人工] 意味着该行不能动」，但实际场景中同行可能存在独立的、安全的局部修复
- 修复方向：
  - 优先修 critic prompt：`[需人工]` 判据收紧，连接池/重构建议不属于跨文件需求，应降为普通修复
  - 备选：同行动态锁定改为「仅锁定 [需人工] 条目自身」，不再连坐同行非 [需人工] 条目
