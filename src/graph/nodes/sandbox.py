"""沙箱执行节点 + Docker/subprocess 双通道"""

import os
import shutil
import subprocess
import tempfile

from config import SANDBOX_TIMEOUT, SANDBOX_IMAGE
from graph.state import AgentState
from models import SandboxResult


def _docker_sandbox(script_path: str) -> SandboxResult:
    """Docker 容器沙箱：network=none, memory=128m, non-root"""
    host_dir = os.environ.get('SANDBOX_TMP_HOST', '/tmp')
    filename = os.path.basename(script_path)
    host_path = os.path.join(host_dir, filename)
    try:
        result = subprocess.run(
            [
                'docker', 'run', '--rm',
                '--network=none',
                '--memory=128m',
                '--memory-swap=128m',
                '--cpus=0.5',
                '-v', f'{host_path}:/sandbox/code.py:ro',
                SANDBOX_IMAGE,
                'python3', '-W', 'error', '/sandbox/code.py',
            ],
            capture_output=True, text=True,
            timeout=SANDBOX_TIMEOUT,
        )
        return SandboxResult(
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            passed=(result.returncode == 0),
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(exit_code=-1, stdout='', stderr='执行超时', passed=False)


def _subprocess_sandbox(script_path: str) -> SandboxResult:
    """降级方案：subprocess 直接执行（Docker 不可用时使用）"""
    try:
        result = subprocess.run(
            ['python3', '-W', 'error', script_path],
            capture_output=True, text=True,
            timeout=SANDBOX_TIMEOUT,
        )
        return SandboxResult(
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            passed=(result.returncode == 0),
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(exit_code=-1, stdout='', stderr='执行超时', passed=False)


def sandbox_executor(state: AgentState) -> dict:
    """沙箱节点：执行修复后的代码，验证能否正常运行"""
    coder = state.get('coder_result')
    if coder is None:
        return {'sandbox_result': SandboxResult(exit_code=-1, stderr='修复代码为空', passed=False)}
    fixed_code = coder.fixed_code

    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False,
                                       dir='/var/sandbox' if os.path.isdir('/var/sandbox') else None) as f:
        f.write(fixed_code)
        tmp_path = f.name

    try:
        if shutil.which('docker'):
            sandbox = _docker_sandbox(tmp_path)
        else:
            sandbox = _subprocess_sandbox(tmp_path)
    finally:
        os.unlink(tmp_path)

    return {'sandbox_result': sandbox}
