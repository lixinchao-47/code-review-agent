#!/bin/bash
# 构建 + 推送主应用和沙箱镜像到阿里云 ACR
set -e

ACR="crpi-g05pgblu4dg99gld.cn-shanghai.personal.cr.aliyuncs.com/lixinchao"

echo "=== 1/4 构建主应用镜像 ==="
docker build -t code-review-agent:latest .

echo ""
echo "=== 2/4 构建沙箱镜像 ==="
docker build -t code-review-sandbox:latest sandbox/

echo ""
echo "=== 3/4 打 tag ==="
docker tag code-review-agent:latest "${ACR}/code-review-agent:latest"
docker tag code-review-sandbox:latest "${ACR}/code-review-sandbox:latest"

echo ""
echo "=== 4/4 推送 ==="
docker push "${ACR}/code-review-agent:latest"
docker push "${ACR}/code-review-sandbox:latest"

echo ""
echo "=== 完成 ==="
echo "部署命令："
echo "  docker compose -f docker-compose.deploy.yml pull"
echo "  docker compose -f docker-compose.deploy.yml up -d"
