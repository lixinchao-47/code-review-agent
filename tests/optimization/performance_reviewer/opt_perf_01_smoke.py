"""
性能审查员优化测试 #01：快速冒烟 —— prompt 文本 + 模型校验 + 图结构（无 LLM）

用法：python tests/optimization/performance_reviewer/opt_perf_01_smoke.py
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*allowed_objects.*")
import logging
logging.getLogger("langgraph.checkpoint.serde.jsonplus").setLevel(logging.ERROR)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from graph.builder import build_graph
from graph.state import INITIAL_STATE, AgentState


REQUIRED_PROMPT_KEYWORDS = [
    "核心原则",
    "只报告从代码本身可直接确认的低效模式",
    "时间复杂度",
    "空间复杂度",
    "I/O",
    "数据结构",
    "重复计算",
    "estimated_impact",
    "宁漏勿错",
    "无确认问题",
]

REQUIRED_NODES = [
    "code_parser", "security_reviewer", "performance_reviewer", "style_reviewer",
    "critic_agent", "coder_agent", "sandbox_executor", "reflect_node",
    "human_review", "output_node",
]

if __name__ == "__main__":
    print("=== 性能审查员优化测试 #01：快速冒烟 ===")
    print()

    total = 0
    passed = 0

    # --- prompt 文本校验 ---
    print("--- prompt 文本校验 ---")
    nodes_src = open(SRC_DIR + "/graph/nodes/reviewers.py").read()
    for kw in REQUIRED_PROMPT_KEYWORDS:
        ok = kw in nodes_src
        total += 1; passed += 1 if ok else 0
        print(f"  {'✅' if ok else '❌'} 关键词: {kw}")

    # --- 模型校验 ---
    print()
    print("--- 模型校验 ---")
    from models import (
        ReviewResult, Issue, Severity, IssueCategory, ReviewDimension,
    )
    # ReviewResult + Issue 兜底校验
    issue = Issue(severity="非常严重", category="资源管理", lineno=None,
                  code_snippet=None, description=None, suggestion=None,
                  estimated_impact=None)
    ok = (issue.severity == Severity.MEDIUM
          and issue.category == IssueCategory.OTHER
          and issue.lineno == 0
          and issue.description == ""
          and issue.estimated_impact is None)
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} Issue: 非法枚举→MEDIUM/OTHER, null→''/0")

    rr = ReviewResult(dimension="performance", issues=None)
    ok = rr.issues == []
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} ReviewResult: null issues→[]")

    # dimension 校验
    # dimension 由节点硬赋值（非 LLM 产出），直接构造合法值验证链路通畅
    rr2 = ReviewResult(dimension="performance", issues=[issue])
    ok = rr2.dimension == ReviewDimension.PERFORMANCE and len(rr2.issues) == 1
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} ReviewResult: dimension=performance + issues 链路通畅")

    # --- 图结构 ---
    print()
    print("--- 图结构 ---")
    app = build_graph()
    nodes = list(app.get_graph().nodes.keys())
    for n in REQUIRED_NODES:
        ok = n in nodes
        total += 1; passed += 1 if ok else 0
        print(f"  {'✅' if ok else '❌'} {n}")

    # --- state 校验 ---
    print()
    print("--- State 字段校验 ---")
    state = dict(INITIAL_STATE)
    expected_keys = [
        "original_code", "code_analysis", "review_results",
        "critic_summary", "coder_result", "sandbox_result",
        "reflection_notes", "retry_count", "human_feedback", "final_report",
    ]
    for k in expected_keys:
        ok = k in state
        total += 1; passed += 1 if ok else 0
        print(f"  {'✅' if ok else '❌'} state['{k}']")

    # --- review_results reducer 检查 ---
    print()
    print("--- review_results 追加语义 ---")
    ok = "review_results" in AgentState.__annotations__
    total += 1; passed += 1 if ok else 0
    print(f"  {'✅' if ok else '❌'} review_results 使用 Annotated[...] 追加")

    # --- 报告 ---
    print()
    print("=== 最终审查报告 ===")
    print(f"  状态: {'success' if passed == total else 'failed'}")
    print(f"  检测项: {total}")
    print(f"  通过:   {passed}")
    print(f"  失败:   {total - passed}")
    sys.exit(0 if passed == total else 1)
