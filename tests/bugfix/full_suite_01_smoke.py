"""
全量测试 #01：快速冒烟 —— 路由函数 + 模型校验 + 图结构（无 LLM）

覆盖：B00/B04/B05 所有不依赖 LLM 的检测项

用法：python tests/bugfix/full_suite_01_smoke.py
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*allowed_objects.*")
import logging
logging.getLogger("langgraph.checkpoint.serde.jsonplus").setLevel(logging.ERROR)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from config import MAX_RETRY
from graph.builder import build_graph, retry_or_human, should_continue_or_output
from graph.state import INITIAL_STATE


def build_state(**kwargs):
    s = dict(INITIAL_STATE)
    s.update(kwargs)
    return s


if __name__ == "__main__":
    print("=== 代码审查 Agent 全量测试 #01：快速冒烟 ===")
    print(f"  MAX_RETRY: {MAX_RETRY}")
    print()

    total = 0
    passed = 0

    # --- B04 routing ---
    print("--- [B04] retry_or_human 路由 ---")
    for rc in range(MAX_RETRY):
        result = retry_or_human(build_state(retry_count=rc))
        ok = result == "coder_agent"
        total += 1; passed += 1 if ok else 0
        print(f"  {'✅' if ok else '❌'} retry_count={rc} → {result}")
    for rc in [MAX_RETRY, MAX_RETRY + 1]:
        result = retry_or_human(build_state(retry_count=rc))
        ok = result == "human_review"
        total += 1; passed += 1 if ok else 0
        print(f"  {'✅' if ok else '❌'} retry_count={rc} → {result}")

    print()
    print("--- [B04] should_continue_or_output 路由 ---")
    result = should_continue_or_output(build_state(human_feedback=""))
    ok = result == "output_node"
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} feedback='' → {result}")
    for fb in ["修一下", "use subprocess.run"]:
        result = should_continue_or_output(build_state(human_feedback=fb))
        ok = result == "coder_agent"
        total += 1; passed += 1 if ok else 0
        print(f"  {'✅' if ok else '❌'} feedback='{fb}' → {result}")

    # --- B00 validators ---
    print()
    print("--- [B00] field_validator 兜底 ---")
    from models import (
        FunctionInfo, ClassInfo, Issue, ReviewResult,
        ActionItem, CriticSummary, ChangeItem, CoderResult, ReflectionResult,
        Severity, IssueCategory, FailureType,
    )

    fi = FunctionInfo(name=None, lineno=None)
    ok = fi.name == "" and fi.lineno == 0
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} FunctionInfo: null name→'{fi.name}', lineno→{fi.lineno}")

    ci = ClassInfo(name=None, lineno=None)
    ok = ci.name == "" and ci.lineno == 0
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} ClassInfo: null name→'{ci.name}', lineno→{ci.lineno}")

    issue = Issue(severity="非常严重", category="资源管理", lineno=None,
                  code_snippet=None, description=None, suggestion=None)
    ok = (issue.severity == Severity.MEDIUM and issue.category == IssueCategory.OTHER
          and issue.lineno == 0 and issue.description == "" and issue.suggestion == "")
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} Issue: illegal enum→MEDIUM/OTHER, null→''/0")

    rr = ReviewResult(dimension="security", issues=None)
    ok = rr.issues == []
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} ReviewResult: null issues→[]")

    ai = ActionItem(priority=None, description=None, lineno=None,
                    severity="critical", category="注入", fix_instruction=None)
    ok = ai.priority == 0 and ai.description == "" and ai.lineno == 0 and ai.fix_instruction == ""
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} ActionItem: null→0/''")

    cs = CriticSummary(score_before=None, total_issues=None, action_plan=[], summary="")
    ok = cs.score_before == 0 and cs.total_issues == 0
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} CriticSummary: null score/total→0")

    ci2 = ChangeItem(lineno=None, original=None, fixed=None, reason=None)
    ok = ci2.lineno == 0 and ci2.original == "" and ci2.fixed == "" and ci2.reason == ""
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} ChangeItem: null→0/''")

    cr = CoderResult(fixed_code=None, changes=None, skipped_items=None)
    ok = cr.fixed_code == "" and cr.changes == [] and cr.skipped_items == []
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} CoderResult: null fixed_code→'', lists→[]")

    ref = ReflectionResult(failure_type="syntax_error", root_cause=None, new_strategy=None)
    ok = ref.root_cause == "" and ref.new_strategy == ""
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} ReflectionResult: null strings→''")

    ref2 = ReflectionResult(failure_type="不知道", root_cause="x", new_strategy="y")
    ok = ref2.failure_type == FailureType.LOGIC_ERROR
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} ReflectionResult: illegal type→LOGIC_ERROR")

    # --- graph structure ---
    print()
    print("--- [B04] 图结构 ---")
    app = build_graph()
    nodes = list(app.get_graph().nodes.keys())
    for n in ["code_parser", "security_reviewer", "performance_reviewer", "style_reviewer",
              "critic_agent", "coder_agent", "sandbox_executor", "reflect_node",
              "human_review", "output_node"]:
        ok = n in nodes
        total += 1; passed += 1 if ok else 0
        print(f"  {'✅' if ok else '❌'} {n}")

    # --- B05 ---
    print()
    print("--- [B05] sandbox -W error ---")
    ok = "'python3', '-W', 'error'" in open(SRC_DIR + "/graph/nodes/sandbox.py").read()
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} sandbox_executor 使用 -W error")

    # --- report ---
    print()
    print("=== 最终审查报告 ===")
    print(f"  状态: {'success' if passed == total else 'failed'}")
    print(f"  检测项: {total}")
    print(f"  通过:   {passed}")
    print(f"  失败:   {total - passed}")
    sys.exit(0 if passed == total else 1)
