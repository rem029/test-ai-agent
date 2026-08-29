"""Core loop: goal -> review -> build -> verify -> iterate -> push.

Each role's backend is whatever config.py resolved (see PLAN.md's pluggable
backend design). Per-task token/cost usage is recorded on every step and
persisted to a structured per-run state file (PLAN.md, "Cost & token
tracking per task" and "Interface: CLI first, web later").
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

try:
    import fcntl
except ImportError:
    # Windows fallback: cross-process locking not supported via fcntl, rely on threading.Lock
    fcntl = None

from .backends import BACKENDS
from .backends.base import (
    Event,
    Message,
    RunResult,
    Usage,
    format_messages_to_prompt,
    run_sync,
)
from .config import AGENTFLOW_HOME, Config, RoleConfig
from .database import (
    append_event,
    create_session,
    drain_pending_messages,
    get_session_runs,
    has_stop_signal,
    pop_next_queued_run,
    requeue_run,
    save_run,
)
from .memory import compose_memory, read_global_memory, read_project_memory
from .tools import ToolContext, get_tool, list_tools, parse_tool_requests
from .tools.base import ToolResult

MAX_TOOL_CALLS_PER_STEP = 10


class RunInProgressError(Exception):
    """Raised when a workflow run is requested for a repository that is already running."""

    def __init__(self, cwd: str):
        self.cwd = cwd
        super().__init__(f"A run is already in progress for {cwd}")


_RUN_LOCKS_LOCK = threading.Lock()
_RUN_LOCKS: dict[str, threading.Lock] = {}
_ACTIVE_RUNS: dict[str, str] = {}  # resolved cwd -> run_id


def _get_run_lock(cwd: str) -> threading.Lock:
    resolved = str(Path(cwd).resolve())
    with _RUN_LOCKS_LOCK:
        if resolved not in _RUN_LOCKS:
            _RUN_LOCKS[resolved] = threading.Lock()
        return _RUN_LOCKS[resolved]


def get_active_run(cwd: str) -> str | None:
    resolved = str(Path(cwd).resolve())
    with _RUN_LOCKS_LOCK:
        return _ACTIVE_RUNS.get(resolved)


def _acquire_process_lock(resolved_cwd: str) -> object | None:
    """Acquire a cross-process file lock on AGENTFLOW_HOME/locks/<hash>.lock.

    Returns open file object or raises RunInProgressError.
    """
    if fcntl is None:
        return None
    locks_dir = AGENTFLOW_HOME / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    cwd_hash = hashlib.sha256(resolved_cwd.encode("utf-8")).hexdigest()[:16]
    lock_file = locks_dir / f"{cwd_hash}.lock"
    f = open(lock_file, "a+")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except (BlockingIOError, OSError):
        f.close()
        raise RunInProgressError(resolved_cwd)


def _release_process_lock(lock_file_obj: object | None) -> None:
    if lock_file_obj is None or fcntl is None:
        return
    try:
        fcntl.flock(lock_file_obj.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        try:
            lock_file_obj.close()
        except OSError:
            pass


def _drain_steer(state: RunState, database_path: Path | None = None) -> str:
    """Drain unconsumed steer and note messages for the current run, logging events and returning formatted steer text."""
    messages = drain_pending_messages(state.run_id, kinds=("steer", "note"), path=database_path)
    if not messages:
        return ""

    steer_bodies: list[str] = []
    now = time.time()
    for msg in messages:
        state.log_event(
            "user_message",
            {"body": msg["body"], "kind": msg["kind"], "consumed_at": now},
            database_path=database_path,
        )
        if msg["kind"] == "steer":
            steer_bodies.append(msg["body"])

    if not steer_bodies:
        return ""

    folded = "\n".join(f"- {b}" for b in steer_bodies)
    return f"User added while running:\n{folded}"


def _finalize_stopped(
    state: RunState,
    cwd: str,
    database_path: Path | None = None,
    log_stop_event: bool = True,
    quiet: bool = False,
) -> RunState:
    """Mark a run stopped, consume control signals, log finish, and persist."""
    drain_pending_messages(state.run_id, kinds=("control",), path=database_path)
    state.finished_at = time.time()
    state.stopped = True
    if log_stop_event:
        state.log_event(
            "run_stopped",
            {"reason": "user stop signal", "at": state.finished_at},
            database_path=database_path,
        )
    state.log_event(
        "run_finished",
        {"finished_at": state.finished_at, "pushed": None, "stopped": True},
        database_path=database_path,
    )
    state.save(cwd, database_path=database_path)
    _print_summary(state, quiet=quiet)
    return state


def _spawn_next_queued_run(
    cwd: str,
    database_path: Path | None = None,
) -> threading.Thread | None:
    """Check queued_runs for the next unstarted item for cwd and spawn it in a thread."""
    queued = pop_next_queued_run(cwd, path=database_path)
    if not queued:
        return None

    try:
        config = Config.model_validate(queued["config"])
    except Exception:
        from .config import load_config

        config = load_config()

    def _worker() -> None:
        try:
            run_workflow(
                goal=queued["goal"],
                config=config,
                cwd=queued["cwd"],
                session_id=queued["session_id"],
                database_path=database_path,
            )
        except RunInProgressError:
            requeue_run(queued["id"], path=database_path)
        except Exception as exc:
            import sys

            print(f"Error executing queued run {queued['id']}: {exc}", file=sys.stderr)

    thread = threading.Thread(
        target=_worker,
        daemon=True,
    )
    thread.start()
    return thread

TOOL_USE_INSTRUCTIONS = """\
You may invoke tools by writing one or more blocks like this:

<tool_call>
{"name": "ToolName", "args": {"arg_name": "value"}}
</tool_call>

Emit the block EXACTLY as shown: literal `<tool_call>` and `</tool_call>` tags around ONE JSON object with `name` and `args` keys. Do NOT use `<invoke>`, `<parameter>`, `<function>`, markdown code fences, the `｜` character, or any other wrapper. After you receive a tool result, use it — do not repeat a tool call you have already made.

Available tools:
"""


def _tool_schemas_text() -> str:
    lines = []
    for name in list_tools():
        tool = get_tool(name)
        schema = tool.schema()
        lines.append(f"- {name}: {schema['description']}")
        params = schema["parameters"].get("properties", {})
        for param_name, info in params.items():
            lines.append(f"    • {param_name}: {info.get('description', '')}")
    return "\n".join(lines)


REVIEW_PROMPT = """You are the reviewer/planner for a coding task in this repository.

Goal: {goal}

Read whatever files you need to understand the codebase, then respond with a
concise, numbered task breakdown for what the build step should implement.
Keep it to the essential steps only - no preamble, no explanation of what \
you did, just the numbered plan."""

BUILD_PROMPT = """You are implementing a coding task in this repository.

Goal: {goal}

Plan:
{plan}
{context_section}
{feedback_section}
Make the necessary changes to the repository now."""

VERIFY_PROMPT = """You are verifying a coding task in this repository.

Goal: {goal}

Plan that was implemented:
{plan}

Actually run the relevant commands to check this - do not just read the code
and judge whether it looks right. At minimum, run whatever confirms the
changed code actually executes (for a Python CLI project, that means
literally running the CLI, e.g. `uv run agentflow --check`, or importing the
changed module: `uv run python -c "import agentflow.cli"`), and run any
existing tests/lint. A file that "looks correct" but crashes when run is a
FAIL. Respond with your findings, and end your response with exactly one of
these two lines:
VERIFY_RESULT: PASS
VERIFY_RESULT: FAIL"""

# Backends without a confirmed native file-reading tool (OpenRouter's plain
# chat completion; Antigravity's SDK fallback) can't see the current repo -
# left alone they rewrite files from guesswork instead of editing them. This
# is what actually broke cli.py during Phase B's first live validation run
# (see PLAN.md): the build backend invented APIs that didn't match the real
# code because it never saw it. Backends with real tools (claude-code,
# antigravity CLI) read the repo themselves, so they're excluded here.
NO_NATIVE_TOOLS_BACKENDS = {"openrouter"}
MAX_CONTEXT_BYTES = 60_000

READ_ONLY_TOOLS = frozenset({
    "ReadFile",
    "ListDirectory",
    "SearchFiles",
    "CodeSearch",
    "WebFetch",
    "DocumentationSearch",
    "Lint",
    "TypeCheck",
    "ImportAnalysis",
    "GitStatus",
    "GitDiff",
    "GitCommitSimulation",
})


def _repo_context(cwd: str) -> str:
    """Dump current contents of tracked source files, for backends with no Read tool."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "src"], cwd=cwd, capture_output=True, text=True, check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""

    parts = []
    total = 0
    for rel_path in result.stdout.splitlines():
        path = Path(cwd) / rel_path
        try:
            content = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        block = f"--- {rel_path} ---\n{content}\n"
        if total + len(block) > MAX_CONTEXT_BYTES:
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts)


def _build_backend(role_config: RoleConfig):
    return BACKENDS[role_config.backend](model=role_config.model)


def _execute_tool_call(name: str, args: dict, cwd: str) -> ToolResult:
    """Execute a single parsed tool call and return its result."""
    try:
        tool = get_tool(name)
        return tool.run(args, context=ToolContext(cwd=cwd))
    except Exception as exc:  # noqa: BLE001
        return ToolResult(success=False, error=f"Tool execution failed: {exc}")


def _check_tool_permission(
    tool_name: str,
    args: dict,
    permissions_policy: str,
    permission_handler: Callable[[str, dict], str] | None = None,
) -> tuple[bool, str | None]:
    """Check tool execution against permission policy.

    Read-only tools are always auto-allowed.
    If permission_handler is provided and the tool is mutating:
      permission_handler(tool_name, args) is called.
      "allow" and "allow_session" return (True, None).
      "deny" returns (False, f"Permission denied by user for tool '{tool_name}'.").
    Otherwise follows permissions_policy ('auto' | 'prompt' | 'deny').
    In non-interactive environments, 'prompt' acts as 'deny'.
    """
    if tool_name in READ_ONLY_TOOLS:
        return True, None

    if permission_handler is not None:
        decision = permission_handler(tool_name, args)
        if decision in ("allow", "allow_session"):
            return True, None
        return False, f"Permission denied by user for tool '{tool_name}'."

    if permissions_policy == "deny":
        return (
            False,
            f"Permission denied: tool '{tool_name}' is blocked by permissions policy ('deny').",
        )

    if permissions_policy == "prompt":
        import sys

        if not sys.stdin.isatty():
            return (
                False,
                f"Permission denied: tool '{tool_name}' requires confirmation, but running in non-interactive mode.",
            )
        try:
            ans = (
                input(f"[permission] Allow tool '{tool_name}' with args {args}? [y/N]: ")
                .strip()
                .lower()
            )
            if ans in ("y", "yes"):
                return True, None
            return False, f"Permission denied by user for tool '{tool_name}'."
        except (EOFError, OSError):
            return False, f"Permission denied: unable to prompt for tool '{tool_name}'."

    # "auto" or other default
    return True, None


def _check_budget(
    state: RunState, max_cost_usd: float | None
) -> tuple[bool, str | None]:
    if max_cost_usd is None:
        return True, None
    current_cost = state.total_cost()
    if current_cost > max_cost_usd:
        msg = (
            f"cumulative cost ${current_cost:.6f} exceeded budget limit max_cost_usd=${max_cost_usd:.6f}"
        )
        return False, msg
    return True, None


def _normalize_stream(item):
    """Normalize a generator of Events or a single RunResult into an Iterator[Event]."""
    if isinstance(item, RunResult):
        yield Event.text_delta(item.text)
        yield Event.usage(item.usage)
        yield Event.done(success=item.success, text=item.text, raw=item.raw)
    else:
        yield from item


def _run_with_tools(
    backend,
    prompt: str,
    *,
    cwd: str,
    mode: str,
    state: RunState,
    step_index: int,
    max_calls: int = MAX_TOOL_CALLS_PER_STEP,
    config: Config | None = None,
    database_path: Path | None = None,
    permission_handler: Callable[[str, dict], str] | None = None,
) -> RunResult:
    """Run a backend prompt, executing any requested tools iteratively with structured conversation."""
    initial_content = f"{prompt}\n\n{TOOL_USE_INSTRUCTIONS}{_tool_schemas_text()}"
    messages: list[Message] = [Message(role="user", content=initial_content)]
    policy = config.permissions if config else "auto"
    max_cost = config.max_cost_usd if config else None

    b_name = getattr(backend, "name", "unknown")
    if not isinstance(b_name, str):
        b_name = "unknown"
    b_model = getattr(backend, "model", None)
    if not isinstance(b_model, (str, type(None))):
        b_model = None

    result = RunResult(
        success=False,
        text="",
        usage=Usage(
            b_name,
            b_model,
            None,
            None,
            None,
        ),
        raw={},
    )

    parse_retries = 0

    for call_index in range(max_calls):
        if has_stop_signal(state.run_id, database_path):
            drain_pending_messages(state.run_id, kinds=("control",), path=database_path)
            state.log_event(
                "run_stopped",
                {"reason": "user stop signal", "at": time.time()},
                database_path=database_path,
            )
            return RunResult(
                success=False,
                text="Run stopped by user.",
                usage=result.usage,
                raw=result.raw,
                stopped=True,
            )

        budget_ok, budget_err = _check_budget(state, max_cost)
        if not budget_ok:
            state.log_event("error", {"error": budget_err}, database_path=database_path)
            return RunResult(
                success=False,
                text=f"Run aborted: {budget_err}",
                usage=result.usage,
                raw=result.raw,
            )

        stream_or_res = backend.run(messages, cwd=cwd, mode=mode)

        accumulated_text: list[str] = []
        usage = Usage(
            backend=b_name,
            model=b_model,
            input_tokens=None,
            output_tokens=None,
            cost_usd=None,
        )
        success = True
        raw: dict = {}
        error_msg: str | None = None
        done_text = ""

        for event in _normalize_stream(stream_or_res):
            state.log_event(event.type, event.payload, database_path=database_path)
            if event.type == "text_delta":
                accumulated_text.append(event.payload.get("delta", ""))
            elif event.type == "tool_result":
                p = event.payload
                tool_name = p.get("tool_name") or p.get("name") or "unknown"
                t_args = p.get("args") or {}
                t_res = p.get("result")
                if not isinstance(t_res, dict):
                    t_res = {
                        "output": str(t_res),
                        "success": p.get("status") == "OK" if "status" in p else p.get("success", True),
                    }
                state.add_tool_call(
                    step_index=step_index,
                    tool_name=tool_name,
                    args=t_args,
                    result=t_res,
                )
            elif event.type == "usage":
                p = event.payload
                usage = Usage(
                    backend=p.get("backend", usage.backend),
                    model=p.get("model", usage.model),
                    input_tokens=p.get("input_tokens", usage.input_tokens),
                    output_tokens=p.get("output_tokens", usage.output_tokens),
                    cost_usd=p.get("cost_usd", usage.cost_usd),
                )
            elif event.type == "error":
                success = False
                error_msg = event.payload.get("error")
            elif event.type == "done":
                if "success" in event.payload:
                    success = event.payload["success"]
                if "raw" in event.payload:
                    raw = event.payload["raw"]
                if "text" in event.payload:
                    done_text = event.payload["text"]

        text = "".join(accumulated_text)
        if not text and done_text:
            text = done_text
        if not success and error_msg and not text:
            text = error_msg

        result = RunResult(success=success, text=text, usage=usage, raw=raw)
        if not result.success:
            return result

        try:
            requests = parse_tool_requests(result.text)
        except Exception as exc:  # noqa: BLE001
            return RunResult(
                success=False,
                text=f"Failed to parse tool requests: {exc}\n\n{result.text}",
                usage=result.usage,
                raw=result.raw,
            )

        if not requests:
            if parse_retries < 2 and re.search(r"<[^>]*?tool_calls?\b|<[^>]*?invoke\b", result.text, re.I):
                parse_retries += 1
                state.log_event(
                    "tool_parse_failed",
                    {"step_index": step_index, "snippet": result.text[:300]},
                    database_path=database_path,
                )
                messages.append(Message(role="assistant", content=result.text))
                messages.append(
                    Message(
                        role="user",
                        content=(
                            'Your previous message contained a tool call that could not be parsed. '
                            'Emit tool calls EXACTLY as: <tool_call>{"name": "ToolName", "args": {"key": "value"}}</tool_call> '
                            '— a single JSON object, literal <tool_call> tags, no <invoke>, no <parameter>, no other wrappers. '
                            'Re-issue the tool call now.'
                        ),
                    )
                )
                continue
            return result

        # Agent requested tools: append assistant message to conversation history
        messages.append(
            Message(
                role="assistant",
                content=result.text,
                tool_calls=[{"name": req.name, "args": req.args} for req in requests],
            )
        )

        # Execute requested tools with permission checking
        results_parts: list[str] = []
        tool_results_data: list[dict] = []
        for req in requests:
            state.log_event(
                "tool_call",
                {"step_index": step_index, "tool_name": req.name, "args": req.args},
                database_path=database_path,
            )

            allowed, reason = _check_tool_permission(
                req.name, req.args, policy, permission_handler=permission_handler
            )
            if not allowed:
                state.add_blocker("permission", reason or "", fatal=False, step_index=step_index, database_path=database_path)
                tool_result = ToolResult(success=False, error=reason)
            else:
                tool_result = _execute_tool_call(req.name, req.args, cwd)

            state.add_tool_call(
                step_index=step_index,
                tool_name=req.name,
                args=req.args,
                result=tool_result.model_dump_truncated(),
            )
            status = "OK" if tool_result.success else "ERROR"
            state.log_event(
                "tool_result",
                {
                    "step_index": step_index,
                    "tool_name": req.name,
                    "args": req.args,
                    "result": tool_result.model_dump_truncated(),
                    "status": status,
                    "execution_time_ms": tool_result.duration_ms,
                    "error": tool_result.error,
                },
                database_path=database_path,
            )

            if has_stop_signal(state.run_id, database_path):
                drain_pending_messages(state.run_id, kinds=("control",), path=database_path)
                state.log_event(
                    "run_stopped",
                    {"reason": "user stop signal", "at": time.time()},
                    database_path=database_path,
                )
                return RunResult(
                    success=False,
                    text="Run stopped by user.",
                    usage=result.usage,
                    raw=result.raw,
                    stopped=True,
                )

            results_parts.append(
                f"[{status}] {req.name}({req.args}) -> {tool_result.output}"
            )
            if tool_result.error:
                results_parts.append(f"Error: {tool_result.error}")
            tool_results_data.append(
                {
                    "name": req.name,
                    "success": tool_result.success,
                    "output": tool_result.output,
                    "error": tool_result.error,
                }
            )

        tool_results_text = (
            f"Tool execution results:\n"
            + "\n".join(results_parts)
            + "\n\nContinue with the task."
        )
        messages.append(
            Message(
                role="user",
                content=tool_results_text,
                tool_results=tool_results_data,
            )
        )

    return RunResult(
        success=False,
        text=(
            f"Reached the maximum of {max_calls} tool calls without a final response.\n\n"
            f"Last response:\n{result.text}"
        ),
        usage=result.usage,
        raw=result.raw,
    )


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]


def new_session_id() -> str:
    return "session-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]


@dataclass
class RunState:
    run_id: str
    goal: str
    started_at: float
    config: dict
    session_id: str | None = None
    steps: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    finished_at: float | None = None
    pushed: dict | None = None
    stopped: bool = False
    blockers: list = field(default_factory=list)
    event_seq: int = 0

    def add_blocker(
        self,
        reason: str,
        detail: str,
        *,
        fatal: bool,
        step_index: int | None = None,
        database_path: Path | None = None,
    ) -> None:
        blocker = {
            "reason": reason,
            "detail": (detail or "")[:500],
            "fatal": fatal,
            "step_index": step_index,
            "ts": time.time(),
        }
        self.blockers.append(blocker)
        self.log_event("blocker", blocker, database_path=database_path)

    def total_usage(self) -> dict[str, dict]:
        totals: dict[str, dict] = {}
        for step in self.steps:
            u = step["usage"]
            key = f"{u['backend']}:{u['model']}"
            bucket = totals.setdefault(
                key, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
            )
            bucket["input_tokens"] += u.get("input_tokens") or 0
            bucket["output_tokens"] += u.get("output_tokens") or 0
            bucket["cost_usd"] += u.get("cost_usd") or 0.0
        return totals

    def total_cost(self) -> float:
        return sum(b["cost_usd"] for b in self.total_usage().values())

    def log_event(
        self,
        event_type: str,
        payload: dict,
        database_path: Path | None = None,
    ) -> None:
        self.event_seq += 1
        append_event(
            self.run_id,
            self.event_seq,
            event_type,
            payload,
            path=database_path,
        )

    def add_tool_call(
        self,
        *,
        step_index: int,
        tool_name: str,
        args: dict,
        result: dict,
    ) -> None:
        """Record a tool invocation in the run state."""
        self.tool_calls.append(
            {
                "step_index": step_index,
                "tool_name": tool_name,
                "args": args,
                "result": result,
                "status": "success" if result.get("success") else "failure",
                "execution_time_ms": result.get("duration_ms", 0),
                "error": result.get("error"),
            }
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("event_seq", None)
        return d

    def save(self, cwd: str, database_path: Path | None = None) -> Path:
        return save_run(self, cwd, path=database_path)


def _step_tool_summary(state: RunState, step_index: int) -> tuple[int, str]:
    step_calls = [c for c in state.tool_calls if c.get("step_index") == step_index]
    count = len(step_calls)
    if count == 0:
        return 0, ""
    seen = set()
    names = []
    for c in step_calls:
        name = c.get("tool_name")
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    if len(names) > 6:
        names_str = ", ".join(names[:6]) + ", …"
    else:
        names_str = ", ".join(names)
    return count, names_str


def _record(
    role: str,
    mode: str,
    iteration: int,
    result: RunResult,
    *,
    tool_count: int = 0,
    tool_names: str = "",
) -> dict:
    d = {
        "role": role,
        "mode": mode,
        "iteration": iteration,
        "success": result.success,
        "text": result.text[:6000],
        "usage": asdict(result.usage),
    }
    if len(result.text.strip()) < 3:
        msg = f"_The {role} backend returned no written response._"
        if tool_count:
            msg += f" It ran {tool_count} tool call(s) this step ({tool_names}) — see Tool Calls below."
        d["text"] = msg
        d["no_response"] = True
    else:
        d["no_response"] = False
    return d


def _parse_verify_result(text: str) -> bool:
    for line in reversed(text.strip().splitlines()):
        if line.strip().upper().startswith("VERIFY_RESULT:"):
            return "PASS" in line.upper()
    return False  # no explicit verdict in the response - don't guess pass


def run_workflow(
    goal: str,
    config: Config,
    cwd: str,
    run_id: str | None = None,
    session_id: str | None = None,
    database_path: Path | None = None,
    require_lock: bool = True,
    permission_handler: Callable[[str, dict], str] | None = None,
    quiet: bool = False,
) -> RunState:
    resolved_cwd = str(Path(cwd).resolve())
    lock = _get_run_lock(resolved_cwd)
    acquired_thread_lock = False
    process_lock_file = None
    if require_lock:
        acquired_thread_lock = lock.acquire(blocking=False)
        if not acquired_thread_lock:
            raise RunInProgressError(resolved_cwd)
        try:
            process_lock_file = _acquire_process_lock(resolved_cwd)
        except Exception:
            lock.release()
            raise

    run_id = run_id or new_run_id()
    session_id = session_id or new_session_id()

    if acquired_thread_lock:
        with _RUN_LOCKS_LOCK:
            _ACTIVE_RUNS[resolved_cwd] = run_id

    state: RunState | None = None
    exc_to_record: BaseException | None = None

    def _say(*args: Any, **kwargs: Any) -> None:
        if not quiet:
            print(*args, **kwargs)

    def _notify(ev: str) -> None:
        if state is not None:
            try:
                from . import notify

                res = notify.maybe_notify(state, config, ev)
                state.log_event(
                    "notification", {"event": ev, "result": res}, database_path=database_path
                )
            except Exception:
                pass

    try:
        # Ensure session exists in the database
        create_session(session_id=session_id, cwd=cwd, title=goal, path=database_path)

        # Check for prior runs in this session to support follow-up turns
        prior_runs = get_session_runs(session_id, path=database_path)
        prior_summary = ""
        leftover_steer_text = ""
        leftover_messages: list[dict] = []
        most_recent_prior_id = None
        if prior_runs:
            summary_lines = [f"Prior runs in this session ({session_id}):"]
            for idx, pr in enumerate(prior_runs, 1):
                summary_lines.append(f"Run {idx} (ID: {pr.get('run_id')}):")
                summary_lines.append(f"  Goal: {pr.get('goal')}")
                pushed = pr.get("pushed")
                summary_lines.append(
                    f"  Pushed: {'Yes' if pushed and pushed.get('pushed') else 'No'}"
                )
                for st in pr.get("steps", []):
                    summary_lines.append(
                        f"  - [{st.get('role')}]: {st.get('text', '')[:300].strip()}"
                    )
            prior_summary = "\n".join(summary_lines)

            # Drain leftover unconsumed messages from the most recent prior run in this session
            most_recent_prior_id = prior_runs[-1].get("run_id")
            if most_recent_prior_id:
                leftover_messages = drain_pending_messages(
                    most_recent_prior_id, kinds=("steer", "note"), path=database_path
                )
                if leftover_messages:
                    steer_items = [m["body"] for m in leftover_messages if m["kind"] == "steer"]
                    if steer_items:
                        folded = "\n".join(f"- {b}" for b in steer_items)
                        leftover_steer_text = f"User added while running:\n{folded}"

        state_config = {role: cfg.model_dump() for role, cfg in config.roles().items()}
        state_config["max_iterations"] = config.max_iterations
        state_config["permissions"] = config.permissions
        if config.max_cost_usd is not None:
            state_config["max_cost_usd"] = config.max_cost_usd

        state = RunState(
            run_id=run_id,
            session_id=session_id,
            goal=goal,
            started_at=time.time(),
            config=state_config,
        )

        # Save immediately and log run_started event
        state.save(cwd, database_path=database_path)
        state.log_event(
            "run_started",
            {
                "run_id": run_id,
                "session_id": session_id,
                "goal": goal,
                "cwd": cwd,
                "config": state_config,
                "started_at": state.started_at,
            },
            database_path=database_path,
        )

        memory_block = compose_memory(cwd)
        if memory_block:
            state.log_event(
                "memory_injected",
                {
                    "chars": len(memory_block),
                    "has_global": bool(read_global_memory()),
                    "has_project": bool(read_project_memory(cwd)),
                },
                database_path=database_path,
            )

        if leftover_messages and most_recent_prior_id:
            now = time.time()
            for msg in leftover_messages:
                state.log_event(
                    "user_message",
                    {
                        "body": msg["body"],
                        "kind": msg["kind"],
                        "consumed_at": now,
                        "from_prior_run": most_recent_prior_id,
                    },
                    database_path=database_path,
                )

        review_backend = _build_backend(config.review)
        build_backend = _build_backend(config.build)
        verify_backend = _build_backend(config.verify)

        _say(f"[review] planning for goal: {goal}")
        state.log_event(
            "step_started",
            {"role": "review", "mode": "read", "iteration": 0},
            database_path=database_path,
        )

        review_prompt = REVIEW_PROMPT.format(goal=goal)
        if leftover_steer_text:
            review_prompt = f"{leftover_steer_text}\n\n{review_prompt}"
        if prior_summary:
            review_prompt = f"{prior_summary}\n\n{review_prompt}"
        if memory_block:
            review_prompt = f"{memory_block}\n\n{review_prompt}"

        review_result = _run_with_tools(
            review_backend,
            review_prompt,
            cwd=cwd,
            mode="read",
            state=state,
            step_index=0,
            config=config,
            database_path=database_path,
            permission_handler=permission_handler,
        )
        tool_count, tool_names = _step_tool_summary(state, 0)
        rec_review = _record(
            "review",
            "read",
            0,
            review_result,
            tool_count=tool_count,
            tool_names=tool_names,
        )
        state.steps.append(rec_review)
        state.log_event("step_finished", {"step": rec_review}, database_path=database_path)
        state.save(cwd, database_path=database_path)

        if review_result.stopped or has_stop_signal(state.run_id, database_path):
            return _finalize_stopped(state, cwd, database_path, log_stop_event=not review_result.stopped, quiet=quiet)

        # Check budget guardrail
        budget_ok, budget_err = _check_budget(state, config.max_cost_usd)
        if not budget_ok:
            _say(f"[budget] ABORTED: {budget_err}")
            state.add_blocker("budget", budget_err or "", fatal=True, database_path=database_path)
            _notify("blocked")
            state.finished_at = time.time()
            state.log_event("error", {"error": budget_err}, database_path=database_path)
            state.log_event(
                "run_finished",
                {"finished_at": state.finished_at, "pushed": None},
                database_path=database_path,
            )
            state.save(cwd, database_path=database_path)
            _print_summary(state, quiet=quiet)
            return state

        if not review_result.success:
            _say(f"[review] FAILED: {review_result.text[:300]}")
            state.add_blocker(
                "backend_error",
                review_result.text,
                fatal=True,
                step_index=0,
                database_path=database_path,
            )
            _notify("blocked")
            state.finished_at = time.time()
            state.log_event(
                "run_finished",
                {"finished_at": state.finished_at, "pushed": None},
                database_path=database_path,
            )
            state.save(cwd, database_path=database_path)
            _print_summary(state, quiet=quiet)
            return state

        plan = review_result.text
        _say(f"[review] plan:\n{plan}\n")

        feedback = leftover_steer_text if leftover_steer_text else ""
        steer_text = _drain_steer(state, database_path=database_path)
        if steer_text:
            feedback = f"{feedback}\n\n{steer_text}".strip() if feedback else steer_text

        verified = False
        for iteration in range(1, config.max_iterations + 1):
            _say(f"[build] iteration {iteration}/{config.max_iterations}")
            state.log_event(
                "step_started",
                {"role": "build", "mode": "write", "iteration": iteration},
                database_path=database_path,
            )

            steer_text = _drain_steer(state, database_path=database_path)
            if steer_text:
                feedback = f"{feedback}\n\n{steer_text}".strip() if feedback else steer_text

            context_section = ""
            if config.build.backend in NO_NATIVE_TOOLS_BACKENDS or prior_summary:
                context = _repo_context(cwd)
                if context:
                    context_section = f"\nCurrent contents of the repository's source files:\n{context}\n"

            feedback_section = (
                f"\nFeedback from the previous attempt:\n{feedback}\n" if feedback else ""
            )
            build_prompt = BUILD_PROMPT.format(
                goal=goal, plan=plan, context_section=context_section, feedback_section=feedback_section
            )
            if prior_summary:
                build_prompt = f"{prior_summary}\n\n{build_prompt}"
            if memory_block:
                build_prompt = f"{memory_block}\n\n{build_prompt}"

            build_result = _run_with_tools(
                build_backend,
                build_prompt,
                cwd=cwd,
                mode="write",
                state=state,
                step_index=iteration,
                config=config,
                database_path=database_path,
                permission_handler=permission_handler,
            )
            tool_count, tool_names = _step_tool_summary(state, iteration)
            rec_build = _record(
                "build",
                "write",
                iteration,
                build_result,
                tool_count=tool_count,
                tool_names=tool_names,
            )
            state.steps.append(rec_build)
            state.log_event("step_finished", {"step": rec_build}, database_path=database_path)
            state.save(cwd, database_path=database_path)

            if build_result.stopped or has_stop_signal(state.run_id, database_path):
                return _finalize_stopped(state, cwd, database_path, log_stop_event=not build_result.stopped, quiet=quiet)

            budget_ok, budget_err = _check_budget(state, config.max_cost_usd)
            if not budget_ok:
                _say(f"[budget] ABORTED: {budget_err}")
                state.add_blocker("budget", budget_err or "", fatal=True, database_path=database_path)
                _notify("blocked")
                state.finished_at = time.time()
                state.log_event("error", {"error": budget_err}, database_path=database_path)
                state.log_event(
                    "run_finished",
                    {"finished_at": state.finished_at, "pushed": None},
                    database_path=database_path,
                )
                state.save(cwd, database_path=database_path)
                _print_summary(state, quiet=quiet)
                return state

            if not build_result.success:
                _say(f"[build] FAILED: {build_result.text[:300]}")
                state.add_blocker(
                    "backend_error",
                    build_result.text,
                    fatal=False,
                    step_index=iteration,
                    database_path=database_path,
                )
                feedback = build_result.text
                continue

            _say(f"[verify] iteration {iteration}/{config.max_iterations}")
            state.log_event(
                "step_started",
                {"role": "verify", "mode": "verify", "iteration": iteration},
                database_path=database_path,
            )

            verify_prompt = VERIFY_PROMPT.format(goal=goal, plan=plan)
            if memory_block:
                verify_prompt = f"{memory_block}\n\n{verify_prompt}"

            verify_result = _run_with_tools(
                verify_backend,
                verify_prompt,
                cwd=cwd,
                mode="verify",
                state=state,
                step_index=iteration,
                config=config,
                database_path=database_path,
                permission_handler=permission_handler,
            )
            tool_count, tool_names = _step_tool_summary(state, iteration)
            rec_verify = _record(
                "verify",
                "verify",
                iteration,
                verify_result,
                tool_count=tool_count,
                tool_names=tool_names,
            )
            state.steps.append(rec_verify)
            state.log_event("step_finished", {"step": rec_verify}, database_path=database_path)
            state.save(cwd, database_path=database_path)

            if verify_result.stopped or has_stop_signal(state.run_id, database_path):
                return _finalize_stopped(state, cwd, database_path, log_stop_event=not verify_result.stopped, quiet=quiet)

            budget_ok, budget_err = _check_budget(state, config.max_cost_usd)
            if not budget_ok:
                _say(f"[budget] ABORTED: {budget_err}")
                state.add_blocker("budget", budget_err or "", fatal=True, database_path=database_path)
                _notify("blocked")
                state.finished_at = time.time()
                state.log_event("error", {"error": budget_err}, database_path=database_path)
                state.log_event(
                    "run_finished",
                    {"finished_at": state.finished_at, "pushed": None},
                    database_path=database_path,
                )
                state.save(cwd, database_path=database_path)
                _print_summary(state, quiet=quiet)
                return state

            steer_text = _drain_steer(state, database_path=database_path)
            if steer_text:
                feedback = f"{feedback}\n\n{steer_text}".strip() if feedback else steer_text

            verified = verify_result.success and _parse_verify_result(verify_result.text)
            _say(f"[verify] {'PASS' if verified else 'FAIL'}: {verify_result.text[:300]}")

            if verified:
                break
            feedback = f"{verify_result.text}\n\n{feedback}".strip() if feedback else verify_result.text

        state.finished_at = time.time()

        if has_stop_signal(state.run_id, database_path):
            return _finalize_stopped(state, cwd, database_path, log_stop_event=True, quiet=quiet)

        if verified:
            state.pushed = _commit_and_push(goal, plan, cwd, quiet=quiet)
        else:
            _say(f"[iterate] gave up after {config.max_iterations} iteration(s) without passing verify")

        state.log_event(
            "run_finished",
            {"finished_at": state.finished_at, "pushed": state.pushed},
            database_path=database_path,
        )
        state.save(cwd, database_path=database_path)
        _print_summary(state, quiet=quiet)
        return state
    except BaseException as exc:
        exc_to_record = exc
        raise
    finally:
        if state is not None and state.finished_at is None:
            state.finished_at = time.time()
            error_msg = str(exc_to_record) if exc_to_record is not None else "interrupted"
            state.log_event(
                "run_finished",
                {"finished_at": state.finished_at, "pushed": None, "error": error_msg},
                database_path=database_path,
            )
            state.save(cwd, database_path=database_path)

        if state is not None:
            _notify("finished")

        if acquired_thread_lock:
            with _RUN_LOCKS_LOCK:
                _ACTIVE_RUNS.pop(resolved_cwd, None)
            _release_process_lock(process_lock_file)
            lock.release()
            _spawn_next_queued_run(resolved_cwd, database_path=database_path)


def _commit_and_push(goal: str, plan: str, cwd: str, quiet: bool = False) -> dict | None:
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True
    )
    if not status.stdout.strip():
        if not quiet:
            print("[push] no changes to commit")
        return None

    subject = goal.strip().splitlines()[0][:72]
    message = (
        f"{subject}\n\n"
        f"Goal: {goal}\n\n"
        f"Plan:\n{plan}\n\n"
        f"Verify: PASS\n\n"
        f"Co-Authored-By: agentflow <noreply@agentflow.local>"
    )

    add = subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True, text=True)
    if add.returncode != 0:
        if not quiet:
            print(f"[push] git add failed: {add.stderr.strip()}")
        return {"pushed": False, "error": f"git add failed: {add.stderr.strip()}"}

    commit = subprocess.run(
        ["git", "commit", "-m", message], cwd=cwd, capture_output=True, text=True
    )
    if commit.returncode != 0:
        if not quiet:
            print(f"[push] git commit failed: {commit.stderr.strip()}")
        return {"pushed": False, "error": f"git commit failed: {commit.stderr.strip()}"}

    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, capture_output=True, text=True
    ).stdout.strip()
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True
    ).stdout.strip()
    push = subprocess.run(
        ["git", "push", "-u", "origin", branch], cwd=cwd, capture_output=True, text=True
    )

    if push.returncode != 0:
        if not quiet:
            print(f"[push] commit created ({sha[:8]}) but push failed: {push.stderr.strip()}")
        return {"branch": branch, "commit": sha, "pushed": False, "error": push.stderr.strip()}

    if not quiet:
        print(f"[push] committed and pushed {sha[:8]} to {branch}")
    return {"branch": branch, "commit": sha, "pushed": True}


def _print_summary(state: RunState, quiet: bool = False) -> None:
    if quiet:
        return
    print("\n=== agentflow run summary ===")
    print(f"run_id: {state.run_id}")
    if state.session_id:
        print(f"session_id: {state.session_id}")
    print(f"goal: {state.goal}")
    if getattr(state, "stopped", False):
        print("status: stopped by user")
    totals = state.total_usage()
    grand_cost = 0.0
    for key, bucket in totals.items():
        print(
            f"  {key}: in={bucket['input_tokens']} out={bucket['output_tokens']} "
            f"cost=${bucket['cost_usd']:.6f}"
        )
        grand_cost += bucket["cost_usd"]
    print(f"total cost: ${grand_cost:.6f}")
    print(f"pushed: {state.pushed if state.pushed else 'no (not verified, or no changes)'}")

