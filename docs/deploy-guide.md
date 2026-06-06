# 新电脑部署指南

新电脑不需要任何源码。镜像已由 GitHub Actions 自动构建并推送到阿里云 ACR，直接拉取运行即可。

---

## 方式一：docker compose（推荐，方便管理）

### 第 1 步：下载 compose 文件

打开终端（Ubuntu `Ctrl+Alt+T` / Mac `Cmd+空格 Terminal` / Windows `Win+R wsl`），输入：

```bash
mkdir -p ~/code-review-agent
cd ~/code-review-agent
wget https://raw.githubusercontent.com/lixinchao-47/code-review-agent/master/docker-compose.deploy.yml
```

### 第 2 步：确认 Docker 已安装

```bash
docker --version
```

如果提示 `command not found`：

```bash
curl -fsSL https://get.docker.com | sudo bash
sudo usermod -aG docker $USER
# 关掉终端重新打开
```

### 第 3 步：创建 sandbox 目录

```bash
mkdir -p sandbox-tmp
```

### 第 4 步：创建 .env 配置文件

```bash
nano .env
```

输入以下内容（把 key 换成你自己的）：

```
DEEPSEEK_API_KEY=sk-你的DeepSeek-API-Key
LLM_MODEL=deepseek-chat
MAX_RETRY=3
SANDBOX_TIMEOUT=10
LOG_LEVEL=INFO
```

> DeepSeek Key 获取：https://platform.deepseek.com → API Keys

保存：`Ctrl+X` → `Y` → 回车

### 第 5 步：登录阿里云 ACR

```bash
docker login crpi-g05pgblu4dg99gld.cn-shanghai.personal.cr.aliyuncs.com
```

- Username: `nick2803276058`
- Password: ACR 访问凭证密码

> 密码获取：阿里云控制台 → 容器镜像服务 → 访问凭证 → 设置固定密码

### 第 6 步：拉取镜像并启动

```bash
docker compose -f docker-compose.deploy.yml pull
docker compose -f docker-compose.deploy.yml up -d
```

### 第 7 步：打开浏览器

地址栏输入 `http://localhost:8501`

---

## 方式二：纯 docker run（零文件传输）

如果连 compose 文件都不想下载，一条命令搞定：

```bash
# 登录 ACR
docker login crpi-g05pgblu4dg99gld.cn-shanghai.personal.cr.aliyuncs.com

# 拉取沙箱镜像
docker pull crpi-g05pgblu4dg99gld.cn-shanghai.personal.cr.aliyuncs.com/lixinchao/code-review-sandbox:latest

# 创建 sandbox 目录
mkdir -p ~/code-review-agent/sandbox-tmp

# 启动（把 sk-xxx 替换成你的真实 Key）
docker run -d \
  --name code-review-agent \
  --restart unless-stopped \
  -p 8501:8501 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v ~/code-review-agent/sandbox-tmp:/var/sandbox \
  -e DEEPSEEK_API_KEY=sk-你的key \
  -e LLM_MODEL=deepseek-chat \
  -e MAX_RETRY=3 \
  -e SANDBOX_TIMEOUT=10 \
  -e SANDBOX_TMP_HOST=/tmp \
  crpi-g05pgblu4dg99gld.cn-shanghai.personal.cr.aliyuncs.com/lixinchao/code-review-agent:latest
```

浏览器打开 `http://localhost:8501`。

---

## 后续更新

开发机 push 代码到 GitHub → Actions 自动构建推 ACR。新电脑更新：

**方式一（compose）：**

```bash
cd ~/code-review-agent
docker compose -f docker-compose.deploy.yml pull
docker compose -f docker-compose.deploy.yml up -d
```

**方式二（docker run）：**

```bash
docker pull crpi-g05pgblu4dg99gld.cn-shanghai.personal.cr.aliyuncs.com/lixinchao/code-review-agent:latest
docker stop code-review-agent && docker rm code-review-agent
# 重新执行上面的 docker run 命令
```

---

## 常用管理命令

| 做什么 | compose 方式 | docker run 方式 |
|--------|-------------|----------------|
| 查看状态 | `docker compose -f docker-compose.deploy.yml ps` | `docker ps \| grep code-review` |
| 查看日志 | `docker compose -f docker-compose.deploy.yml logs --tail=50` | `docker logs code-review-agent --tail=50` |
| 实时日志 | `docker compose -f docker-compose.deploy.yml logs -f` | `docker logs -f code-review-agent` |
| 停止 | `docker compose -f docker-compose.deploy.yml down` | `docker stop code-review-agent` |
| 重启 | `docker compose -f docker-compose.deploy.yml restart` | `docker restart code-review-agent` |

---

## 常见问题

| 问题 | 原因 | 解决 |
|---|---|---|
| `wget` 404 | 仓库名或分支不对 | 浏览器打开 `https://github.com/lixinchao-47/code-review-agent` 确认存在 |
| `docker login` 失败 | 密码错 / 网络不通 | 阿里云控制台重置访问凭证 |
| 启动后访问不了 | 防火墙拦截 | `sudo ufw allow 8501` |
| 沙箱报"修复代码为空" | `docker.sock` 没挂载 | 确认命令里有 `-v /var/run/docker.sock:/var/run/docker.sock` |
