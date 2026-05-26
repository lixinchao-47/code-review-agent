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

## 4. Docker 全项目容器化 ✅ 已完成（2026-05-25）

- `Dockerfile`：主应用镜像，Docker CLI 静态二进制 + Streamlit/Batch 双模式
- `docker-compose.yml`：开发部署（sandbox + app 一键启动）
- `docker-compose.deploy.yml`：生产部署（预构建镜像 + restart 策略）
- `sandbox/Dockerfile`：沙箱隔离镜像（非 root、最小化）
- `docker-entrypoint.sh`：`STREAMLIT_MODE` 环境变量切换 Streamlit / Batch
- `.streamlit/config.toml`：Docker headless 模式配置
- 阿里云 apt + pip 镜像加速，构建速度可接受

### 分发方案

- 阿里云容器镜像仓库 ACR（`crpi-g05pgblu4dg99gld.cn-shanghai.personal.cr.aliyuncs.com/lixinchao/`）已可用
- 推送命令：`docker tag code-review-agent:latest <acr>/code-review-agent:latest && docker push`
- CI/CD 可集成 GitHub Actions + ACR 自动构建推送

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

## 9. coder LLM 自发写入 notes 污染前端警告 ✅ 已修复（2026-05-25）

- 修复：`_detect_scope_violations` 无条件覆盖 `result.notes = ""`（无违规时），过滤 LLM 自述类文本
- 已 commit: 777c3cb

## 10. critic LLM 返回 None → 级联导致沙箱"修复代码为空" ✅ 已修复（2026-05-25）

- 修复：`coder_agent` 中 `critic is None` 时返回 `CoderResult(fixed_code=original_code)`，中断三次 `return {}` 级联
- 已 commit: 777c3cb

## 11. 同行动态锁定误伤 —— critic 误标 [需人工] + 锁定连坐 ✅ 已修复（2026-05-25）

- 修复：`coder_agent` 中去掉同行连坐逻辑，`[需人工]` 条目只锁定自身，不再连坐同行非 `[需人工]` 条目
- 已 commit: 777c3cb
