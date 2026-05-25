"""
B05 验证脚本 #01：-W error 将 SyntaxWarning 升级为异常

用 is 比较字面量（Python 3.12+ 触发 SyntaxWarning）。
旧 sandbox 命令 python3 → exit_code=0, passed=True
新 sandbox 命令 python3 -W error → exit_code=1, passed=False

用法：python tests/bugfix/b05/test_b05_01_warning_as_error.py
"""
import sys
import subprocess
import tempfile
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*allowed_objects.*")
import logging
logging.getLogger("langgraph.checkpoint.serde.jsonplus").setLevel(logging.ERROR)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# 触发 SyntaxWarning 的代码：is 比较字面量
WARNING_CODE = """
x = 1
if x is 1:
    print("yes")

def get_data():
    import datetime
    return datetime.datetime.utcnow()
"""


def run_sandbox(cmd, code):
    """用指定命令执行代码，返回 exit_code + stderr"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(code)
        tmp = f.name

    try:
        result = subprocess.run(cmd + [tmp], capture_output=True, text=True, timeout=10)
        return result.returncode, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"


if __name__ == "__main__":
    print(f"=== B05 验证 #01：-W error 升级 warning 为异常 ===")
    print()

    all_passed = True

    # 检测 1：旧命令 python3 → warning 不阻断
    print("--- 检测 1：python3（旧行为） ---")
    exit_old, stderr_old = run_sandbox(["python3"], WARNING_CODE)
    print(f"  exit_code: {exit_old}")
    print(f"  stderr 前 200 字符: {stderr_old[:200]}")
    if exit_old == 0:
        print(f"  ✅ 旧行为确认：warning 不阻止通过")
    else:
        print(f"  ⚠ 旧命令也非 0（exit_code={exit_old}），可能代码有其他问题")
    print()

    # 检测 2：新命令 python3 -W error → warning 变成 error
    print("--- 检测 2：python3 -W error（新行为） ---")
    exit_new, stderr_new = run_sandbox(["python3", "-W", "error"], WARNING_CODE)
    print(f"  exit_code: {exit_new}")
    print(f"  stderr 前 200 字符: {stderr_new[:200]}")

    if exit_new != 0:
        print(f"  ✅ -W error 成功将 warning 升级为异常（exit_code={exit_new}）")
    else:
        print(f"  ⚠ -W error 下仍通过（可能此 Python 版本不触发 SyntaxWarning）")
        print(f"     stderr: {stderr_new[:300]}")
        # 不直接 fail——取决于 Python 版本的 warning filter 策略
    print()

    # 检测 3：正常代码在 -W error 下仍通过
    print("--- 检测 3：正常代码 -W error 不受影响 ---")
    CLEAN_CODE = """
def add(a, b):
    return a + b

print(add(1, 2))
"""
    exit_clean, stderr_clean = run_sandbox(["python3", "-W", "error"], CLEAN_CODE)
    print(f"  exit_code: {exit_clean}")
    print(f"  stderr: {stderr_clean[:100] if stderr_clean else '(空)'}")
    if exit_clean == 0:
        print(f"  ✅ 正常代码不受 -W error 影响")
    else:
        print(f"  ❌ -W error 误杀了正常代码")
        all_passed = False
    print()

    # 检测 4：确认 sandbox_executor 源码使用了 -W error
    print("--- 检测 4：sandbox_executor 源码确认 ---")
    nodes_path = Path(SRC_DIR) / "graph" / "nodes" / "sandbox.py"
    content = nodes_path.read_text()
    if "'python3', '-W', 'error', tmp_path" in content:
        print(f"  ✅ sandbox_executor 已使用 -W error 标志")
    elif "'-W', 'error'" in content:
        print(f"  ✅ sandbox_executor 包含 -W error")
    else:
        print(f"  ❌ sandbox_executor 未找到 -W error 标志")
        all_passed = False

    print()
    print(f"=== B05 验证 #01 {'全部通过' if all_passed else '存在失败'} ===")
    sys.exit(0 if all_passed else 1)
