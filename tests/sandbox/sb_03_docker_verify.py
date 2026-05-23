"""
沙箱测试 #03：验证 Docker 沙箱安全特性 —— network=none, non-root, memory-swap

用法：python tests/sandbox/sb_03_docker_verify.py
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
import subprocess
from config import SANDBOX_TIMEOUT

DOCKER_IMAGE = "code-review-sandbox"

# 与 _docker_sandbox 保持一致的 docker run 参数
DOCKER_BASE = [
    "docker", "run", "--rm",
    "--network=none",
    "--memory=128m",
    "--memory-swap=128m",
    "--cpus=0.5",
]

if __name__ == "__main__":
    print("=== 沙箱测试 #03：Docker 安全特性验证 ===")
    print()

    total = 0
    passed = 0

    # --- 网络隔离：network=none ---
    print("--- 网络隔离：network=none ---")
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False)
    tmp.write("""
import socket
try:
    s = socket.socket()
    s.settimeout(2)
    s.connect(('8.8.8.8', 53))
    print('NETWORK_OK')
except Exception as e:
    print('NETWORK_BLOCKED:' + type(e).__name__)
""".strip())
    tmp.close()
    try:
        result = subprocess.run(
            DOCKER_BASE + ["-v", f"{tmp.name}:/sandbox/code.py:ro", DOCKER_IMAGE,
                           "python3", "-W", "error", "/sandbox/code.py"],
            capture_output=True, text=True, timeout=SANDBOX_TIMEOUT,
        )
        ok = "NETWORK_BLOCKED" in result.stdout
        total += 1; passed += 1 if ok else 0
        print(f"  {'✅' if ok else '❌'} 容器内无法联网 (network=none 生效)")
        if not ok:
            print(f"    实际 stdout: {result.stdout!r}")
    finally:
        os.unlink(tmp.name)

    # --- 非 root 用户：UID≠0 ---
    print()
    print("--- 非 root 用户执行 ---")
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False)
    tmp.write("""
import os
print('UID:' + str(os.getuid()))
""".strip())
    tmp.close()
    try:
        result = subprocess.run(
            DOCKER_BASE + ["-v", f"{tmp.name}:/sandbox/code.py:ro", DOCKER_IMAGE,
                           "python3", "-W", "error", "/sandbox/code.py"],
            capture_output=True, text=True, timeout=SANDBOX_TIMEOUT,
        )
        ok = "UID:0" not in result.stdout
        total += 1; passed += 1 if ok else 0
        print(f"  {'✅' if ok else '❌'} 非 root 运行 (UID≠0)")
        if not ok:
            print(f"    实际 stdout: {result.stdout!r}")
    finally:
        os.unlink(tmp.name)

    # --- 内存限制：memory + swap 共计 128m ---
    print()
    print("--- 内存限制：memory+swap=128m ---")
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False)
    tmp.write("""
try:
    data = bytearray(200 * 1024 * 1024)  # 200MB
    print('MEMORY_OK')
except MemoryError:
    print('MEMORY_LIMITED')
""".strip())
    tmp.close()
    try:
        result = subprocess.run(
            DOCKER_BASE + ["-v", f"{tmp.name}:/sandbox/code.py:ro", DOCKER_IMAGE,
                           "python3", "-W", "error", "/sandbox/code.py"],
            capture_output=True, text=True, timeout=SANDBOX_TIMEOUT,
        )
        ok = "MEMORY_OK" not in result.stdout
        total += 1; passed += 1 if ok else 0
        print(f"  {'✅' if ok else '❌'} 超过 128m 内存被限制 (OOM kill 或 MemoryError)")
        if not ok:
            print(f"    实际 stdout: {result.stdout!r}")
    finally:
        os.unlink(tmp.name)

    # --- 报告 ---
    print()
    print("=== 最终审查报告 ===")
    print(f"  状态: {'success' if passed == total else 'failed'}")
    print(f"  检测项: {total}")
    print(f"  通过:   {passed}")
    print(f"  失败:   {total - passed}")
    sys.exit(0 if passed == total else 1)
