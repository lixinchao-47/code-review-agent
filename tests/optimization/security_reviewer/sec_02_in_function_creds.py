"""
安全审查员检测率测试 #02：函数内硬编码凭据（4 处 × 5 轮）

4 处硬编码凭据分散在函数/类内部，与正常业务代码混杂。
测试安全审查员在函数作用域内能否同样识别硬编码凭据。

用法：python tests/optimization/security_reviewer/sec_02_in_function_creds.py
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

# 4 处硬编码凭据，分散在不同函数/类中
SAMPLE_CODE = """
import smtplib
import base64

def send_email(to_addr, subject, body):
    # 漏洞1：函数内硬编码 SMTP 密码
    smtp_user = "admin@company.com"
    smtp_password = "EmailP@ssw0rd"
    server = smtplib.SMTP("smtp.company.com", 587)
    server.login(smtp_user, smtp_password)
    server.sendmail(smtp_user, to_addr, f"Subject: {subject}\\n\\n{body}")
    server.quit()

class PaymentService:
    def process_payment(self, amount):
        # 漏洞2：类方法内硬编码支付密钥
        stripe_key = "sk_live_abc123def456"
        # 正常业务逻辑
        return {"status": "paid", "amount": amount}

def decode_token(encoded):
    # 漏洞3：函数内硬编码 JWT secret
    jwt_secret = "super-secret-jwt-key-2024"
    import jwt
    return jwt.decode(encoded, jwt_secret, algorithms=["HS256"])

def connect_database():
    # 漏洞4：函数内硬编码数据库连接字符串
    conn_str = "mysql://root:password123@localhost:3306/mydb"
    import pymysql
    return pymysql.connect(conn_str)
"""

KNOWN_VULNERABILITIES = 4
RUNS = 5

if __name__ == "__main__":
    print(f"=== 安全审查员检测率 #02：函数内硬编码凭据 ===")
    print(f"  LLM: {LLM_MODEL}")
    print(f"  已知漏洞: {KNOWN_VULNERABILITIES} 处（分散在函数/类方法中）")
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
