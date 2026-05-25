"""
Streamlit 前端 —— 代码审查与自动修复系统可视化界面

用法：streamlit run app/streamlit_app.py
"""
import sys
import asyncio
import time
import threading
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*allowed_objects.*")
import logging
logging.getLogger("langgraph.checkpoint.serde.jsonplus").setLevel(logging.ERROR)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = str(PROJECT_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import streamlit as st

from config import LLM_MODEL, MAX_RETRY
from graph.builder import build_graph
from graph.state import INITIAL_STATE, AgentState

# ============================================================
# 页面配置
# ============================================================
st.set_page_config(
    page_title="代码审查 Agent",
    page_icon="",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ============================================================
# 样式
# ============================================================
st.markdown("""
<style>
    .severity-critical { background-color: #dc3545; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; font-weight: bold; }
    .severity-high { background-color: #fd7e14; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; font-weight: bold; }
    .severity-medium { background-color: #ffc107; color: black; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; font-weight: bold; }
    .severity-low { background-color: #0dcaf0; color: black; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; font-weight: bold; }
    .status-success { color: #198754; font-weight: bold; }
    .status-failed { color: #dc3545; font-weight: bold; }
    .status-partial { color: #fd7e14; font-weight: bold; }
    .score-box { text-align: center; padding: 20px; border-radius: 12px; margin: 10px 0; }
    .score-good { background-color: #d1e7dd; }
    .score-ok { background-color: #fff3cd; }
    .score-bad { background-color: #f8d7da; }
</style>
""", unsafe_allow_html=True)

# ============================================================
# 预置示例代码
# ============================================================
SAMPLES = {
    "SQL 注入 + 硬编码密码": '''
DB_PASSWORD = "admin123"

def get_users(filter_role=None):
    query = "SELECT * FROM users"
    if filter_role:
        query += " WHERE role = '%s'" % filter_role
    import sqlite3
    conn = sqlite3.connect("app.db")
    return conn.execute(query).fetchall()
''',
    "eval + exec 双重隐患": '''
def calculate(expression, x):
    return eval(expression)

def run_script(code_str):
    exec(code_str)

def load_config(data):
    import pickle
    return pickle.loads(data)
''',
    "循环内字符串拼接 + 低效数据结构": '''
def build_report(users):
    result = ""
    for u in users:
        result += u["name"] + "," + u["email"] + "\\n"
    return result

def find_duplicates(items):
    seen = []
    duplicates = []
    for item in items:
        if item in seen:
            duplicates.append(item)
        else:
            seen.append(item)
    return duplicates
''',
    "bare except + 无类型注解": '''
def read_config(path):
    try:
        with open(path) as f:
            return f.read()
    except:
        return None

def process(data, options):
    tmp = data.copy()
    tmp.update(options)
    return sorted(tmp.items())
''',
}

# ============================================================
# Session State 初始化
# ============================================================
DEFAULT_SESSION = {
    "app": None,
    "thread_id": None,
    "config": None,
    "review_state": "idle",
    "original_code": "",
    "final_report": None,
    "error": None,
    "history": [],
    # 流式执行相关
    "exec_thread": None,       # 后台执行线程
    "stream_progress": None,   # 线程间共享的进度 dict
    "stream_final_state": None,
    "stream_hit_interrupt": False,
    "stream_error": None,
    "show_feedback_input": False,
}

for key, default in DEFAULT_SESSION.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ============================================================
# 核心：后台线程 + 异步流式执行
# ============================================================
ALL_NODES = [
    "code_parser", "security_reviewer", "performance_reviewer",
    "style_reviewer", "critic_agent", "coder_agent",
    "sandbox_executor", "reflect_node", "human_review", "output_node",
]


def _run_graph_async(app, initial_state, config, progress):
    """在后台线程中执行 astream_events，实时写入 progress 供前端轮询。
    app 必须作为参数传入，不能在后台线程里读 st.session_state。"""
    current_state = dict(initial_state) if initial_state else {}
    node_start = {}

    async def _stream():
        nonlocal current_state
        last_state = current_state
        async for event in app.astream_events(initial_state, config, version="v2"):
            kind = event["event"]
            name = event.get("name", "")

            if kind == "on_chain_start" and name in ALL_NODES:
                node_start[name] = time.time()
                progress["node_status"][name] = "running"

            elif kind == "on_chain_end" and name in node_start:
                elapsed = time.time() - node_start.pop(name, 0)
                progress["node_times"][name] = round(elapsed, 1)
                progress["node_status"][name] = "done"

                output = event["data"].get("output", {})
                if isinstance(output, dict):
                    for k, v in output.items():
                        if k in AgentState.__annotations__:
                            current_state[k] = v
                last_state = current_state

        return last_state

    try:
        final_state = asyncio.run(_stream())
        snapshot = app.get_state(config)
        hit_interrupt = bool(snapshot.next and "human_review" in str(snapshot.next))
        progress["final_state"] = final_state
        progress["hit_interrupt"] = hit_interrupt
    except Exception as e:
        progress["error"] = str(e)
    finally:
        progress["done"] = True


# ============================================================
# UI：结果展示
# ============================================================
def severity_badge(sev):
    label = sev.value if hasattr(sev, 'value') else str(sev)
    m = {"critical": "severity-critical", "high": "severity-high",
         "medium": "severity-medium", "low": "severity-low"}
    css = m.get(label.lower(), "")
    return f'<span class="{css}">{label}</span>'


def render_issues(issues):
    if not issues:
        st.info("未发现问题")
        return
    for item in issues:
        sev = item.severity.value if hasattr(item.severity, 'value') else str(item.severity)
        cat = item.category.value if hasattr(item.category, 'value') else str(item.category)
        with st.container():
            col1, col2 = st.columns([1, 11])
            with col1:
                st.markdown(severity_badge(sev), unsafe_allow_html=True)
            with col2:
                fix = getattr(item, 'fix_instruction', '') or ''
                st.markdown(
                    f"**行 {item.lineno}** | {cat} | {item.description}"
                    + (f"\n\n```\n{fix}\n```" if fix else "")
                )
            st.divider()


def render_code_diff(original, fixed):
    if not fixed:
        st.info("未生成修复代码")
        return
    import difflib
    diff = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        fixed.splitlines(keepends=True),
        fromfile='原始代码', tofile='修复后代码', n=3,
    ))
    if diff:
        st.code("".join(diff), language="diff", line_numbers=False)
    col1, col2 = st.columns(2)
    with col1:
        st.caption("原始代码")
        st.code(original, language="python", line_numbers=True)
    with col2:
        st.caption("修复后代码")
        st.code(fixed, language="python", line_numbers=True)


def render_notes(notes: str):
    """展示审查警告（如作用域变更检测）"""
    if not notes:
        return
    st.warning(f"**【警告】**\n\n{notes}")


def render_skipped_items(items):
    if not items:
        return
    st.warning(f"**{len(items)} 条需人工介入**")
    for item in items:
        st.markdown(f"- {item}")


def render_sandbox_result(result):
    if result is None:
        st.info("未执行沙箱验证")
        return
    if result.passed:
        st.success(f"沙箱验证通过 (exit_code={result.exit_code})")
    else:
        st.error(f"沙箱验证失败 (exit_code={result.exit_code})")
    if result.stdout:
        with st.expander("stdout"):
            st.code(result.stdout, language="text")
    if result.stderr:
        with st.expander("stderr"):
            st.code(result.stderr, language="text")


def render_score_box(score, label):
    if score >= 80:
        css = "score-good"
    elif score >= 60:
        css = "score-ok"
    else:
        css = "score-bad"
    st.markdown(f"""
    <div class="score-box {css}">
        <div style="font-size: 2em; font-weight: bold;">{score}</div>
        <div style="color: #6c757d;">{label}</div>
    </div>
    """, unsafe_allow_html=True)


def _node_line(node, label, node_status, node_times):
    """单个节点的状态行"""
    status = node_status.get(node)
    if status == "done":
        elapsed = node_times.get(node, "")
        t = f"({elapsed}s)" if elapsed else ""
        return f"✅ {label} {t}"
    elif status == "running":
        return f"⏳ **{label}** 执行中..."
    else:
        return f"⬜ {label}"


def render_node_progress(node_status, node_times):
    # 第一阶段：代码解析
    st.markdown(_node_line("code_parser", "代码解析", node_status, node_times))

    # 第二阶段：三路审查并行
    reviewer_any_started = any(
        node_status.get(r) is not None
        for r in ("security_reviewer", "performance_reviewer", "style_reviewer")
    )
    if reviewer_any_started:
        st.caption("三路并行审查")
        rc1, rc2, rc3 = st.columns(3)
        with rc1:
            st.markdown(
                _node_line("security_reviewer", "安全审查", node_status, node_times))
        with rc2:
            st.markdown(
                _node_line("performance_reviewer", "性能审查", node_status, node_times))
        with rc3:
            st.markdown(
                _node_line("style_reviewer", "风格审查", node_status, node_times))

    # 第三阶段：汇总 → 修复 → 沙箱
    st.markdown(_node_line("critic_agent", "汇总评判", node_status, node_times))
    st.markdown(_node_line("coder_agent", "自动修复", node_status, node_times))
    st.markdown(_node_line("sandbox_executor", "沙箱验证", node_status, node_times))

    # 第四阶段：条件节点（失败分析 / 人工确认 / 输出）
    for node, label in [
        ("reflect_node", "失败分析"),
        ("human_review", "人工确认"),
        ("output_node", "生成报告"),
    ]:
        status = node_status.get(node)
        if status is not None:
            st.markdown(_node_line(node, label, node_status, node_times))


# ============================================================
# UI：主布局
# ============================================================

def main():
    st.title("代码审查 Agent")
    st.caption(f"LLM: {LLM_MODEL} | 最多重试: {MAX_RETRY} 次")

    left, right = st.columns([1, 2])

    # ---- 左侧：输入区 ----
    with left:
        st.subheader("输入代码")

        sample_name = st.selectbox(
            "快速加载示例", ["(自定义)"] + list(SAMPLES.keys()),
            key="sample_select",
        )
        if sample_name != "(自定义)":
            st.session_state.original_code = SAMPLES[sample_name]
            st.session_state.code_input_area = SAMPLES[sample_name]

        code_input = st.text_area(
            "粘贴待审查的 Python 代码",
            value=st.session_state.original_code,
            height=300,
            key="code_input_area",
            label_visibility="collapsed",
        )
        st.session_state.original_code = code_input

        c1, c2 = st.columns(2)
        with c1:
            start_clicked = st.button(
                "开始审查", type="primary", use_container_width=True,
                disabled=(st.session_state.review_state != "idle"),
            )
        with c2:
            if st.button("清空", use_container_width=True):
                for key in DEFAULT_SESSION:
                    if key != "app":
                        st.session_state[key] = DEFAULT_SESSION[key]
                st.rerun()

        if st.session_state.history:
            st.divider()
            st.caption("审查历史")
            for h in st.session_state.history[-10:]:
                icon = {"success": "✅", "partial": "⚠️", "failed": "❌"}.get(
                    h.get("status", ""), "❓")
                st.caption(f"{icon} {h['score_after']}分 | {h.get('issues', '?')}个问题")

        if st.session_state.review_state != "idle":
            st.divider()
            st.error("**开始一次全新的审查（重置当前状态）**")
            if st.button("开始新审查", key="reset_review", use_container_width=True, type="primary"):
                for key in DEFAULT_SESSION:
                    if key != "app":
                        st.session_state[key] = DEFAULT_SESSION[key]
                st.rerun()

    # ---- 右侧：结果展示区 ----
    with right:
        if st.session_state.error:
            st.error(st.session_state.error)
            st.session_state.error = None

        # ============ idle ============
        if st.session_state.review_state == "idle":
            st.info("在左侧粘贴 Python 代码，点击「开始审查」启动分析")
            st.markdown("""
            **功能说明**
            - 三路并行审查：安全 / 性能 / 风格
            - 自动修复 + Docker 沙箱隔离验证
            - 失败自动反思重试（最多 3 次）
            - 修复完成后需人工确认
            """)

        # ============ running ============
        elif st.session_state.review_state == "running":
            progress = st.session_state.stream_progress
            if progress is None:
                st.error("执行状态丢失")
                st.session_state.review_state = "idle"
                st.rerun()

            st.subheader(f"执行中... {time.time() - st.session_state.poll_start:.1f}s")
            render_node_progress(progress.get("node_status", {}),
                                 progress.get("node_times", {}))

            if progress.get("done"):
                # 后台线程执行完毕
                if progress.get("error"):
                    st.session_state.error = f"审查失败: {progress['error']}"
                    st.session_state.review_state = "idle"
                elif progress.get("hit_interrupt"):
                    st.session_state.review_state = "waiting_human"
                else:
                    final_state = progress.get("final_state", {})
                    report = final_state.get("final_report")
                    st.session_state.final_report = report
                    st.session_state.review_state = "done"
                    if report:
                        st.session_state.history.append({
                            "code": st.session_state.original_code[:80],
                            "status": report.status,
                            "score_after": report.score_after,
                            "issues": len(report.action_items) if report.action_items else 0,
                        })
                st.rerun()
            else:
                # 还没执行完，0.5 秒后自动刷新进度；超过 180s 超时
                elapsed = time.time() - st.session_state.get("poll_start", time.time())
                if elapsed > 180:
                    st.session_state.error = "审查超时，请重试"
                    st.session_state.review_state = "idle"
                    st.rerun()
                time.sleep(0.1)
                st.rerun()

        # ============ waiting_human ============
        elif st.session_state.review_state == "waiting_human":
            st.subheader("人工确认")

            progress = st.session_state.stream_progress
            with st.expander("执行进度", expanded=False):
                if progress:
                    render_node_progress(progress.get("node_status", {}),
                                         progress.get("node_times", {}))

            snapshot = st.session_state.app.get_state(st.session_state.config)
            state_vals = snapshot.values if snapshot else {}
            sandbox = state_vals.get("sandbox_result")
            retry = state_vals.get("retry_count", 0)
            coder = state_vals.get("coder_result")

            elapsed = time.time() - st.session_state.get("review_start_time", time.time())
            st.caption(f"已耗时 {elapsed:.1f}s  |  重试 {retry} 次")

            st.divider()
            if sandbox:
                render_sandbox_result(sandbox)

            if coder and coder.changes:
                with st.expander(f"修改记录（{len(coder.changes)} 处）", expanded=True):
                    for ch in coder.changes:
                        st.caption(f"行 {ch.lineno}: {ch.reason}")
            if coder and coder.skipped_items:
                render_skipped_items(coder.skipped_items)
            if coder and coder.notes:
                render_notes(coder.notes)

            st.divider()
            st.markdown("**请选择操作：**")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("确认修复结果", use_container_width=True, type="primary"):
                    _continue_from_human("")
                    st.rerun()
            with c2:
                if st.button("提交修改意见", use_container_width=True):
                    st.session_state.show_feedback_input = True
                    st.rerun()

            if st.session_state.get("show_feedback_input"):
                feedback = st.text_area(
                    "输入修改意见（例如：用 subprocess.run 替换 os.system）",
                    key="human_feedback_input",
                )
                if st.button("提交意见", type="primary"):
                    if feedback.strip():
                        _continue_from_human(feedback.strip())
                        st.session_state.show_feedback_input = False
                        st.rerun()
                    else:
                        st.warning("意见不能为空")

        # ============ done ============
        elif st.session_state.review_state == "done":
            report = st.session_state.final_report
            if not report:
                st.error("报告未生成")
                return

            col_s1, col_s2, col_s3, col_s4 = st.columns(4)
            with col_s1:
                m = {"success": ("成功", "status-success"),
                     "partial": ("部分完成", "status-partial"),
                     "failed": ("失败", "status-failed")}
                label, css = m.get(report.status, (report.status, ""))
                st.markdown(f"**状态**: <span class='{css}'>{label}</span>",
                            unsafe_allow_html=True)
            with col_s2:
                render_score_box(report.score_before, "修复前")
            with col_s3:
                render_score_box(report.score_after, "修复后")
            with col_s4:
                st.metric("沙箱验证",
                          "通过" if report.sandbox_passed else "失败")
                st.caption(f"重试 {report.retry_count} 次")

            tab1, tab2, tab3 = st.tabs(["问题清单", "代码对比", "完整报告"])

            with tab1:
                issues = report.action_items if report.action_items else []
                st.caption(f"共 {len(issues)} 个问题")
                render_issues(issues)
                render_skipped_items(report.skipped_items)
                render_notes(report.notes)

            with tab2:
                render_code_diff(report.original_code, report.fixed_code)

            with tab3:
                snapshot = st.session_state.app.get_state(
                    st.session_state.config)
                sandbox = snapshot.values.get("sandbox_result") if snapshot else None
                render_sandbox_result(sandbox)
                st.divider()
                st.markdown(f"**审查摘要**: {report.summary or '无'}")

    # ---- 触发审查 ----
    if start_clicked:
        _start_review()


def _start_review():
    """启动新一轮审查"""
    if not st.session_state.original_code.strip():
        st.error("请先输入代码")
        st.stop()

    if st.session_state.app is None:
        with st.spinner("初始化工作流图..."):
            st.session_state.app = build_graph()

    thread_id = f"ui-{int(time.time())}"
    st.session_state.thread_id = thread_id
    st.session_state.config = {"configurable": {"thread_id": thread_id}}
    st.session_state.review_state = "running"
    st.session_state.final_report = None
    st.session_state.error = None
    st.session_state.show_feedback_input = False
    st.session_state.review_start_time = time.time()

    initial_state = dict(INITIAL_STATE)
    initial_state["original_code"] = st.session_state.original_code

    progress = {
        "done": False,
        "node_status": {},
        "node_times": {},
        "final_state": None,
        "hit_interrupt": False,
        "error": None,
    }
    st.session_state.stream_progress = progress

    st.session_state.poll_start = time.time()

    thread = threading.Thread(
        target=_run_graph_async,
        args=(st.session_state.app, initial_state, st.session_state.config, progress),
        daemon=True,
    )
    thread.start()
    st.session_state.exec_thread = thread
    st.rerun()


def _continue_from_human(feedback: str):
    """人工确认后继续执行：update_state → 后台线程 resume"""
    st.session_state.app.update_state(
        st.session_state.config, {"human_feedback": feedback})

    progress = {
        "done": False,
        "node_status": {},
        "node_times": {},
        "final_state": None,
        "hit_interrupt": False,
        "error": None,
    }
    st.session_state.stream_progress = progress
    st.session_state.review_state = "running"
    st.session_state.poll_start = time.time()

    thread = threading.Thread(
        target=_run_graph_async,
        args=(st.session_state.app, None, st.session_state.config, progress),
        daemon=True,
    )
    thread.start()
    st.session_state.exec_thread = thread


if __name__ == "__main__":
    main()
