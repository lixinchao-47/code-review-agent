# 沙箱 Docker 化日志

## 当前状态（2026-05-20）

### 改前实现

```python
# nodes.py sandbox_executor —— subprocess 直接执行，零隔离
result = subprocess.run(
    ['python3', '-W', 'error', tmp_path],
    capture_output=True, text=True, timeout=10,  # timeout 硬编码
)
```

- 在宿主机直接开子进程跑修复后的代码
- 无网络隔离、无内存限制、无权限限制
- 超时 10 秒硬编码，无视 `.env` 的 `SANDBOX_TIMEOUT`
- 临时文件用后未清理

### 目标

Docker 容器隔离执行，满足需求文档 4.1 节安全要求。

---

## Dockerfile 设计

**文件**：`sandbox/Dockerfile`

```dockerfile
FROM python:3.12-slim
RUN useradd -m sandboxuser && mkdir -p /sandbox && chown sandboxuser:sandboxuser /sandbox
USER sandboxuser
WORKDIR /sandbox
```

| 决策 | 理由 |
|------|------|
| `python:3.12-slim` | 最小体积（~50MB），拉取快 |
| `useradd sandboxuser` | 非 root 执行，限制容器内权限 |
| 不预装任何第三方包 | 被审查代码通常只用标准库，减少攻击面 |

**镜像**：`code-review-sandbox:latest`（123af689a2c5，内容 43.2MB）

---

## 代码改动

**文件**：`src/graph/nodes.py`

### 新增导入

```python
import os           # os.unlink() 清理临时文件
import shutil       # shutil.which('docker') 检测 docker 可用性
from config import SANDBOX_TIMEOUT  # 替换硬编码 timeout=10
```

### 拆分三个函数

```
sandbox_executor (入口，LangGraph 节点)
  ├─ coder_result 为 None → 返回失败（Bug #5 守卫保留）
  ├─ 写临时文件
  ├─ shutil.which('docker')?
  │    ├─ 有 Docker → _docker_sandbox()
  │    └─ 无 Docker → _subprocess_sandbox()  # 降级兜底
  └─ finally: os.unlink(tmp_path)  # 确保清理
```

### `_docker_sandbox` —— Docker 容器执行

```
docker run --rm \                    # 跑完自动销毁
  --network=none \                   # 断网
  --memory=128m \                    # 物理内存上限
  --memory-swap=128m \               # swap 配额为 0，不可绕过
  --cpus=0.5 \                       # CPU 限制
  -v /tmp/xxx.py:/sandbox/code.py:ro \  # 代码文件只读挂载
  code-review-sandbox \
  python3 -W error /sandbox/code.py
```

### `_subprocess_sandbox` —— 降级方案

Docker 不可用时退回原 subprocess 方式，使用 `SANDBOX_TIMEOUT` 配置。

---

## 设计决策记录

### --read-only 不加

容器文件系统设为只读会拦截合法代码的文件写入（日志、输出结果、临时文件），造成大量误杀。当前威胁模型中，攻击者写入的文件受 namespace 隔离 + `--rm` 销毁 + `--network=none` 断网三重保护，无法外泄也无法持久化。收益为零，副作用明显。

### --memory-swap=128m 添加

`--memory=128m` 只限制物理内存，不限制 swap。测试证实 200MB bytearray 可成功分配（swap 接管）。加 `--memory-swap=128m` 使 swap 配额归零，超出物理内存立刻 OOM kill，内存限制真正生效。

### UID 检查而非用户名

容器内 `/etc/passwd` 无映射，`$USER` 环境变量可能为空。验证非 root 应检查 `os.getuid() != 0`，不检查用户名字符串。

---

## 测试结果（2026-05-20）

### sb_01 冒烟：9/9 ✅

| 检测项 | 结果 |
|--------|:--:|
| sandbox_executor 函数存在 | ✅ |
| _docker_sandbox 函数存在 | ✅ |
| _subprocess_sandbox 函数存在 | ✅ |
| Docker 沙箱：正常代码 | ✅ exit_code=0, passed=True |
| Docker 沙箱：代码报错 | ✅ exit_code=1, passed=False |
| Docker 沙箱：死循环超时 | ✅ exit_code=-1, stderr=执行超时 |
| coder_result 为空守卫 | ✅ exit_code=-1, stderr=修复代码为空 |
| 图集成：sandbox_executor 已注册 | ✅ |
| _docker_sandbox 返回 SandboxResult | ✅ |

### sb_02 降级：4/4 ✅

| 检测项 | 结果 |
|--------|:--:|
| subprocess 正常代码 | ✅ |
| subprocess 代码报错 | ✅ |
| subprocess 死循环超时 | ✅ |
| SANDBOX_TIMEOUT 配置生效 | ✅ |

### sb_03 安全特性：3/3 ✅

| 检测项 | 结果 |
|--------|:--:|
| network=none 网络隔离 | ✅ NETWORK_BLOCKED |
| 非 root 执行 (UID≠0) | ✅ UID=1000 |
| --memory-swap 内存限制 | ✅ 200MB 被 OOM kill |

### 全流程集成测试

`python scripts/run.py` 10 节点完整跑通，`sandbox_executor` 耗时 0.7s，`sandbox_passed=True`。

---

## 文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `sandbox/Dockerfile` | 新增 | 沙箱镜像定义 |
| `src/graph/nodes.py` | 修改 | 新增 `_docker_sandbox`、`_subprocess_sandbox`，重写 `sandbox_executor` |
| `tests/sandbox/sb_01_smoke.py` | 新增 | 冒烟测试 |
| `tests/sandbox/sb_02_fallback.py` | 新增 | 降级路径测试 |
| `tests/sandbox/sb_03_docker_verify.py` | 新增 | 安全特性验证 |
| `/etc/systemd/system/docker.service.d/http-proxy.conf` | 新增 | Docker 守护进程代理配置 |
