# Code Review Agent

基于 LangGraph 的多智能体代码审查与自动修复系统。输入代码，并行执行安全、性能、风格三维审查，自动去重排序后尝试修复，修复结果经沙箱验证，不通过则反思重试（最多 3 次），最终输出审查报告。

## 架构

```
code_parser → [security, performance, style] (并行审查)
  → critic_agent (去重 + 排序 + 评分)
  → coder_agent (自动修复)
  → sandbox_executor (沙箱验证)
    → 通过 → human_review (HITL 人工确认) → 输出报告
    → 失败 → reflect_node → 重试 (最多 3 次) 或标记失败
```

10 个节点：7 个 LLM 智能体 + 3 个工具/函数节点。

## 快速开始

```bash
# 安装
pip install -e .

# 命令行运行
python scripts/run.py --file path/to/code.py

# 启动 Web 界面
run-agent
# 浏览器打开 http://localhost:8501
```

## 配置

在项目根目录创建 `.env` 文件：

```env
DEEPSEEK_API_KEY=sk-你的key
LLM_MODEL=deepseek-chat
MAX_RETRY=3
SANDBOX_TIMEOUT=10
LOG_LEVEL=INFO
```

## Docker 部署

```bash
# 构建
docker build -t code-review-agent .

# 运行
docker run -d \
  --name code-review-agent \
  -p 8501:8501 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e DEEPSEEK_API_KEY=sk-你的key \
  code-review-agent
```

新电脑零源码部署指南见 [`docs/deploy-guide.md`](docs/deploy-guide.md)。

## 项目结构

```
src/code_review_agent/
├── config.py          # 配置读取
├── models.py          # Pydantic 数据模型
└── graph/
    ├── builder.py     # 图组装 + 条件路由
    ├── state.py       # AgentState 定义
    └── nodes/
        ├── reviewers.py     # code_parser + 三维审查
        ├── critic_coder.py  # 去重排序 + 自动修复
        ├── sandbox.py       # 沙箱执行验证
        └── terminal.py      # 反思 + HITL + 输出
```

## 技术栈

- **LangGraph** — 工作流编排
- **LangChain + DeepSeek** — LLM 调用
- **Pydantic v2** — 结构化输出
- **Streamlit** — Web 界面
- **Docker** — 沙箱隔离执行

## 文档

- [需求文档](docs/requirements.md)
- [状态设计](docs/state-design.md)
- [智能体设计](docs/agents-design.md)
- [图设计](docs/graph-design.md)
- [模型设计](docs/models-design.md)
- [部署指南](docs/deploy-guide.md)
- [开发日志](docs/dev-log.md)
