"""
安全审查员检测率测试 #01：模块级硬编码凭据（3 处 × 5 轮）

3 处硬编码凭据分散在模块不同位置，只满足条件 1（危险操作），不满足条件 2（无可信数据源）。
测试安全审查员是否正确识别"凭据本身即是漏洞"这一例外，稳定检出。

用法：python tests/optimization/security_reviewer/sec_01_sql_injection.py
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

# 3 处硬编码凭据，分散在模块不同位置
SAMPLE_CODE = """
import hashlib
import os

# 漏洞1：模块顶部硬编码密码
DB_PASSWORD = "admin123"

def hash_password(pw):
    return hashlib.md5(pw.encode()).hexdigest()

# 漏洞2：模块中部硬编码 API 密钥
API_KEY = "sk-proj-abc123def456ghi789"

def call_api(endpoint):
    import requests
    headers = {"Authorization": f"Bearer {API_KEY}"}
    return requests.get(endpoint, headers=headers)

# 漏洞3：模块底部硬编码 secret
SECRET = "my-secret-key-2024"
"""

KNOWN_VULNERABILITIES = 3
RUNS = 5

if __name__ == "__main__":
    print(f"=== 安全审查员检测率 #01：模块级硬编码凭据 ===")
    print(f"  LLM: {LLM_MODEL}")
    print(f"  已知漏洞: {KNOWN_VULNERABILITIES} 处（仅满足条件 1，不满足条件 2）")
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
