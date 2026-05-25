# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run

```bash
# Editable install (first time)
pip install -e .

# Run entry script
python scripts/run.py

# Tests (future)
pytest tests/
```

## Architecture

A LangGraph-based multi-agent code review and auto-fix system. The workflow:

```
code_parser → Send API → [security, performance, style] (parallel reviews)
→ critic_agent (deduplicate + sort + score)
→ coder_agent (auto-fix) → sandbox_executor (subprocess verify)
→ passed? → human_review (HITL) → output_node
→ failed? → reflect_node → retry (max 3) or fail
```

10 nodes total: 7 LLM agents (code_parser, 3 reviewers, critic, coder, reflect) + 3 tools/functions (sandbox, human_review, output).

## Key files

- `src/models.py` — 12 Pydantic models + 4 enums. Bottom of the stack, imported by everything.
- `src/config.py` — Reads `.env`, exports `DEEPSEEK_API_KEY`, `LLM_MODEL`, `MAX_RETRY`, etc.
- `src/graph/state.py` — `AgentState` TypedDict + `INITIAL_STATE`. `review_results` uses `Annotated[list, operator.add]` for parallel writes.
- `src/graph/nodes/` — 4 modules: `reviewers.py` (code_parser + 3 reviewers), `critic_coder.py` (critic + coder + guards), `sandbox.py` (sandbox + docker/subprocess), `terminal.py` (reflect + HITL + output).
- `src/graph/builder.py` — Conditional edge routing functions + `build_graph()`.
- `scripts/run.py` — Entry point that injects `src/` into `sys.path` before any imports.
- `docs/` — 6 design docs: `requirements.md`, `state-design.md`, `agents-design.md`, `models-design.md`, `graph-design.md`, `dev-log.md`

## Tech stack

- LangGraph 1.1.x + LangChain 1.2.x + langchain-deepseek 1.0.x
- Pydantic v2 with `BaseModel` and `Field`
- LLM: DeepSeek-V3 (`deepseek-chat`), fallback GPT-4o-mini
- Sandbox: subprocess (current, stage 2); Docker (future, stage 6: network=none, memory=128MB)
- Frontend: Streamlit (future)
- Checkpointer: InMemorySaver (future: SqliteSaver / PostgresSaver)

## Critical API conventions

- `from langgraph.types import Send` (NOT `langgraph.graph`)
- `from langgraph.graph import StateGraph, END`
- `from langgraph.checkpoint.memory import InMemorySaver`
- `from langchain_deepseek import ChatDeepSeek`
- `from langchain_core.messages import SystemMessage, HumanMessage`
- Structured output via `llm.with_structured_output(pydantic_model)`
- Parallel writes: `Annotated[list[T], operator.add]` for reducers
- Conditional edge functions return node name strings
- Stream via `graph.astream_events(version="v2")`
- HITL via `interrupt_before=["human_review"]` in `compile()`
- All imports are bare module names (e.g. `from config import ...`) — no `src.` prefix
- State is read via `state['key']` but never mutated directly; nodes return `{"key": new_value}` dicts that LangGraph merges via reducers

## Design decisions

- Single `models.py` (not `models/` package): ~200 lines, single file simpler
- Models imported as single `from models import (...)` block per file — no `import models` prefix style
- 3 reviewers share `Issue` model; dimension-specific fields (cwe_id, estimated_impact, pep8_ref) are Optional
- `review_results` uses `Annotated[list, operator.add]` so parallel reviewers append without overwriting
- `ReflectionResult` is NOT stored as-is in state; node function splits it into `reflection_notes` + increments `retry_count`
- `FinalReport.action_items` reuses `ActionItem` (not `Issue`) since it already carries `fix_instruction`
- `SandboxResult.passed` is a boolean, not calculated from `exit_code == 0`, allowing future non-exit-code checks
- `reflect_node` uses independent `ChatDeepSeek(temperature=0.3)` instance — the only node with temperature != 0.1
- `human_review` is an empty `return {}` function; the actual HITL work happens via external `graph.update_state()` before resume
