FROM python:3.12-slim

WORKDIR /app

# 阿里云镜像加速 —— apt 源
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources

# curl —— 下载 Docker CLI 静态二进制
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Docker CLI 静态二进制 —— sandbox_executor 用它在容器内调宿主机 Docker daemon
# 只需 CLI（~25MB），不需要整套 Docker 引擎（containerd/runc/iptables ~200MB）
ARG DOCKER_VERSION=29.1.3
RUN curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_VERSION}.tgz" | \
    tar xz --strip-components=1 -C /usr/local/bin docker/docker && \
    chmod +x /usr/local/bin/docker

# 先复制依赖清单 + 源码，利用 Docker 层缓存（依赖不变则跳过重装）
COPY pyproject.toml .
COPY src/ ./src/

RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ . && \
    pip cache purge

# 复制应用层代码（变更频率高于依赖）
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY docker-entrypoint.sh /

RUN chmod +x /docker-entrypoint.sh

ENV PYTHONPATH=/app/src
ENV STREAMLIT_MODE=true

EXPOSE 8501

ENTRYPOINT ["/docker-entrypoint.sh"]
