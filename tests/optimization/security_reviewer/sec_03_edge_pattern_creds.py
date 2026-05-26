"""
安全审查员检测率测试 #03：边缘模式硬编码凭据（4 处 × 5 轮）

4 处硬编码凭据使用非常规命名（token/key/auth/cfg 等变体），
嵌入字典/配置对象中，测试安全审查员能否跨命名变体识别。

用法：python tests/optimization/security_reviewer/sec_03_edge_pattern_creds.py
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*allowed_objects.*")
import logging
logging.getLogger("langgraph.checkpoint.serde.jsonplus").setLevel(logging.ERROR)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC_DIR = str(PROJECT_ROOT / "src")
sys.path.insert(0, SRC_DIR)

from config import LLM_MODEL
from graph.nodes.reviewers import code_parser, security_reviewer
from graph.state import INITIAL_STATE

# 4 处硬编码凭据，使用非常规变量名/嵌套结构
SAMPLE_CODE = """
import requests

# 漏洞1：字典中的硬编码 token
DEFAULT_CONFIG = {
    "host": "api.example.com",
    "token": "xoxb-1234-5678-abcdef",
    "timeout": 30,
}

def get_client_auth():
    # 漏洞2：不同命名风格
    auth_token = "ghp_abc123def456ghi789"  # GitHub personal access token
    return {"Authorization": f"token {auth_token}"}

def upload_file(bucket):
    # 漏洞3：AK/SK 组合
    access_key = "AKIAIOSFODNN7EXAMPLE"
    secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    import boto3
    s3 = boto3.client(
        "s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_access_key,
    )

def init_app():
    # 漏洞4：嵌套在条件分支中
    import os
    if os.environ.get("ENV") == "production":
        # 应该在环境变量里读，但直接写死了
        DATABASE_URL = "postgresql://admin:prod_pass@10.0.0.1:5432/proddb"
"""

KNOWN_VULNERABILITIES = 4
RUNS = 5

if __name__ == "__main__":
    print(f"=== 安全审查员检测率 #03：边缘模式硬编码凭据 ===")
    print(f"  LLM: {LLM_MODEL}")
    print(f"  已知漏洞: {KNOWN_VULNERABILITIES} 处（非常规命名/嵌套结构）")
    print(f"  运行轮数: {RUNS}")
    print()

    detected_per_run = []

    for r in range(1, RUNS + 1):
        state = dict(INITIAL_STATE)
        state["original_code"] = SAMPLE_CODE

        parsed = code_parser(state)
        state.update(parsed)

        result = security_reviewer(state)
        review_results = result.get("review_results", [])

        issues = []
        for rr in review_results:
            issues.extend(rr.issues)

        detected = len(issues)
        detected_per_run.append(detected)

        print(f"  第 {r} 轮: 检出 {detected}/{KNOWN_VULNERABILITIES} 个")
        for issue in issues:
            print(f"    行 {issue.lineno} | {issue.severity.value} | {issue.category.value} | {issue.description[:80]}")

    print()
    print(f"=== 汇总 ===")
    avg = sum(detected_per_run) / len(detected_per_run)
    print(f"  平均检出: {avg:.1f}/{KNOWN_VULNERABILITIES}")
    print(f"  检出率: {avg / KNOWN_VULNERABILITIES * 100:.0f}%")
    print(f"  各轮检出: {detected_per_run}")
    all_detected = all(d == KNOWN_VULNERABILITIES for d in detected_per_run)
    print(f"  5 轮全检出: {'是' if all_detected else '否'}")
