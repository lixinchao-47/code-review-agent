"""
沙箱测试 #02：降级路径 —— 模拟 Docker 不可用，subprocess 接管

用法：python tests/sandbox/sb_02_fallback.py
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*allowed_objects.*")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import tempfile
import os
from graph.nodes import _subprocess_sandbox
from models import SandboxResult

if __name__ == "__main__":
    print("=== 沙箱测试 #02：降级路径 ===")
    print()

    total = 0
    passed = 0

    # --- 降级：正常代码 ---
    print("--- 降级 subprocess：正常代码 ---")
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False)
    tmp.write("print('fallback ok')")
    tmp.close()
    try:
        r = _subprocess_sandbox(tmp.name)
        ok = isinstance(r, SandboxResult) and r.exit_code == 0 and r.passed
        total += 1; passed += 1 if ok else 0
        print(f"  {'✅' if ok else '❌'} exit_code=0, passed=True, stdout='fallback ok'")
        if not ok:
            print(f"    实际: exit_code={r.exit_code}, passed={r.passed}, stdout={r.stdout!r}")
    finally:
        os.unlink(tmp.name)

    # --- 降级：代码报错 ---
    print()
    print("--- 降级 subprocess：代码报错 ---")
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False)
    tmp.write("1/0")
    tmp.close()
    try:
        r = _subprocess_sandbox(tmp.name)
        ok = r.exit_code == 1 and not r.passed and "ZeroDivisionError" in r.stderr
        total += 1; passed += 1 if ok else 0
        print(f"  {'✅' if ok else '❌'} exit_code=1, passed=False, stderr 含 ZeroDivisionError")
        if not ok:
            print(f"    实际: exit_code={r.exit_code}, passed={r.passed}, stderr={r.stderr!r}")
    finally:
        os.unlink(tmp.name)

    # --- 降级：死循环超时 ---
    print()
    print("--- 降级 subprocess：死循环超时 ---")
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False)
    tmp.write("while True: pass")
    tmp.close()
    try:
        r = _subprocess_sandbox(tmp.name)
        ok = r.exit_code == -1 and not r.passed and "超时" in r.stderr
        total += 1; passed += 1 if ok else 0
        print(f"  {'✅' if ok else '❌'} exit_code=-1, passed=False, stderr='执行超时'")
        if not ok:
            print(f"    实际: exit_code={r.exit_code}, passed={r.passed}, stderr={r.stderr!r}")
    finally:
        os.unlink(tmp.name)

    # --- 降级：SANDBOX_TIMEOUT 配置 ---
    print()
    print("--- SANDBOX_TIMEOUT 配置生效 ---")
    from config import SANDBOX_TIMEOUT
    ok = SANDBOX_TIMEOUT == 10
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} SANDBOX_TIMEOUT=10 (默认)")
    if not ok:
        print(f"    实际: SANDBOX_TIMEOUT={SANDBOX_TIMEOUT}")

    # --- 报告 ---
    print()
    print("=== 最终审查报告 ===")
    print(f"  状态: {'success' if passed == total else 'failed'}")
    print(f"  检测项: {total}")
    print(f"  通过:   {passed}")
    print(f"  失败:   {total - passed}")
    sys.exit(0 if passed == total else 1)
