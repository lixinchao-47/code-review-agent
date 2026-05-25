"""
沙箱测试 #01：快速冒烟 —— Docker 沙箱正常执行 + 图集成

用法：python tests/sandbox/sb_01_smoke.py
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*allowed_objects.*")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from graph.builder import build_graph
from graph.nodes import sandbox_executor
from graph.nodes.sandbox import _docker_sandbox, _subprocess_sandbox
from models import SandboxResult

if __name__ == "__main__":
    print("=== 沙箱测试 #01：快速冒烟 ===")
    print()

    total = 0
    passed = 0

    # --- 函数存在性 ---
    print("--- 函数存在性 ---")
    for fn_name in ["sandbox_executor", "_docker_sandbox", "_subprocess_sandbox"]:
        ok = fn_name in dir()
        total += 1; passed += 1 if ok else 0
        print(f"  {'✅' if ok else '❌'} {fn_name} 存在")

    # --- Docker 沙箱：正常代码 ---
    print()
    print("--- Docker 沙箱：正常代码 ---")
    state = {
        "coder_result": type("obj", (object,), {
            "fixed_code": "print('hello sandbox')"
        })()
    }
    r = sandbox_executor(state)["sandbox_result"]
    ok = r.exit_code == 0 and r.passed and "hello sandbox" in r.stdout
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} exit_code=0, passed=True, stdout='hello sandbox'")
    if not ok:
        print(f"    实际: exit_code={r.exit_code}, passed={r.passed}, stdout={r.stdout!r}")

    # --- Docker 沙箱：代码报错 ---
    print()
    print("--- Docker 沙箱：代码报错 ---")
    state = {
        "coder_result": type("obj", (object,), {
            "fixed_code": "raise ValueError('test error')"
        })()
    }
    r = sandbox_executor(state)["sandbox_result"]
    ok = r.exit_code == 1 and not r.passed and "ValueError" in r.stderr
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} exit_code=1, passed=False, stderr 含 ValueError")
    if not ok:
        print(f"    实际: exit_code={r.exit_code}, passed={r.passed}, stderr={r.stderr!r}")

    # --- Docker 沙箱：死循环超时 ---
    print()
    print("--- Docker 沙箱：死循环超时 ---")
    state = {
        "coder_result": type("obj", (object,), {
            "fixed_code": "while True: pass"
        })()
    }
    r = sandbox_executor(state)["sandbox_result"]
    ok = r.exit_code == -1 and not r.passed and "超时" in r.stderr
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} exit_code=-1, passed=False, stderr='执行超时'")
    if not ok:
        print(f"    实际: exit_code={r.exit_code}, passed={r.passed}, stderr={r.stderr!r}")

    # --- coder_result 为 None ---
    print()
    print("--- coder_result 为空守卫 ---")
    r = sandbox_executor({})["sandbox_result"]
    ok = r.exit_code == -1 and not r.passed and "修复代码为空" in r.stderr
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} exit_code=-1, passed=False, stderr='修复代码为空'")
    if not ok:
        print(f"    实际: exit_code={r.exit_code}, passed={r.passed}, stderr={r.stderr!r}")

    # --- 图集成：sandbox_executor 节点已注册 ---
    print()
    print("--- 图集成 ---")
    app = build_graph()
    nodes = list(app.get_graph().nodes.keys())
    ok = "sandbox_executor" in nodes
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} sandbox_executor 已注册到图中")

    # --- _docker_sandbox 返回类型 ---
    print()
    print("--- _docker_sandbox 返回类型 ---")
    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False)
    tmp.write("x = 1")
    tmp.close()
    try:
        r = _docker_sandbox(tmp.name)
        ok = isinstance(r, SandboxResult) and r.passed
        total += 1; passed += 1 if ok else 0
        print(f"  {'✅' if ok else '❌'} 返回 SandboxResult, passed=True")
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
