# Feasibility: Multi-Agent Dev Workflow (pluggable model backends)

## Context

The repo `rem029/test-ai-agent` is brand new (no commits yet). The goal is a
multi-agent workflow: take a user goal → review → plan → build → test →
verify/iterate → push, where each agent role can run on whichever backend
the user picks, not a hardcoded pairing:
- **Claude Code** — billed against the user's existing Claude subscription
  (not pay-per-token API), originally proposed as the "senior developer".
- **Google Antigravity** — billed against the user's existing Google AI
  subscription (OAuth), originally proposed as the "junior developer".
- **OpenRouter** — a hosted API aggregator giving pay-per-token access to
  many models (Claude, Gemini, GPT, Llama, etc.) through one OpenAI-compatible
  API, added per the user's follow-up request so any role can run on a
  standard API key instead of either subscription.

Every agent role (senior, junior, or however roles end up split) should be
configurable to use **any** of these three backends — Claude Code
subscription, Antigravity OAuth, or OpenRouter API key — selected per role,
not fixed at senior=Claude/junior=Antigravity.

Before writing any code, the user asked to confirm this is actually doable
and how. This document is the research findings and recommended design.
**No implementation yet — the user chose to confirm feasibility first.**

## Findings

### 1. Claude side — subscription reuse is doable, but not through the SDK's advertised path

Official docs (code.claude.com) explicitly steer *SDK* users toward
`ANTHROPIC_API_KEY` (pay-per-token), and state: "Anthropic does not allow
third party developers to offer claude.ai login or rate limits for their
products, including agents built on the Claude Agent SDK."

However, that restriction targets **products offered to other people's
customers**, not personal automation on your own account. The actual
mechanism that reuses your subscription is running the `claude` CLI itself
in **non-interactive/headless mode**, without `--bare` and without
`ANTHROPIC_API_KEY` set:

```bash
claude -p "Review the plan for <goal> and break it into build tasks" \
  --output-format json --allowedTools "Read,Grep,Glob"
```

- Docs confirm `--bare` mode is what drops subscription auth ("bare mode
  doesn't use your subscription login... never reads OAuth credentials or
  the system keychain"). The clear implication: **default (non-bare) `-p`
  mode does read your existing `claude login` OAuth session**, i.e. it bills
  against your Claude subscription usage limits, same as interactive use.
- The Python/TypeScript Agent SDK (`claude-agent-sdk`) bundles this same CLI
  binary and drives it the same way — so `options` without an API key set,
  running non-bare, should behave the same way. This session itself is an
  instance of that pattern.
- Practical takeaway: drive the senior-dev role via `claude -p` subprocess
  calls (or the Python SDK's `query()`, unset `ANTHROPIC_API_KEY`, avoid
  `--bare`), not by treating "Agent SDK" and "API key" as inseparable.

### 2. Antigravity side — subscription reuse is doable via the CLI, not the Python SDK

Two distinct things exist under the Antigravity name:
- **`antigravity-sdk-python`** — a Python library, auth is `GEMINI_API_KEY`
  or Vertex AI Application Default Credentials only. No consumer-subscription
  path.
- **Antigravity CLI** (`antigravity` binary) — supports headless mode using
  **cached Google account credentials** from a one-time interactive login
  (or a device-code flow over SSH). If the account is on **Google AI Pro /
  Ultra**, headless runs get that plan's higher rate limits — no separate
  API key billing. An explicit API-key mode also exists as a fallback for
  pure CI use with no account.

Practical takeaway: drive the junior-dev role via the **Antigravity CLI in
headless/non-interactive mode**, authenticated once with the user's Google
account, mirroring the Claude side exactly.

**Update from Phase A infrastructure work:** `google-antigravity` (the SDK)
installs cleanly from PyPI in this sandbox and is confirmed to be exactly
the Python library described above (`Agent`, `LocalAgentConfig`, etc.) — no
bundled CLI binary, no console-script entry point. The actual `antigravity`
CLI is not installable here: it is distributed from `antigravity.google`
with an interactive browser-based OAuth login, and this sandboxed
environment's network egress to that domain is blocked. `AntigravityBackend`
therefore tries the CLI first and falls back to the SDK
(GEMINI_API_KEY/Vertex) when the CLI isn't present, so infra can still be
validated in CI/sandboxed environments — but reusing the actual Google
subscription (Pro/Ultra rate limits) requires running the CLI path on a
machine that can reach `antigravity.google` and complete the browser login,
i.e. the user's own machine, not this container.

### 3. OpenRouter — straightforward, no open questions

OpenRouter is a standard hosted API: one OpenAI-compatible endpoint
(`https://openrouter.ai/api/v1/chat/completions`), authenticated with a
per-account API key (`OPENROUTER_API_KEY`), pay-per-token billed by
OpenRouter itself (it marks up or passes through the underlying model's
price). It can serve Claude, Gemini, GPT, Llama, and others through the same
call shape. Unlike the other two, there's no subscription-reuse angle here —
it's just an API key, no feasibility question to resolve. It's useful as:
- A drop-in backend for either role when the user doesn't want to spend
  Claude/Google subscription usage on a given run.
- A fallback if one subscription hits its rate limit mid-workflow.
- A way to try other models (e.g. GPT, Llama) in either role without adding
  another provider integration later.

**Update from Phase A infrastructure work:** `openrouter.ai` was blocked by
the original sandboxed session's network egress policy (confirmed via both
`curl` and `WebFetch` — a policy denial, not a code/key problem), so the
default model slug shipped as an unverified guess.

**Verified since, from the user's own code server (real network access):**
- The supplied disposable key is valid ($2 limit, expires 2026-08-28).
- `deepseek/deepseek-v4-flash` is a real OpenRouter slug — confirmed against
  the live `/models` list (1M context, ~$0.05 / $0.10 per M input/output
  tokens). The guess was correct.
- A real `/chat/completions` call against it succeeds, and the response's
  `usage` object includes `cost` directly (no separate pricing lookup
  needed) — confirms the "Cost & token tracking per task" design below.
- `agentflow --check` passes for real on that machine for both `claude-code`
  and `openrouter`.

The key itself was never committed anywhere — it only ever lived in a shell
env var and a gitignored local `agentflow.config.yaml`.

### 4. Confidence / caveats

- Claude findings are from Anthropic's own current docs (`code.claude.com`),
  fetched directly — high confidence.
- Antigravity CLI headless-auth findings come from secondary sources (Google
  developer forum thread, Codelabs tutorial, blog) since `antigravity.google`
  itself is not reachable from this network — reasonably credible (specific,
  consistent across independent sources) but **should be double-checked
  against `antigravity --help` / official docs once the CLI is actually
  installed**, before relying on it.
- Several SEO-blog search hits (e.g. claims of a flat "$X/month Agent SDK
  credit" for Claude subscriptions) looked like fabricated/AI-generated
  content and were discarded — not corroborated by Anthropic's own docs.
- Antigravity's free tier has reportedly shrunk in rate limits over time;
  the user's Pro/Ultra plan matters for iteration speed, and this should be
  watched once running for real.

## Orchestrator

Naming this explicitly since "who is running this?" wasn't previously
spelled out as its own concept: **the orchestrator is `agentflow` itself —
plain Python control flow, not an LLM or agent.** Concretely, it's
`orchestrator.py`'s `run_workflow()`, invoked by `cli.py` when you run
`agentflow "<goal>"`.

It is deterministic code that calls out to whichever LLM backend is
configured for each role, and makes every control decision itself:

- Calls the **review** backend, gets a plan back as text.
- Calls the **build** backend with that plan.
- Calls the **verify** backend and parses its own `VERIFY_RESULT: PASS/FAIL`
  line out of the response — the orchestrator decides pass/fail from that
  parse, it does not ask another LLM to judge.
- Loops build → verify with feedback on FAIL, up to `config.max_iterations`.
- Runs `git commit`/`git push` itself once verified — a subprocess call,
  not something it delegates to a backend.
- Records each step's `Usage` and writes the run-state JSON.

No review/build/verify role is "in charge" of the run; they're workers the
orchestrator calls and evaluates. This keeps the control loop reproducible
and auditable regardless of which backend is doing the actual work — the
same principle behind Bernstein's "no model in the coordination loop"
design (see the open-source landscape note below), arrived at
independently here for the same reason: an LLM deciding its own retry/pass
logic is exactly the failure mode Phase B's live test caught (see Phase C).

## Recommended architecture (for a later implementation phase)

A Python orchestrator (matches the user's language choice) built around a
**pluggable backend interface**, so each role picks its provider
independently via config (e.g. a `backend: claude-code | antigravity |
openrouter` field per role in a YAML/JSON config or env vars), rather than
a fixed senior/junior-to-provider mapping:

- `ClaudeCodeBackend` — shells out to `claude -p ... --output-format json`,
  non-bare, no `ANTHROPIC_API_KEY` set, so it authenticates via the existing
  `claude login` subscription session.
- `AntigravityBackend` — shells out to `antigravity` CLI headless mode,
  authenticated via the cached Google account OAuth session (Pro/Ultra
  rate limits apply automatically).
- `OpenRouterBackend` — plain HTTPS calls to OpenRouter's chat-completions
  API with `OPENROUTER_API_KEY`, model name picked per role (e.g.
  `anthropic/claude-...`, `google/gemini-...`, or any other listed model).

All three implement the same small interface (`run(prompt, tools, cwd) ->
structured result`), so the workflow steps don't care which backend backs
which role:

1. Take a goal from the user (CLI arg or file).
2. **Review/Plan** — the configured review/plan-role backend turns the goal
   into a task breakdown, read-only tools only.
3. **Build** — the configured build-role backend executes each task, scoped
   to the repo working directory, allowed to write files.
4. **Test/Verify** — the configured verify-role backend runs the test
   suite/lint, reviews the diff, and produces a pass/fail + notes. (Can be
   the same backend as step 2, or a different one — configurable.)
5. **Iterate** — on fail, loop back to step 3 with feedback appended, up to
   a max iteration count.
6. **Push** — once verified, orchestrator runs `git add`, `git commit`,
   `git push -u origin <branch>` directly (no PR, per user's answer).

Each backend returns structured JSON (`--output-format json` for Claude;
Antigravity's equivalent flag to be confirmed; native JSON for OpenRouter),
so the orchestrator parses results the same way regardless of backend.

## Interface: CLI first, web later

Decided: the orchestrator is a **CLI tool** for now, not a web app.

**Usage model** — same pattern as the Claude Code CLI itself: install the
orchestrator as its own command (via `pip install -e .`, console-script
entry point, name TBD), `cd` into the target repo, then invoke it with the
goal:

```bash
cd test-ai-agent
<orchestrator-command> "build a login page with email/password auth"
```

It operates on the current working directory as the target repo — all
file reads/edits, test runs, and git operations happen there, exactly like
`claude` does when you `cd` into a project and run it.

Rationale: the whole pipeline is already subprocess/CLI-driven (`claude -p`,
`antigravity`, git) — a terminal UI is the fastest way to get the actual
review→plan→build→test→iterate loop working, with no extra hosting, auth,
or server layer to build before the core logic is even proven.

To keep a future web dashboard cheap to add without re-plumbing the
orchestrator, every run should persist structured state as it goes (not
just print to stdout) — e.g. a JSON or SQLite file per run capturing: the
goal, the plan, each iteration's backend calls, diffs, test/verify results,
and final push status. A read-only web viewer (or just `tail`-ing the file)
can be layered on top later purely by reading this state, once the CLI loop
itself is working end-to-end.

## Memory: descriptive git history instead of a separate file

Decided: no separate memory file/format to build or maintain. Instead, the
orchestrator enforces **descriptive commit messages** as the shared record
any backend can read via plain `git log` / `git show` — no bespoke
convention to keep in sync across three providers.

- Each commit the orchestrator makes should summarize: the goal it was
  working toward, a short plan/approach summary, and the verify/test
  outcome — not just "build changes."
- Any backend, before starting work, can run `git log --oneline -20` (or
  `git log -p` on relevant files) to reconstruct recent project context —
  this works identically for Claude Code, Antigravity, and OpenRouter-driven
  calls, since it's just a shell command, not a custom format one backend
  understands and another doesn't.
- Simpler than a maintained memory file: nothing to prune, nothing to keep
  in sync, no risk of the memory file and actual repo state drifting apart.

## Open items before implementation

- Confirm exact Antigravity CLI headless flags (prompt arg, structured
  output format, working-directory scoping, tool/permission flags) directly
  from `antigravity --help` / installed docs — the current picture is from
  secondary sources.
- Decide max iteration count and what "verified" means concretely (tests
  passing? lint clean? senior's sign-off?) — deferred until the user is
  ready to define the build/test loop for a real task.
- Confirm target push branch naming/convention once implementation starts.
- Decide the default backend-per-role mapping (e.g. review/verify → Claude
  Code, build → Antigravity, with OpenRouter as an override/fallback) —
  and how the user switches backends per run (config file vs CLI flag vs
  env var).
- Define the exact commit message template/convention (e.g. goal line +
  plan summary + verify result in the body) so it stays consistent across
  runs and is easy for any backend to parse back out of `git log`.

## Open-source landscape (related projects)

Checked after Phase C, since it's worth knowing what already exists before
investing further. Both verified directly (repo/docs fetched, not taken
from search snippets alone):

- **[OpenCode](https://github.com/sst/opencode)** — closest mature match.
  200k+ stars, actively maintained, genuinely provider-agnostic (Claude,
  Gemini, OpenRouter, Bedrock, local via Ollama). Already has a "plan"
  (read-only) vs "build" (full access) agent-mode split — the same
  read/write role separation used here.
- **[Bernstein](https://github.com/sipyourdrink-ltd/bernstein)** —
  conceptually closer to this project's specific shape: goal → tasks →
  multiple pluggable CLI coding-agent backends (Claude Code, Codex, Gemini
  CLI, 40+ adapters), each in an isolated git worktree, tests/lint gating
  the merge, "no model in the coordination loop" (see the Orchestrator
  section above). Newer/smaller (beta, single maintainer) than OpenCode —
  promising, not as proven.
- **[awesome-cli-coding-agents](https://github.com/bradAGI/awesome-cli-coding-agents)**
  — curated list for browsing the wider space (Aider, Goose, SWE-agent,
  etc.) if this project's scope ever needs re-justifying against it.

What this project does that neither documents as a built-in: subscription-
vs-API-key billing as a first-class design constraint (Findings #1/#2), and
per-role token/cost tracking from day one (see "Cost & token tracking per
task").

## Branch workflow

Every phase is done on its own branch, not directly on `dev`:

1. `git checkout dev && git pull` — start from the current tip of `dev`.
2. `git checkout -b phase-<n>` (e.g. `phase-j`, `phase-l7`) — one branch per
   phase, branched from `dev`.
3. Implement, test (`uv run pytest`), and commit on that branch.
4. Merge back into `dev` once the phase is reviewed and green; `dev` stays
   the integration branch. Do not commit phase work straight to `dev`.

Small follow-up fixes to an already-merged phase can go on a short-lived
`<phase>-<topic>` branch (e.g. `deepseek-toolcalls`, `followup-composer`)
branched from `dev` and merged back the same way.

## Task breakdown

Broken into phases so infrastructure lands first and can be reviewed before
any orchestration logic is built on top of it.

### Phase A — Infrastructure (this phase, do next)

1. Commit this design doc to the repo root as `PLAN.md` (first commit).
2. Scaffold the Python project: `pyproject.toml`/`uv` (or `venv`+`pip`),
   base package layout (e.g. `src/orchestrator/`), `.gitignore`.
3. Confirm/install the **Claude Code CLI** in this environment (`claude
   --version`); confirm it's already authenticated via subscription login
   in this session, since that's the auth path Phase 1's findings depend on.
4. Install the **Antigravity CLI**; run its one-time interactive login;
   verify headless mode works with cached credentials (`antigravity --help`,
   a trivial headless prompt) — this also closes the "confirm exact headless
   flags" open item from the findings above.
5. Set up **OpenRouter** access: obtain/store `OPENROUTER_API_KEY` (user-
   provided), verify a basic chat-completions call works.
6. Define the config schema for backend-per-role selection (env vars and/or
   a `config.yaml`/`.json`) — no orchestration logic yet, just the schema
   and a loader that validates it.
7. Stub the backend interface (`ClaudeCodeBackend`, `AntigravityBackend`,
   `OpenRouterBackend`) with real auth/connectivity checks (e.g. a
   `.ping()`/health-check call each), but no `run(prompt, tools, cwd)` task
   logic yet — just prove each backend is reachable and authenticated the
   way Phase 1 assumed.
8. Commit infrastructure scaffolding, stop, and let the user review before
   Phase B starts.

### Phase B — Core orchestrator loop (done)

- Implemented `run(prompt, cwd, mode) -> RunResult` for real on each backend
  (`mode` is "read"/"write"/"verify"; see `backends/base.py`).
- Implemented review → build → verify → iterate → push in `orchestrator.py`,
  wired to Phase A's config. Commit message includes goal/plan/verify
  result per the memory-via-git-history design above.
- Every step's `Usage` is persisted to `~/.agentflow/agentflow.db`
  (gitignored) and summarized at the end of each run.

**Live validation caught a real bug, now fixed** (see Phase C below for the
full story): the first real run pushed a commit that broke `cli.py`, because
`OpenRouterBackend` has no file-reading tool and rewrote the file from
guesswork. Fixed by (1) dumping current `src/` contents into the build
prompt for backends without confirmed native tools, and (2) requiring verify
to actually *run* the changed code, not just read it.

### Phase C — Validate end-to-end on a toy task (done)

Ran the full loop twice against this repo on `phase-b`, build on
`openrouter`/`deepseek-v4-flash`, review+verify on `claude-code`:

1. **First run** (goal: add `--version` to `cli.py`) — the loop mechanically
   worked (plan → build → verify PASS → commit → push), but the pushed
   commit was actually broken: DeepSeek invented APIs that didn't match the
   real `Config`/`Backend` classes, and Claude's verify step said "compiles
   cleanly" without actually running anything. Caught by manually testing
   `agentflow --check` after the "successful" run.
2. Fixed the two root causes in `orchestrator.py` (repo-context injection
   for tool-less backends; verify prompt now requires actually executing
   the code).
3. **Second run** (goal: reject a missing `--config` path) — build correctly
   read and edited the real file this time; verify's PASS was independently
   re-checked by hand (`agentflow --config missing.yaml`, `--version`,
   `--check` all behave correctly). Confirms the fix.

Take-away for any future non-tool-using backend (or any backend, really):
don't trust a self-reported PASS without spot-checking at least once.

### Phase D — Local admin web UI (done)

Grew beyond the original "optional read-only viewer" scope: the user wanted
a real local admin panel — live run progress, creating new tasks from the
browser, and editing backend configuration from the browser. Full design
in `plan-web-ui.md`. Built on `phase-d`, branched from `dev`:

- **Stack**: FastAPI + Jinja2 + htmx (vendored locally, no CDN at runtime),
  uvicorn dev server, bound to `127.0.0.1` by default, no auth (personal
  single-user tool). New CLI flags: `agentflow --serve [--host] [--port]`.
  Dev deployment note (this project runs inside a Coolify-hosted
  code-server): reachable at `https://agentui.app.rem029.com/` when started
  with `--host 0.0.0.0 --port 4200` — see `plan-web-ui.md`, "Development
  environment", for why `0.0.0.0` is needed here specifically.
- **Live progress**: htmx polls a run's `/fragment` endpoint every 2s, which
  re-reads the run snapshot that `orchestrator.py` writes incrementally —
  polling stops itself once `finished_at` is
  set, no manual stop button or extra plumbing needed.
- **Task creation**: `POST /runs` spawns `run_workflow` in a real background
  `threading.Thread` (not the request threadpool) and redirects immediately
  to the new run's detail page. Only one run at a time is allowed — a
  module-level lock serializes runs against the same git working tree
  (concurrent builds/commits would clobber each other); a second submission
  while one is active just redirects to the run already in flight.
- **Config editing**: a structured form (dropdown per role, not raw YAML)
  posts to `/config`, which builds a validated `Config(...)` pydantic model
  before writing anything to disk via a new `dump_config()` helper — bad
  input never reaches the file.
- `orchestrator.py`'s `run_workflow` gained an optional `run_id` param (so
  the web layer can know the id before the run starts) and now saves its
  initial state immediately instead of only after the review step — both
  changes are backward compatible with existing callers/tests.
- Verified: `uv run pytest` (10 tests, all mocked — no real backend calls),
  plus manual smoke tests against the two real run fixtures from Phase C and
  a fake fast `run_workflow` (confirmed immediate redirect, live polling,
  polling stopping on completion, and the concurrency guard via the
  dedicated `threading.Event`-synchronized test). Also confirmed live
  through the real Coolify-hosted URL via browser automation: dashboard,
  run detail, and config editor (including a real save/round-trip/revert on
  the local agentflow config) all render and work correctly.

### Phase E — Design polish pass (Impeccable) (done)

Completed the polish pass over the local admin web UI using Impeccable
design principles:

- **Context artifacts**: Generated `PRODUCT.md` capturing product purpose,
  users, constraints, and principles, and `DESIGN.md` establishing color
  tokens, typography, elevation, spacing scales, and component guidelines.
- **Web UI templates & CSS**:
  - Enhanced `style.css` with dark/light themes, high-contrast semantic
    status tokens (`running`, `pushed`, `failed`), custom scrollbars,
    focus-visible outlines, card surfaces, and responsive tabular layouts.
  - Polished `base.html` (added viewport meta tag and brand navigation header),
    `dashboard.html` (card-based task creation, active run banner, styled run table),
    `run_detail.html` (clean metadata header, breadcrumb navigation, collapsible config),
    `_run_fragment.html` (live status pill with pulsing animation, step logs, monospace cost & model badges),
    and `config_edit.html` (grid-aligned fieldsets for roles and clear save alerts).
- Verified: `uv run pytest` (10 tests passing).

### Phase F — OpenRouter API key configuration

**Implemented:** OpenRouter credentials resolve from `OPENROUTER_API_KEY` first,
then the project-local `agentflow.config.yaml`.
The CLI accepts a one-shot `--openrouter-key` override, and the web UI updates
the saved key without ever rendering it.

**Security:** The agentflow config is written with owner-only (`0600`)
permissions and is gitignored.

*(Note 2026-08-29)*: Credential storage moved to `.env` (env vars only); `agentflow.config.yaml` no longer holds secrets.

*(Note 2026-08-29, reversed)*: Secrets moved back into `agentflow.config.yaml`
under a `credentials:` block — this is the installed app's config store (CLI and
web both read/write it). Resolution order: env var (dev override) > config file
`credentials.<field>` > none. File stays `0600` and gitignored; API responses
never echo the raw key (only masked + source).

### Phase G — Central local persistence

**Implemented:** Backend settings are stored in the project-local `agentflow.config.yaml`.
Workflow task/run state is
persisted in `~/.agentflow/agentflow.db` (SQLite), scoped by target repository,
so the web UI no longer depends on `.agentflow/runs/*.json` inside individual
projects.

*(Note 2026-08-29)*: Credential storage moved to `.env` (env vars only); `agentflow.config.yaml` no longer holds secrets. The CLI `--set-openrouter-key` and web UI write keys to `.env` (mode 0600).

*(Note 2026-08-29, reversed)*: `--set-openrouter-key` and the web UI now write `agentflow.config.yaml` (`credentials:` block) again; `.env` / env vars remain a dev-only override.

## Status

Phases A, B, C, D, E, F, G, H, I (incl. I.5), and J (incl. J.5, J.6) are done.
L1-L5 done. L7 (MCP client support) done and merged to `dev`: L7.1 config +
client, L7.2 orchestrator wiring + `--list-tools`/`--mcp-check`, L7.2a `/mcp`
REPL command. Phase K (web console rewrite) done on branch `phase-k`: K1+K2
(dead-asset delete, SSE endpoint) plus the full "Transport"-direction SPA
rewrite (K3-K5) - see the Phase K section below. L6 (visual direction) and
L7.3 (web MCP config UI) landed with it.

Phase N (requirements clarification + build review) done and merged to `dev`:
requirements loop, build-review step, configurable tool-call caps, review
read-cap fix, empty-response nudge, and DSML param-pair parsing.

Phase O (chat-first run history) done on branch `phase-o`: thread renders
chronological chat bubbles (assistant + user + system), tool calls grouped per
step under a single collapsible "tool callings (N)" toggle, changes footer with
file chips + tool count, auto-scroll with follow/pause indicator, and the
timeline removed from the web UI. Cache-busting query params + no-cache headers
added to static assets.

Also merged to `dev` this session (not part of a numbered phase):
- Readable tool-arg validation errors + `file_path`/`filepath`/`filename`
  aliases for `path` on the file tools; `Toolset.capability_hints()` nudging
  the agent to use `mcp__playwright__*` for web-UI work.
- `/serve [<port>] [<host>]` REPL command - starts the web console
  (`src/agentflow/tui/webserver.py`) in a uvicorn daemon thread against the
  REPL's cwd + DB, works mid-run, so a running session can be watched in the
  browser. Defaults 127.0.0.1:8420.

`dev` is ahead of `origin/dev` - nothing pushed yet this session.

---

## Next Phase — Phase H: Recursive Tool Use & Agent Composition

**Goal:** Evolve agentflow from a deterministic linear orchestrator into a
claude-code-like agentic platform while keeping the existing review → build →
verify → iterate → push workflow intact.

**Core idea:** Agents can request tools during their runs. The orchestrator
parses those requests, executes the tools deterministically, and feeds the
results back to the agent. The orchestrator stays in control of the overall
workflow; the LLM stays in the worker roles.

### Why this matters now

The current workflow already works end-to-end, but the agents are limited to
whatever their native backend can do:
- **OpenRouter** has no repository access unless we manually inject `src/`
  contents into the prompt.
- **Claude Code** can read files on its own, but agentflow doesn't surface
  or audit those reads.
- **Antigravity** (SDK fallback) has no repo access at all in this
  environment.

By giving agentflow its own tool layer, every backend can work with the
repository uniformly, and every tool call becomes observable, replayable,
and testable.

### Planned capabilities

1. **Tool abstraction & registry**
   - Abstract `Tool` base class with schemas and validation.
   - `ToolRegistry` for discovery and lookup.
   - `agentflow --list-tools` to see available tools.

2. **Core tool suite (10+ tools)**
   - File: `ReadFile`, `WriteFile`, `ListDirectory`, `SearchFiles`.
   - Shell: `Shell` with timeout, cwd, and output capture.
   - Code analysis: `Lint`, `TypeCheck`, `ImportAnalysis`.
   - Search: `WebFetch`, `CodeSearch`.
   - Git: `GitStatus`, `GitDiff`, `GitCommitSimulation`.

3. **Orchestrator tool loop**
   - Parse tool requests from agent responses (XML or JSON protocol).
   - Execute tools and return results back to the agent.
   - Record every tool call in `RunState`.
   - Enforce limits (max calls per step, timeouts) to prevent runaway loops.

4. **Backend integration**
   - Pass tool schemas to each backend via system prompt or configuration.
   - Claude Code, OpenRouter, and Antigravity can all request the same tools.

5. **Web UI visibility**
   - Tool call timeline in run details (name, args, result, status).
   - Expandable tool output and visual file diffs.
   - Live updates via htmx polling.

6. **Persistence**
   - Extend SQLite schema with a `tool_calls` table.
   - Store tool calls per step; query and filter later.

7. **Documentation & safety**
   - Tool developer guide for adding new tools.
   - Security review of shell, file, and git tools.
   - Integration tests and benchmarks.

### Architecture principles preserved

- **No LLM in the coordination loop:** The orchestrator decides when to call
  review/build/verify and when to execute tools.
- **Pluggable backends:** Tool availability is uniform across Claude Code,
  OpenRouter, and Antigravity.
- **No CDN:** Web UI continues to use vendored htmx and vanilla CSS.
- **Single-user local deployment:** Web UI stays bound to local/container
  network, no auth layer.

### Task breakdown

Phase H has been implemented. The tool layer, orchestrator loop, web UI
timeline, persistence, tests, benchmarks, and documentation are all in place.

### Success criteria

- ✅ Agents in review, build, and verify steps can request tools.
- ✅ At least 10 tools implemented, validated, and tested.
- ✅ Orchestrator executes tool requests and returns results within the
  existing linear workflow.
- ✅ Web UI displays tool calls, arguments, and results.
- ✅ Tool calls are persisted and queryable in SQLite.
- ✅ `uv run pytest` passes; no security vulnerabilities introduced.
- ✅ Existing CLI and web UI behavior remains backward compatible.

---

## Post-Phase-H review (2026-08-27)

A code review of the shipped Phase H work found the tool layer and the
"no LLM in the coordination loop" design worth keeping, but three structural
limits block the next goal: a terminal UI that behaves like Claude Code and a
web UI that behaves like a modern agent console (OpenCode-style chat threads).

### What Phase H got right (keep)

- `Tool` + pydantic `param_model`, `ToolRegistry`, the XML/JSON request parser.
- 14 built-in tools, path-escape guards, `ToolResult.structured` for diffs.
- `tool_calls` SQLite table and the web timeline.
- Deterministic review -> build -> verify -> iterate -> push control flow.

### Structural limits found

1. **Everything is synchronous and single-shot.** `Backend.run()` returns one
   final string. Neither UI can show live output, streaming tokens, or
   tool-call progress - only a finished result.
2. **No conversation model.** `_run_with_tools` rebuilds the entire prompt +
   full tool schema + all prior tool output as one concatenated string on
   every iteration (`orchestrator.py`). Token-wasteful, fragile past
   `MAX_TOOL_CALLS_PER_STEP`, and not a real message history.
3. **No sessions or follow-up.** A run is one goal, then it ends. The
   "follow-up chat / what's next" item in `NOTES.md` is impossible today.

### Web UI cleanup debt (do first, before adding anything)

- `web/static/htmx.min.js` is a 0-byte file.
- Three overlapping stylesheets: `style.css` (Phase E), `styles.css` (current
  SPA), `auth.css`.
- Orphaned Jinja templates - `base.html`, `dashboard.html`, `run_detail.html`,
  `_run_fragment.html`, `config_edit.html` are not referenced by `app.py`,
  which serves a static SPA. Left behind when the SPA replaced the templated
  UI.
- Orphaned login page - `login.html` + `auth.css`, hardcoded `admin/admin123`,
  no `/login` route and no session middleware. Dead code. For a single-user
  local tool, delete it rather than wire up real auth.
- The Phase D single-run lock (serialize runs against one git worktree) is not
  present in the current `app.py`; `create_run` spawns a thread with no guard.
  Treat as a regression to fix.

### Persistence fixes

- `save_run` does `DELETE FROM tool_calls WHERE run_id=?` then re-INSERTs every
  tool call on every snapshot - O(n^2) writes per run and `created_at` becomes
  meaningless. Make it append-only.
- `runs` stores one opaque `state_json` blob. Add normalized `sessions` and
  `events` tables (below).
- No budget guardrail: add `max_cost_usd` per run.

---

## Phase I - Streaming + sessions core (do next)

**Goal:** make the orchestrator stream, converse, and resume - with no UI work
yet. This unblocks Phases J and K. Keep the deterministic review/build/verify
control flow; the change is *how* each role talks to its backend and *how*
progress is observed.

1. **Streaming event interface.** `Backend.run()` becomes a generator that
   yields typed events: `text_delta`, `tool_call`, `tool_result`, `usage`,
   `done`, `error`. A `run_sync()` helper drains it to the current `RunResult`
   so existing callers/tests keep working.
   - `ClaudeCodeBackend`: `claude -p --output-format stream-json`.
   - `OpenRouterBackend`: SSE with `stream: true`.
   - `AntigravityBackend`: best-effort; may buffer and emit one `text_delta`.
2. **Structured conversation.** Replace the string concatenation in
   `_run_with_tools` with `list[Message]` (role, content, tool_calls,
   tool_results). Tool schemas are sent once, not re-embedded per iteration.
3. **Persisted event log.** New `events` table
   (`run_id, seq, type, payload_json, ts`). The orchestrator appends events as
   they happen; both UIs read this log for live view and for replay. Single
   source of truth.
4. **Sessions.** New `sessions` table; `runs` gets a `session_id` FK. A
   follow-up message starts a new lightweight agent turn seeded with repo
   context + a summary of prior runs in the session.
5. **Permission layer.** Tool execution goes through a policy: auto-allow the
   read-only tools; in interactive mode prompt for `WriteFile`, `Shell`, and
   git-mutating tools; in headless mode follow config
   (`permissions: auto | prompt | deny` per tool class). `Shell` stays
   explicitly "not a sandbox".
6. **Budget guardrail.** Abort a run when cumulative `cost_usd` exceeds
   `max_cost_usd` (config, default unset = no limit).

**Success criteria**

- ✅ `Backend.run()` streams events for all three backends (Antigravity may
  buffer); `run_sync()` preserves the old return shape.
- ✅ `_run_with_tools` holds a real message list; tool schema sent once per step.
- ✅ `events` and `sessions` tables exist; a run is fully reconstructable from its
  event log.
- ✅ A follow-up turn can be issued against an existing session and is persisted.
- ✅ Permission policy gates write/shell/git tools; read tools stay automatic.
- ✅ `uv run pytest` passes; existing CLI and web behavior unchanged for callers
  that use `run_sync()` / the current endpoints.

**Status (2026-08-28):** implemented on branch `phase-i`, not yet committed.
All six items plus two follow-up fixes are in the working tree:
- DB test isolation — an autouse `tests/conftest.py` fixture redirects
  `DEFAULT_DATABASE_PATH` to a temp file so tests never touch
  `~/.agentflow/agentflow.db`.
- Session title is set once at creation and preserved across follow-up runs.
A web-UI + CLI review pass also fixed: `POST /api/config` was wiping
`permissions`/`max_cost_usd`; `_build_config_from_overrides` dropped them for
web-started runs; `/api/health` ignored `--config`; the model-autocomplete
datalist id mismatch; the tool-call timeline rendered every call as a red
`FAIL` (checked `call.success` instead of `call.status`) with no timing; and
`app.js` called an undefined `initTheme()` that broke the whole SPA. 117 tests
pass. Still open for Phase K: `POST /api/runs` has no single-run lock (A4);
the web UI does not consume `/api/sessions` or `/api/runs/{id}/events` (A7);
Config form has no `permissions`/`max_cost_usd` inputs; interrupted old runs
show "Running" forever; Review/Verify step boxes render empty.

## Phase I.5 - Follow-up messages & run control (new session)

**Goal:** let the user send a message, comment, or stop signal to a run that
is *already executing*, without corrupting the git worktree. Builds directly
on Phase I's `events` table and the checkpoint structure already in
`run_workflow` (the points where `_check_budget` is called today).

**Problem today:** `run_workflow` is a straight-through loop with no check for
incoming input. A second `POST /api/runs` during an active run spawns another
`run_workflow` thread on the same worktree — they collide on file writes and
`git commit` (see Phase K "single-run lock", A4). `--resume` only seeds a
*new* run after the first fully finishes; there is no way to inject into a
running one.

### Design

1. **`pending_messages` table** (or a `user_message` event type), keyed by
   `run_id`: `id, run_id, body, kind ('steer' | 'note'), consumed, ts`.
   Writers: the web follow-up composer, a new `agentflow --say <run_id>
   "<text>"`, and web run comments. Writing is non-blocking and returns
   immediately.
2. **Orchestrator drains the queue at each checkpoint** — between review→build
   and between every build/verify iteration (next to the existing
   `_check_budget` calls). Drained `steer` messages are folded into the next
   step's feedback/context block ("User added while running: …"); `note`
   messages are logged as events only, not fed to the agent. Every drain
   emits a `user_message` event so the timeline shows it.
3. **Control signals, checked more eagerly** (every tool call, not just
   checkpoints): `stop` / `abort`. `stop` = finish the current tool, do not
   start the next, mark the run `stopped`, no commit/push. Never hard-kill the
   thread — a kill mid-`git commit` or mid-write corrupts the worktree.
4. **Single active run per repo** (implements the lock Phase K also needs). A
   follow-up submitted while a run is active is routed by `kind`:
   - `steer` → appended to the active run's queue; UI shows "queued — picked
     up at the next step".
   - a genuinely new task → enqueued as the next run in the session (not a
     hard `409`), so the `NOTES.md` "what's next" list falls out naturally.
5. **Session hand-off:** if a run finishes with unconsumed queued messages,
   they auto-seed the next `--resume` run in that session instead of being
   lost.

### Scope

- Backend + orchestrator + one CLI flag + two web endpoints (`POST
  /api/runs/{id}/messages`, `POST /api/runs/{id}/stop`). The composer UI and
  the "queued" indicator are Phase K.
- Tests: queue drain at each checkpoint, `stop` mid-iteration leaves a clean
  worktree, concurrency lock, session hand-off of unconsumed messages.

### Success criteria

- A message sent to a running run is picked up at the next checkpoint and
  visibly influences the next step.
- `stop` halts a run within one tool call, leaves no partial commit, and the
  worktree is clean.
- Two runs can never execute against the same repo at once.
- Unconsumed queued messages seed the next session run rather than vanishing.
- `uv run pytest` passes; one-shot `agentflow "<goal>"` behavior unchanged.

**Status (2026-08-28):** implemented on branch `phase-i.5`. All five design
items plus adversarial-review hardening are in the working tree:
- `pending_messages` table (`kind` in `steer|note|control`) + `queued_runs`
  table, with `add_pending_message`/`get_pending_messages`/
  `drain_pending_messages`/`mark_messages_consumed`/`add_control_signal`/
  `has_stop_signal`/`add_queued_run`/`pop_next_queued_run`/`requeue_run`.
- `run_workflow` wraps its body in `try/except BaseException/finally`: a
  per-cwd `threading.Lock` **and** an `fcntl.flock` lock file under
  `~/.agentflow/locks/` (cross-process), both released in `finally`; a
  crashed/interrupted run is finalized (`finished_at` set, `run_finished`
  logged with the error) instead of showing "Running" forever; the next
  `queued_runs` entry is spawned on completion (re-queued on lock contention).
- `_drain_steer` at every checkpoint (post-review, each build/verify
  iteration); `has_stop_signal` checked per tool call **and** after every
  step and immediately before `_commit_and_push`; `_finalize_stopped` marks
  the run `stopped`, consumes the control rows, and never commits.
- `RunResult.stopped` / `RunState.stopped` fields; `reconstruct_run` handles
  `run_stopped`.
- CLI: `agentflow --say/--note/--stop <RUN_ID> "<text>"` (warn on unknown
  run), and `RunInProgressError` is caught with a clean stderr message.
- Web: `POST /api/runs/{id}/messages`, `GET /api/runs/{id}/messages`,
  `POST /api/runs/{id}/stop`; `POST /api/runs` routes a submission during an
  active run to `queued_runs` (`{"status": "queued", ...}`), never silently
  into steer; `GET /api/runs/{id}/events` now 404s for unknown runs.
- `get_tool_calls` returns chronological (`id ASC`) order.
- 137 tests pass (`+20`: `test_pending_messages.py`, `test_run_control.py`,
  `test_web_control.py`, plus `test_cli.py`/`test_web.py` additions).
- Deferred: SQLite WAL / DDL-per-connection perf pass; the narrow
  concurrent-double-`POST /api/runs` 200-vs-404 race (acceptable for a
  single-user tool). The web composer/queued-indicator UI stays Phase K.

**Web UI fixes shipped in the same session (commits `a039df9`, `5f5eaa9`,
`e0b1696`), ahead of the Phase K rewrite:**
- Run-detail steps now render the agent response as Markdown (vendored
  `static/md.js`), show a PASS/FAIL verdict pill on verify steps, and a
  per-step backend·model·cost header. Tool calls show real output / a diff
  view / an errors block instead of a raw `JSON.stringify` dump.
- **Root cause of the "unreadable runs":** `deepseek-v4-flash` emits tool
  calls wrapped in DSML delimiter tags (`<｜DSML｜tool_call>…`, U+FF5C) that
  the strict `<tool_call>` regex never matched — so `parse_tool_requests`
  returned `[]`, the orchestrator treated the raw block as the final answer,
  and the whole review→build→verify loop silently no-op'd.
  `parser._extract_loose_tool_calls` now handles DSML / ASCII-pipe / bare /
  asymmetric / unclosed forms; `splitToolBlocks` in the UI strips any
  residual block into a "requested tool" chip.
- Real URL routes (`/run`, `/runs`, `/runs/<id>`, `/config`, `/health`) via a
  history-API router + a FastAPI catch-all serving `index.html`; the runs
  list has Prev/Next pagination (`GET /api/runs?limit=&offset=` →
  `{runs,total,limit,offset}`, `database.count_runs`).
- A step that returns only whitespace / only a tool call is no longer blank:
  `_record` synthesizes "the <role> backend returned no written response
  (ran N tools…)" and marks `no_response`. `RunState.blockers` +
  `add_blocker` record budget / review-fail (fatal), build-fail / permission
  denial (non-fatal); run-detail shows a blockers banner, `runStatus` adds
  "Blocked". A 🔔 navbar toggle opts into desktop notifications
  (Notification API, `localStorage`, not the config file) on run
  finish / new fatal blocker — the email channel is Phase L1.
- 161 tests pass.
- Still open for Phase K: `permissions`/`max_cost_usd` config-form inputs;
  interrupted-old-runs "Running" display; SSE instead of polling; the
  follow-up composer + queued indicator; a stylesheet reconciliation to
  DESIGN.md (styles.css predates the token discipline).

## Phase J - Terminal UI (Claude Code-like)

Built on Phase I's event stream. `agentflow` with no goal argument opens an
interactive REPL (the one-shot `agentflow "<goal>"` form stays).

- Prompt loop with streaming assistant output rendered live.
- Tool calls rendered with status spinners; results collapsible.
- Permission prompts: `allow` / `allow for session` / `deny`.
- Inline colored diffs for file edits.
- Slash commands: `/model`, `/config`, `/tools`, `/resume <session>`,
  `/clear`, `/cost`.
- `Ctrl+C` interrupts the current step, not the process.
- Session resume from SQLite; cost/token footer per turn.
- Rendering via `rich`/`textual` + `prompt_toolkit` (presentation libraries,
  not agent frameworks - consistent with the hand-rolled-orchestrator rule).

**Status (2026-08-29):** implemented on branch `phase-j`.
- `permission_handler` injection in `orchestrator.py` (`run_workflow` -> `_run_with_tools` -> `_check_tool_permission`) for interactive mutating tool confirmations without breaking backward compatibility.
- `src/agentflow/tui/render.py`: pure rendering helpers (`format_event`, `format_diff`, `truncate_output`, `format_footer`, `session_cost`).
- `src/agentflow/tui/permissions.py`: `SessionPermissionBroker` coordinating cross-thread tool approval requests (`allow`, `allow_session`, `deny`).
- `src/agentflow/tui/commands.py`: pure slash-command parser and dispatcher (`/help`, `/model`, `/config`, `/tools`, `/resume`, `/clear`, `/cost`, `/exit`, `/quit`).
- `src/agentflow/tui/repl.py`: `run_repl` interactive loop with prompt_toolkit history, live event streaming, non-blocking broker servicing, and Ctrl+C step-interruption.
- CLI wiring in `src/agentflow/cli.py`: bare invocation or `--resume <session>` without goal launches the REPL, preserving one-shot goal execution.
- 263 tests pass (`+35` tests in `test_tui.py`, `test_cli.py`, `test_sessions_events.py`).

**Polish (2026-08-29):** implemented on branch `phase-j-repl-polish`.
- Added `quiet: bool = False` to `orchestrator.run_workflow` and threaded to status prints / `_print_summary` / `_commit_and_push`, silencing duplicate prints during REPL turns while leaving CLI/web behavior unchanged.
- Added `strip_tool_blocks` to `src/agentflow/tools/parser.py` (and exported from `agentflow.tools`), stripping closed/unclosed `<tool_call>`, DSML delimiters, standalone `<invoke>`, bare tool JSON lines, leftover tags, and ```` ```FILE: ```` blocks before printing text deltas.
- Updated `apply_file_blocks` in `backends/base.py` to return diff structures (`path`, `previous`, `current`), and wired `openrouter.py` / `antigravity.py` to emit structured `tool_result` events for inline diff rendering in REPL and web UI.
- Corrected `render.py`: `run_finished` handles error/push/silent states cleanly; `blocker` renders formatted `reason` and truncated `detail`; `step_started` only shows iteration when `N > 1`.
- Collapsed verbose tool outputs in `render.py`: single-line `ReadFile` summary, `.git`-filtered `ListDirectory` display (capped at 8), tighter generic tool truncation (12 lines), and immediate consecutive identical tool call deduplication in `repl.py`.
- Integrated `rich` console status spinner in `repl.py` poll loop during backend wait intervals with clean pause/resume around event renders and prompts.
- Cleaned up REPL banner formatting with `~` home directory abbreviation and single-line soft wrapping.
- 277 tests pass (`+14` tests in `test_parser_strip.py`, `test_streaming.py`, `test_tui.py`).

**Follow-up (2026-08-29):**
- Slash-command autocomplete: `commands.py` gained a declarative `COMMANDS`
  registry (single source of truth for `/help` + completion); new
  `tui/completion.py` `SlashCommandCompleter` wired into the `PromptSession`
  with `complete_while_typing=True`. Completes command names and argument
  positions (`/config` sub-commands, role -> backend, permission values,
  role model ids from `CURATED_MODELS`). Commit `73d86b5`. 297 tests pass.
- Antigravity backend now resolves the `agy` binary (Antigravity CLI's
  current name; falls back to `antigravity`) so `verify` runs on the Google
  subscription. Commit `fb3c5a1`.


## Phase J.5 - REPL: concurrent input & live status

Follow-on polish to Phase J. Feasibility checked 2026-08-29; **implemented
2026-08-29** (commits `50ac23c` J.5b, `867f70e` J.5a). 313 tests pass.

### J.5a - Accept input while a run is executing (Claude Code style)

Today `repl.py` runs the workflow in a daemon thread (`_run_turn`) while the
main thread sits in the event-drain loop (`while True` ... `time.sleep(0.15)`),
reading the keyboard only via `except KeyboardInterrupt` (stop). You cannot
type during a build.

**The backend for this already exists** (built for the web UI):
- `pending_messages` table, kinds `steer` / `note` / `control`
  (`database.py`); `add_pending_message` / `drain_pending_messages`.
- `_drain_steer()` runs at every phase boundary (`orchestrator.py:1064,
  1077, 1207`) and folds queued steer text into the next phase's prompt.
- `add_control_signal(run_id, "stop"|"abort")` + `has_pending_control`
  (`orchestrator.py:453, 625`).
- `add_queued_run()` / `pop_next_queued_run()` / `_spawn_next_queued_run()`
  (`orchestrator.py:1258`) - the next queued goal auto-starts on completion.
- Web already exposes all of it (`POST /api/runs/{id}/messages`, `/stop`,
  auto-queue when `get_active_run` is set).

**Work required (CLI only):**
- Restructure the run loop to accept a line without blocking event
  rendering - cleanest is `prompt_toolkit` async (`prompt_async()` + the
  drain loop as a coroutine); a second input thread feeding a
  `queue.Queue` is the fallback but fights the spinner/print for the tty.
- Route input typed during a run:
  - `/config`, `/model`, `/cost` -> safe to run live; `/clear`,
    `/resume`, `/exit` -> defer or reject mid-run.
  - plain text -> `add_pending_message(run_id, text, "steer")`.
  - a second goal while one runs -> `add_queued_run(...)`.
- Show a "queued" indicator for pending steer / queued runs.

**Caveat:** steer granularity is per phase (review -> build -> verify), not
per tool-call like Claude Code - a queued message lands at the next phase
boundary, which during a long build can be minutes away.

Estimate: ~half a day (state/DB layer is done; this is a REPL input-loop
rewrite).

**Done (`867f70e`):** non-blocking `select` poll on stdin (tty-only) each
loop tick, after event drain + permission servicing. Plain text ->
`add_pending_message(run_id, text, "steer")`; `/config /model /cost /help
/tools` dispatched live (config mutations shared with the running
workflow); other slash commands rejected. `_read_pending_line` /
`_handle_mid_run_input` helpers in `repl.py`. Deferred: prompt_toolkit
line-editing/history for mid-run input (raw tty line discipline for now),
and queuing a *separate* run mid-build (steer only).

**Follow-up (2026-08-29, branch `phase-j.5a-async-input`):** the deferred
prompt_toolkit item is now done — the mid-run `select`/`readline` poll (and
the Rich `console.status` spinner that clobbered typed echo) is replaced by
a real always-open input line. `_execute_turn` runs the workflow in a worker
thread and, on a tty, drives `asyncio.run(_turn_interactive(...))`:
`patch_stdout()` pins a `prompt_async` input at the bottom while
`_drain_async` streams events above it; progress shows in a `bottom_toolbar`
(`⠋ <role> working · $<cost> · Ctrl+C stop`). Tool-permission confirmations
reuse the same input line modally. Non-tty / tests use `_turn_drain_only`
(the old synchronous loop, spinner removed). Event rendering is factored
into `_process_new_events`. `_read_pending_line` removed. 330 tests pass
(`-5` read-pending, `+6` async-turn).

**Fix (branch `phase-j.5a-render-fix`):** `patch_stdout()` defaults to
`raw=False`, which sanitized the VT100 escapes `rich` emits — mid-run output
showed literal `?[36m` codes and didn't wrap. Now `patch_stdout(raw=True)`
and the in-turn `Console` gets an explicit `shutil.get_terminal_size()`
width. 336 tests pass.

**Run indicator (branch `phase-j.5a-run-indicator`):** the dim
`bottom_toolbar` was easy to miss. The prompt itself is now a two-line
animated status while a run executes — cyan spinner + role + elapsed +
cost + "Ctrl+C to interrupt" directly above the `›` input line, re-rendered
every 0.3s via a callable `message` (`_turn_prompt_message`) + `_fmt_elapsed`.
Toolbar restyled to match. 338 tests pass.

### J.5b - Persistent bottom status bar + richer footer

`format_footer()` (`render.py:212`) already computes per-backend
`in / out / cost`, total cost, status, elapsed - printed once per turn.
Missing: session id and title.

- Add a `Session: <id> "<title>"` line to `format_footer` (pass the
  session dict in). Title is set to the first goal at
  `orchestrator.py:887`.
- Add a persistent bottom toolbar: `PromptSession(bottom_toolbar=...)`
  showing `session - "title" - $running_cost`
  (`session_cost(get_session_runs(sid))`). Nothing uses `bottom_toolbar`
  yet.

**Prerequisite for showing agy usage:** `AntigravityBackend._run_cli`
currently returns `_empty_usage()` (`antigravity.py`), so agy runs record
zero tokens/cost. Parse `agy -p --output-format json` for its usage block
first (claude-code and openrouter usage already flow through).

**Done (`50ac23c`):**
- `_run_cli` now uses `--output-format json`; `_extract_usage` parses the
  `usage` block (`input_tokens` / `output_tokens`; agy reports no
  per-token cost, so `cost_usd` stays `None` — subscription). Non-JSON
  stdout falls back to raw text + empty usage.
- `format_footer(state, session=...)` prepends `Session: <id> — "<title>"`
  (output unchanged when `session` omitted).
- `repl.py`: `_session_tag` / `_build_toolbar` helpers; persistent
  `bottom_toolbar` on the prompt: `agentflow · <tag> · "<title>" ·
  $<running cost>`. Footer call passes the session.


## Phase J.6 - REPL: `@`-file completion (done)

Requested 2026-08-29. Like Claude Code: typing `@` at the between-turns
prompt pops a fuzzy file picker for files in the project.

**Status (2026-08-29):** implemented on branch `phase-j.6`. Autocomplete +
literal passthrough only (contents-expansion deferred per the recommendation
below). 329 tests pass (`+10` in `tests/test_tui.py` section 10).
- `tui/completion.py`: `_list_project_files(cwd)` (git `ls-files` + `--others
  --exclude-standard`, dedup/sort; bounded `os.walk` fallback skipping
  `.git`/`.venv`/`node_modules`/`__pycache__`/cache dirs, capped at 5000,
  never raises); `_is_subsequence` scorer; `FileMentionCompleter(cwd,
  file_lister=None, cache_ttl=30.0)` — fires only when the cursor token
  starts with `@` (and not on a leading `/`), fuzzy subsequence match ranked
  by contiguous-substring / basename hit / path length, capped at 50, 30s
  lazy cache. Inserts `@<path>` (start_position covers only the post-`@`
  query).
- `tui/repl.py`: `PromptSession` completer is now
  `merge_completers([SlashCommandCompleter(), FileMentionCompleter(cwd)])`;
  `complete_while_typing=True` unchanged. The mid-run `select` input path
  (J.5a) stays plain — no completion.
- Literal passthrough: `@path` tokens go into the goal string as-is; the
  agent's Read tools act on them. No REPL-side expansion.

**Follow-ups (2026-08-29):**
- `phase-j.6-dotfile-ranking`: `_score` reworked into match tiers (exact
  basename > basename-prefix > path-segment-prefix > basename-substring >
  path-substring > subsequence, then shorter path, then alpha) so `@.` and
  `@.gi` surface dotfiles instead of burying them under files that merely
  contain a `.`. Also stopped the `os.walk` fallback dropping `.github/`
  etc. Plus a `worker.join(timeout=5)` in `_execute_turn` (fixes a flaky
  final-footer race). 332 tests.
- `phase-j.6-include-ignored`: `_list_project_files` now runs `git ls-files
  --others` **without** `--exclude-standard` (tracked + untracked +
  gitignored), pruned by a `NOISE_DIRS` denylist (`.venv`, `node_modules`,
  `__pycache__`, `dist`, `build`, `.egg-info`, …), 8000-cap. Directory
  entries (trailing `/`) are derived and offered; scorer strips the `/`
  before tiering; `display_meta` is `"dir"`. This is why a gitignored
  scratch dir like `.test/` now completes. 335 tests.

### Autocomplete
- Extend the completer in `tui/completion.py`. Today `SlashCommandCompleter`
  only fires on a leading `/`; add an `@`-branch (or a second completer
  combined via `prompt_toolkit.completion.merge_completers`).
- When the token under the cursor starts with `@`, complete file paths
  under `cwd`. File list source: `git ls-files` (tracked) plus untracked-
  not-ignored (`git ls-files --others --exclude-standard`); fall back to a
  bounded `os.walk` skipping `.git/` when not a git repo. Cache the list
  per REPL session, refresh lazily (e.g. on each fresh prompt, or every
  ~30s).
- Fuzzy match the text after `@` — wrap the path completer in
  `prompt_toolkit.completion.FuzzyCompleter`, or match with a small
  subsequence scorer. Show the path as `display`, the containing dir as
  `display_meta`.
- Insert `@<path>` on selection. `complete_while_typing=True` is already
  set, so the menu appears as soon as `@` is typed.
- tty / between-turns prompt only (the mid-run `select` input path from
  J.5a stays plain — no completion there).

### Expansion (decide the semantics)
Autocomplete only inserts text; something must act on `@path` when the
goal is submitted. Options, cheapest first:
- Leave it literal — the path string is context enough for the agent
  (it has Read tools). Zero code.
- Expand in the REPL before dispatch: replace each `@path` with the file
  contents fenced as ```` ```path ... ``` ```` (cap size; skip binary),
  mirroring how `_drain_steer` folds text in. New helper in `repl.py` or
  `commands.py`.
- A dedicated `@`-mentions parser shared by REPL + web composer (aligns
  with Phase K).
Recommendation: ship autocomplete + literal passthrough first; add
contents-expansion as a follow-up if the agent needs it.


## Phase K - Web console rewrite (OpenCode-like)

- Delete the dead templates, `login.html`, `auth.css`, and the empty
  `htmx.min.js`; collapse to one stylesheet.
- Single SPA: session sidebar + message thread + follow-up composer wired to
  Phase I.5's `pending_messages` / stop endpoints, with a "queued" indicator.
- Live updates via SSE off the Phase I event log (replaces 2s JS polling).
- Inline collapsible tool calls with output and visual file diffs (reuse
  `ToolResult.structured`).
- Model/backend picker per session; config panel continues to write
  `agentflow.config.yaml`.
- Restore the single-run worktree lock.
- **TUI/web parity (user, 2026-08-29):** everything the terminal REPL can do
  should be reachable from the web console too. Concretely, that means web
  equivalents of the slash commands: `/model` + `/config` (config panel,
  incl. `mcp_servers` - the deferred L7.3), `/tools` + `/mcp` (a tools/MCP
  panel showing built-ins and live MCP connection status), `/resume` +
  `/cost` (session sidebar + per-session cost), `/clear` (new session),
  mid-run steer + stop (composer + stop button, already scoped above), and
  the `@`-file mention picker (J.6) in the composer. Track this as an
  explicit checklist item when K3 starts.

### Sub-phases

- **K1 - Dead-code cleanup (done, branch `phase-k`).** Deleted the orphaned
  Jinja `templates/` dir (`base`, `dashboard`, `run_detail`, `_run_fragment`,
  `config_edit`, `login`), `static/auth.css`, `static/style.css` (Phase E,
  superseded by `styles.css`), and the unused vendored `static/htmx.min.js`.
  Dropped the now-unused `jinja2` dependency from `pyproject.toml`. Updated
  `AGENTS.md`, `.github/copilot-instructions.md`, `PRODUCT.md` stack
  descriptions (SPA, not Jinja/htmx). 338 tests still pass. Note: the
  single-run worktree lock is **not** a regression — Phase I.5 added a per-cwd
  `threading.Lock` + `fcntl.flock` inside `run_workflow` and `create_run`
  already routes a submission during an active run to `queued_runs`.
- **K2 - SSE live event stream (done, `7df9053`).** `GET /api/runs/{id}/stream`
  replaying the persisted `events` table then tailing new rows.
- **K3-K5 - "Transport" SPA rewrite (done, 2026-08-29, commits `e5f1334`
  backend + `df40394` frontend).** Full from-scratch rewrite, not a reskin.
  - **Direction (L6):** Impeccable `new-work` / `operate` roll, seed
    `dfedc3e0`, candidate 7 of 7 - "a run is a recording you scrub".
    `DESIGN.md` replaced from the built world. A run plays out on a shared
    review/build/verify timeline with a draggable playhead that scrubs the
    event log; a docked transport bar (stop / steer / live cost) is always
    present. One self-hosted mono face (JetBrains Mono), one amber signal
    reserved for the live element, state by lane treatment + cell glyphs not
    pills, hairline dividers, no cards/shadows, dark-only, animation gated to
    live runs + `prefers-reduced-motion`.
  - **K3 backend:** `GET /api/tools`, `GET /api/mcp` (the `--mcp-check`
    equivalent), `GET /api/files` (@-mention), `mcp_servers` in
    `POST /api/config` (was silently wiped), `max_cost_usd: null` clears,
    `total_cost`/`run_count` on `GET /api/sessions/{id}`.
  - **K3/K4 frontend:** session rail, timeline + playhead scrub-to-replay,
    playhead-bound thread, collapsible steps + tool calls, fixed-scale inline
    diffs, docked transport bar (stop / steer / start-run), `@`-file
    completion, `GOAL` header with the full untruncated prompt + full-text
    hover on truncated titles.
  - **SSE everywhere:** `EventSource` off the Phase I event log; all
    `setInterval` polling of run endpoints removed. Orphan/interrupted runs
    (`finished_at` null, not the active run) now resolve to a static
    `interrupted` state - the stream emits `interrupted`+`done`, the client
    classifies up front. Closes the "runs show Running forever" open item.
  - **TUI/web parity:** `Cmd/Ctrl-K` command palette (model / config / tools /
    mcp / resume <session> / cost / clear / stop). Right-side overlay panels
    (no scrim): Config (per-role backend/model, permissions, `max_cost_usd`,
    masked OpenRouter key, global/project memory, notifications, **full MCP
    servers editor - L7.3**), Tools (read-only registry), MCP (live
    per-server connect/error/disabled status + tools).
  - Hand-authored SVG icon set for controls; `styles.css` deleted,
    `console.css` added. Detector clean. 387 tests pass. Desktop + all panels
    + palette + empty/interrupted states verified live via the Coolify URL;
    mobile (<700px) code-reviewed only (review browser stopped honoring
    viewport resize).
  - **Still open (follow-ups, noted in DESIGN.md):** interactive web
    permission prompts (`permissions: prompt` still denies in the headless
    web thread); no session-level SSE stream; no queue-management UI; mobile
    not screenshot-verified; deep-linking `/runs/<id>` shows the active run
    rather than the requested one when a run is in flight.

### Post-K bug-fix round (2026-08-30, on `dev`)

Shipped after the merge, from live use:
- `558a812` - MCP config save sent `auto_approve: "all"` (string) not
  `["all"]`; `handleTransportSubmit` swallowed `{status:queued}` and every
  4xx/5xx silently (now an inline feedback line); `loadProjectFiles` never
  parsed its response so `@`-mention was dead; double-submit guard.
- `0adf7c3` - session dates in the left rail.
- `438e483` - **analysis-only workflow path.** A goal like "review X, give
  me your feedback" produced a good review step then ran build+verify
  anyway; with nothing to build, a tool-using build backend spiralled
  (malformed tool syntax, endless `ListDirectory`, one step hit 213K tokens
  off a recursive full-repo listing). Now: `REVIEW_PROMPT` asks for a
  trailing `BUILD_NEEDED: yes|no`; `run_workflow` finishes after review on
  `no` (or, absent the marker, a goal matching review/audit/explain/question
  verbs). New `Config.workflow_mode = auto|review_only|full`, CLI
  `--workflow-mode` / `--review-only`. Plus tool-loop guards (stop after 3
  identical consecutive calls, or 6 write-mode calls with zero writes) and
  `ListDirectory` now skips `.git`/`.venv`/`node_modules`/... and caps at
  400 entries. Verified live: both "review @.test/dragtask ..." phrasings
  finish review-only, `mode: review_only`, no build. 400 tests.

## Phase L - Projects, memory, extensibility, notifications (user backlog, 2026-08-28)

Captured from the user during the Phase I.5 session. Several items overlap
with Phase K (the web rewrite) and should land together; MCP and skills are
new capability work on top of the Phase H tool layer.

**Status (2026-08-29):** L1, L2, L3, L4, L5 implemented on branch `phase-l`
(commits `64a4d41` L2, `a80536e` L4+L5, `2fee0c7` L3, `3b97be5` L1). 217
tests pass. L6 landed with the Phase K rewrite (2026-08-29) - see Phase K.
L7 done. L8 not started.

### L1 - Email notifications

- New config block (`notifications:` in `agentflow.config.yaml`, or a
  dedicated section) for SMTP / an email provider: on run **finished**
  (pushed / completed / blocked / stopped) and on **action needed** (a
  `blocker` event - budget, permission, backend error; see the Phase I.5
  session's blocker work), send an email summary with a link to the run.
- Complements the per-browser desktop-notification toggle already shipped
  (localStorage `af_notify`). Email is the "I walked away" channel; keep the
  send opt-in and rate-limited (one digest per run, not per event).
- Secrets (SMTP password / API key) follow the OpenRouter-key pattern:
  env-var first, then `0600` project config, never rendered back to the UI.

### L2 - Config UI: surface the OpenRouter key

- The config panel currently never shows the saved OpenRouter key (Phase F
  deliberately write-only). Users can't tell if one is set. Fix: show a
  masked indicator (`sk-or-…dd6d`, last 4 only) + a "set / not set" badge,
  with a "replace" field that still writes without echoing the full value.
- Same treatment for any L1 email secret.

### L3 - Per-run project-folder selection

- Today `cwd` is fixed at `agentflow --serve` launch (`os.getcwd()`); every
  run and the whole DB scope is that one repo. Let the `/run` form pick the
  target project folder (from an allow-list of roots the server was started
  with, or a configurable list in `agentflow.config.yaml` - do **not** allow
  arbitrary filesystem paths from the browser).
- `run_workflow` already takes `cwd`; the DB is already scoped by `cwd`
  (`list_runs`/`list_sessions`/`count_runs` all filter on it). Main work:
  a project picker + validation + the single-run lock is already per-cwd.
- The runs/sessions views gain a project filter.

### L4 - Agentflow-level memory

- A persistent instruction/fact store (à la Claude Code's `CLAUDE.md` /
  memory) that gets injected into review/build/verify prompts: coding
  conventions, "always run `uv run pytest`", "don't touch `legacy/`", etc.
- Editable from the Config panel. Stored outside the repo (in
  `~/.agentflow/`) so it applies across projects.

### L5 - Per-project memory

- Same mechanism as L4 but scoped to one project folder (keyed by `cwd`),
  stored per-repo. Project memory layers on top of global memory in the
  prompt. Editable from Config, filtered by the L3 project selector.

### L6 - UI design reference pass (done, with Phase K, 2026-08-29)

- Committed to the "Transport" visual direction via the Impeccable `new-work`
  `operate` roll (seed `dfedc3e0`). `DESIGN.md` replaced from the built world.
  Full detail in the Phase K section above.
- `styles.css` deleted, not reconciled - the SPA rewrite replaced it with
  `console.css` on the new token discipline. Detector clean (the few
  intentional ramp/palette literals the degraded regex detector can't
  resolve are recorded in `.impeccable/config.json`).

### L7 - MCP client support

- Let agent roles call tools from configured MCP servers, alongside
  agentflow's built-in tool registry. Config: an `mcp_servers:` list
  (command/args/env or URL). The orchestrator's tool loop (`_run_with_tools`)
  dispatches MCP tool calls the same way it dispatches built-ins; MCP tool
  schemas are merged into `_tool_schemas_text()`. Permission policy
  (`READ_ONLY_TOOLS` / `_check_tool_permission`) must extend to MCP tools -
  default unknown MCP tools to "prompt/deny", not "auto".

**Design decisions (2026-08-29):**
- Config store: `agentflow.config.yaml` `mcp_servers:` (user's instruction),
  same file as backends / `credentials:`. Not a separate `.mcp.json`.
- Client: the official `mcp` PyPI SDK (v2.0.0, already a transitive dep via
  `google-antigravity`; promoted to a direct dependency). It's a wire-protocol
  library, not an agent framework - consistent with the hand-rolled rule, same
  as using `httpx` for OpenRouter. stdio transport first; HTTP/SSE deferred.
- The SDK is async; the orchestrator tool loop is sync. `MCPManager` owns a
  background asyncio loop thread, enters all `stdio_client` + `ClientSession`
  contexts on `start()`, exposes sync `list_tools()` / `call_tool()` that block
  on `run_coroutine_threadsafe`, and tears down on `close()`.

**Config shape:**
```yaml
mcp_servers:
  - name: playwright
    command: npx
    args: ["-y", "@playwright/mcp@latest"]
    env: {}
    enabled: true
    auto_approve: []          # tool names auto-allowed even under prompt policy; "all" allowed
```
`MCPServerConfig`: `name`, `command|url` (exactly one), `args`, `env`,
`enabled=True`, `auto_approve: list[str] | "all" = []`.

### Sub-phases

- **L7.1 - config + client + adapter (done, branch `phase-l7`, commit after
  the L7 PLAN commit).** `MCPServerConfig` in `config.py` +
  `load_config`/`dump_config` round-trip + `AGENTFLOW_MCP_DISABLED=1`
  kill-switch; `src/agentflow/mcp/` package: `MCPManager` (background
  asyncio-loop thread, `AsyncExitStack` per run, blocking `list_tools()` /
  `call_tool() -> MCPCallOutcome`, per-server error isolation via `.errors`,
  idempotent `close()`), `MCPClientError`, `MCPTool(Tool)` adapter naming
  remote tools `mcp__<server>__<tool>` with a passthrough param model and the
  remote `inputSchema` surfaced verbatim, `discover_mcp_tools()`. `mcp>=2.0.0`
  promoted to a direct dependency. 8 tests drive `tests/fixtures/fake_mcp_server.py`
  (`MCPServer` from `mcp.server.mcpserver`) - no npx / network. URL/HTTP
  transport stubbed as unsupported. 346 tests.
- **L7.2 - orchestrator wiring + CLI (done).** `Toolset` in `orchestrator.py`
  unifies the built-in registry with a run's live MCP tools
  (`has`/`get`/`names`/`schema_text`/`is_read_only`/`is_mcp`/`mcp_auto_approved`);
  `_tool_schemas_text()` delegates to it. `_execute_tool_call` gains an
  optional trailing `toolset`; `_run_with_tools` a keyword `toolset` (empty
  default = unchanged behaviour); `_check_tool_permission` a keyword-only
  `toolset`. MCP tools never read-only; one not in its server's `auto_approve`
  (or `["all"]`) needs approval even under `auto` - interactive prompts,
  headless `auto` denies with an actionable message. `run_workflow` starts an
  `MCPManager` for enabled servers before review, logs `mcp_ready`/`mcp_error`,
  passes the `Toolset` to all three `_run_with_tools` calls, closes it in
  `finally`. `--list-tools` also lists live MCP tools + errors; new
  `--mcp-check` (`[OK] name: N tool(s): ...` / `[ERROR] ...`, exit 1 on any
  error). `/tools` in the REPL notes the configured server count. 352 tests;
  `--mcp-check` verified end-to-end.
- **L7.2a - `/mcp` REPL command (user, 2026-08-29).** A `/mcp` slash command
  in the TUI that connects to each configured server and reports its tools /
  errors (the `--mcp-check` equivalent, rich-formatted, shows disabled
  servers too). Not in `_SAFE_DURING_RUN` - rejected mid-run (a run already
  holds its own `MCPManager`; a second would spawn duplicate server procs).
  Added to the `COMMANDS` registry so `/help` + completion pick it up.
- **L7.3 - web config UI for MCP servers.** Deferred - lands with the Phase K
  web console rewrite (config panel). Backend is ready: `GET/POST /api/config`
  just needs `mcp_servers` surfaced (the `Config` model already carries it,
  and `dump_config` persists it).

### L8 - User-defined skills

- A skill = a named, versioned instruction bundle (like this repo's
  `.claude/skills/`) that a role can load for a task: a `SKILL.md` plus
  optional scripts/references. Config lists skill directories; the
  orchestrator exposes `agentflow --list-skills` and injects a chosen
  skill's instructions into the relevant step's prompt. Keep it
  hand-rolled - no third-party skill runtime.

## Phase N - Requirements clarification + build review (2026-09-02)

**Requested by the user.** Changed the default workflow from
`goal -> review -> build -> verify -> test` to a requirements-driven flow:

`goal -> review/plan -> (requirements unclear? ask user -> re-review) -> build
-> review-build -> verify/test`

### Fold-in requirements assessment (review step)

- `REVIEW_PROMPT` now also instructs the reviewer to list any ambiguous /
  under-specified requirements as numbered questions and to end with a
  `REQUIREMENTS_CLEAR: yes|no` line (in addition to `BUILD_NEEDED`).
- New `parse_and_strip_requirements_clear()` parses that marker.
- After the review/plan step, if `REQUIREMENTS_CLEAR: no`, the orchestrator
  asks the user for answers (event-driven), folds them in, and **re-reviews**
  the plan with the new requirements — cycling until clear or
  `max_requirements_rounds` is reached. `0` disables the loop. The happy path
  (`clear`) adds no extra backend call.
- The requirements prompt is folded into review, so no new role/backend config
  was added; it reuses the review backend.

### Wait / resume ("block then resume")

- `_wait_for_requirements_answer()`:
  - **Web / background run:** polls the DB queue for an unconsumed
    `answer` message (the composer sends `kind: answer` while awaiting
    requirements) or a `steer`, aborts on a stop signal.
  - **CLI:** an interactive `requirements_responder` prints the questions and
    reads `input()` (skips when stdin is not a TTY).
  - **TUI:** surfaces the questions and proceeds (inline prompt in the worker
    thread conflicts with the REPL — recorded as a follow-up).
- Messages endpoint accepts `kind: steer|note|answer`; new SSE event types
  `requirements_pending` / `requirements_answer`; the web composer shows
  "awaiting your requirements" and sends `answer`.

### Build review step (config-gated)

- New `BUILD_REVIEW_PROMPT` runs after a successful build and before verify
  (reuses the review backend, `mode=read`, role `build_review`). It reviews the
  actual diff vs the plan/requirements but does NOT run tests (verify still
  does).
- Non-fatal: a build-review failure does not block verification.

### Config / CLI / web surface

- `Config.max_requirements_rounds: int = 3`, `Config.build_review: bool`
  (model default `False`; the shipped `agentflow.config.yaml` and example set
  `build_review: true` so real runs review the build). `load_config` /
  `dump_config` / env overrides updated.
- Tool-call caps are configurable too: `Config.max_tool_calls: int = 10`
  (build/verify) and `Config.max_read_tool_calls: int = 40`
  (review/requirements/build-review). `load_config` / `dump_config` / env
  overrides (`AGENTFLOW_MAX_TOOL_CALLS`, `AGENTFLOW_MAX_READ_TOOL_CALLS`)
  updated.
- CLI: `--max-requirements-rounds <n>`, `--no-build-review`,
  `--max-tool-calls <n>`, `--max-read-tool-calls <n>`.
- Web config panel: "Clarify Rounds", "Max Tool Calls", "Max Read Calls"
  inputs + "Review build before verify" toggle, wired through `POST/PUT
  /api/config`.
- TUI `/config` shows the new fields.

### Review read-cap fix (from live test)

A live re-test (`run-20260902-160621`) showed the **review** step faulting with
"Reached the maximum of 10 tool calls without a final response": the reviewer
was legitimately exploring a multi-file repo (read `app.py`, `db.py`,
`schema.sql`, templates, static, git status, ls) but hit the old 10-call cap
before writing its analysis. Changes:
- Read-only steps (review, requirements, build-review) now allow
  `max_read_tool_calls` (default 40); build/verify stay at `max_tool_calls`
  (default 10) since they're write/test-gated.
- Added a read-only re-read guard: re-requesting the exact same read 3+ times in
  one step stops the run (catches spinner loops that aren't identical-consecutive).

### Verification

- 405 tests pass (400 prior + 5 new in `tests/test_requirements_flow.py`
  covering: ask-then-proceed, rounds cap, clear-first-pass (no extra calls),
  build-review recorded when enabled, build-review skipped when disabled).
- Impeccable detector clean on the changed web files.

### Follow-ups

- TUI inline requirement prompt (currently surfaces + proceeds).
- The requirements `REQUIREMENTS_CLEAR` marker maturity depends on the
  backend actually emitting it; a backend that omits it proceeds as before.

## Phase O - Chat-first run history with live updates (2026-09-02)

**Requested by the user after reviewing `run-20260902` session #2.**
Current web console keeps the assistant's reasoning in the step cards, but the
user has to click the timeline/playhead to read past analysis, and the page
must be refreshed to see new events. Phase O makes the main thread panel a
first-class chat history while keeping the timeline as secondary navigation.

### Goal

The run detail page should feel like Claude Code / OpenCode / Antigravity:
assistant reasoning appears as scrolling chat bubbles, file changes are visible
inside each turn, and live runs update automatically without a manual refresh.

### Problems to solve

1. **History is behind the timeline.** Completed steps are rendered as
   collapsed cards; the rich analysis the user wants to re-read requires
   clicking a timeline segment.
2. **Refresh required for updates.** SSE events arrive, but the main panel
   does not auto-append + auto-scroll, so the user reloads the page.
3. **Tool calls dominate the view.** A separate "Tool Calls" wall sits below
   the steps, pulling attention away from the assistant's prose and the files
   it changed.

### Design

1. **Chat-bubble thread as the primary view**
   - Each completed step becomes a bubble with:
     - Role avatar/label (`Review`, `Build`, `Verify`, `Build review`,
       `Requirements`).
     - Rendered Markdown prose (or the synthesized no-prose summary from
       `_record`).
     - A compact "Changes" footer: files written/edited, count of tool calls,
       and key calls (`WriteFile src/foo.py`, `Shell pytest`, etc.).
   - User messages (`user_message` events, steer/answer/note) render as
     right-aligned user bubbles so the conversation turn structure is obvious.
   - Bubbles are stacked chronologically; the timeline above becomes a
     scrubber that scrolls/jumps to a step rather than a separate content view.

2. **Timeline stays, but secondary**
   - Keep the top timeline as a run-overview / playhead.
   - Clicking a segment scrolls the chat to that step and highlights it.
   - Remove the current behavior where the timeline is the only way to see
     step prose.

3. **Live auto-update + auto-scroll**
   - On `text_delta`, append to the active bubble's prose and scroll the thread
     to the bottom if the user is already near the bottom (standard
     "following" behavior).
   - On `step_started` / `step_finished` / `tool_call` / `tool_result`, insert
     or update the relevant bubble without a full page reload.
   - Add a subtle "Following" / "Paused" indicator when the user scrolls up
     manually.

4. **Collapsible tool detail inside bubbles**
   - Each assistant bubble shows a one-line summary of tools used.
   - Expand within the bubble to see arguments/output, instead of the global
     "Tool Calls" section.
   - File-write bubbles can show a mini diff inline.

5. **Empty-step friendliness**
   - Reuse the improved `_record` summaries from Phase N, but render them as
     normal bubble text (not a faint italic aside).
   - Add an icon/color cue so the user can tell "this step had no prose but
     did work" at a glance.

### Task breakdown

1. **Data plumbing**
   - Ensure `GET /api/runs/{id}/events` returns all event types the UI needs,
     including `text_delta`, `step_started`, `step_finished`, `tool_call`,
     `tool_result`, `user_message`, `requirements_pending`, `requirements_answer`,
     `blocker`, `run_finished`.
   - Add or reuse a per-step tool/file-change summary endpoint or compute it
     client-side from the existing event stream.

2. **Front-end refactor (`src/agentflow/web/static/app.js`)**
   - Replace the current `renderThreadContent` step-card list with a
     `renderChatThread` function that emits bubbles.
   - Implement `renderAssistantBubble(step)` and `renderUserBubble(msg)`.
   - Add `renderChangesFooter(step)` summarizing files written/edited and
     tool-call count.
   - Implement follow-scroll logic and a manual-scroll pause detector.
   - Retain timeline click handlers but make them scroll-to-step.

3. **Styling (`src/agentflow/web/static/console.css`)**
   - Chat bubble tokens (background, border-radius, max-width, padding) using
     existing DESIGN.md color tokens.
   - User bubble vs assistant bubble distinction.
   - Live-step pulsing/amber accent already added in Phase N; refine for the
     bubble layout.

4. **SSE/live reliability**
   - Confirm `EventSource` reconnects on error and resumes from the last seen
     event id/seq.
   - Ensure newly created runs immediately open the SSE stream and start
     rendering bubbles.

5. **Tests**
   - Update `tests/test_web.py` render assertions if any fixture text changed.
   - New unit tests for `renderChangesFooter` (or backend equivalent) using
     mocked step + tool_call fixtures.
   - `uv run pytest` must stay green.

### Success criteria

- A completed run's analysis is readable by scrolling the chat thread; no
  timeline click required.
- A live run appends new assistant/user bubbles and scrolls automatically.
- File changes are visible per turn without opening a separate tool-call wall.
- Tool calls are grouped per step under a single collapsible toggle.
- The timeline is removed from the web UI.
- `uv run pytest` passes; Impeccable detector clean.

## Phase P - Conversational workflow UI (do next)

**Requested by the user after Phase O review.**

Goal: make the web console explicitly guide the user through a
requirements-driven, multi-review workflow where every AI response is visible
without scrubbing, and the user is asked at each decision gate.

### Desired flow

1. **Goal** — user submits a task in the composer.
2. **Review / requirements** — AI reviews the goal and gives feedback.
   - If requirements are incomplete or ambiguous, AI asks clarifying questions.
   - The user answers by selecting from options or typing their preference.
   - Loop back to review with the new context until requirements are clear
     (reuse Phase N's `max_requirements_rounds` / `requirements_pending` flow).
3. **Plan** — once requirements are clear, AI produces a plan and displays it
   as a visible assistant bubble.
4. **Plan review** — a reviewer (can reuse the review backend in `read` mode)
   checks the plan against requirements.
   - If the plan is rejected, loop back to review or ask the user.
   - If approved, proceed.
5. **Build** — AI executes the approved plan (reuse Phase N's `build_review`).
6. **Build review** — AI reviews the actual diff/result against the plan.
   - If rejected, loop back to build with feedback or ask the user.
   - If approved, proceed.
7. **Verify / test** — AI runs tests/verification against the plan.
   - If it fails, loop back to build with feedback (existing `max_iterations`).
   - If it passes, proceed.
8. **Summary** — show a concise summary bubble of what was done, files changed,
   cost, and final status.
9. **What's next** — prompt the user for a follow-up task or let them start a
   new turn in the same session.

### Why this is a distinct phase

Most of the backend logic already exists (Phase N requirements loop, Phase N
build review, Phase H/I tool loop, Phase O chat UI). Phase P is about:
- **UI clarity**: labeling each gate explicitly ("Review", "Plan", "Plan review",
  "Build", "Build review", "Verify", "Summary", "What's next").
- **User interruption at decision gates**: the composer changes placeholder and
  the input is routed as `answer` / `steer` at the right checkpoints.
- **No hidden state**: every AI response is rendered in the chat thread as it
  happens; no timeline scrubbing needed.
- **Summary / next-action prompt**: a final assistant bubble that summarizes the
  run and asks the user what to do next.

### Task breakdown

1. **Label steps clearly in the UI**
   - Map `role` values to human gate labels: `review` → "Review", `build` →
     "Build", `verify` → "Verify/Test", `build_review` → "Build review",
     `requirements` → "Clarify requirements".
   - Render a gate label in each assistant bubble header.

2. **Expose the plan as its own bubble**
   - When the review backend emits a plan (the prose before `BUILD_NEEDED:`),
     render it as a dedicated "Plan" bubble between "Review" and "Build".
   - This may require a new event type (`plan`) or parsing the review step text
     client-side; prefer backend emitting a `plan` event if ambiguous.

3. **Decision-gate composer states**
   - `requirements_pending`: composer placeholder becomes "answer the
     requirements question…" and sends `kind: answer`.
   - After a plan is shown: composer placeholder becomes "approve this plan or
     ask for changes…" and sends `kind: steer`.
   - After build review: composer placeholder becomes "approve the build or ask
     for changes…".
   - After verify failure: composer placeholder becomes "verify failed — steer
     the next build iteration…".
   - After summary: composer placeholder becomes "what's next?".

4. **Summary bubble**
   - On `run_finished`, render a final "Summary" assistant bubble with:
     - Goal
     - Files changed
     - Final status (pushed / completed / stopped / failed)
     - Total cost & duration
     - "What would you like to do next?"

5. **Backend polish**
   - Ensure `BUILD_REVIEW_PROMPT` and `REVIEW_PROMPT` emit clear
     approve/reject markers that the UI can surface.
   - Consider emitting explicit `plan`, `plan_review`, `build_review` events so
     the UI doesn't have to heuristically label steps.

### Success criteria

- Every AI response is visible in the chat thread without timeline interaction.
- Requirements questions, plan approval, build review, and verify failure all
  surface clear composer prompts.
- A summary bubble appears at the end of every run.
- The composer is always ready for the next user turn after a run finishes.
- `uv run pytest` passes.

## Phase M - Release & distribution (Linux only, for now)

**Requested 2026-08-29. Not yet implemented.**

**Goal:** a one-liner install on Linux -
`curl -fsSL https://rem029.agentflow.com/install.sh | sh` - plus tagged
GitHub Releases with downloadable artifacts. Linux `x86_64` + `aarch64` only
for the first release; macOS/Windows later.

**Why it's feasible cheaply:** every dependency (`google-antigravity`, `mcp`,
`fastapi`, `pydantic`, `rich`, `prompt_toolkit`, `pyyaml`, `httpx`,
`uvicorn`) ships as a pure-Python wheel - no compiled extensions - so a
single built wheel runs on any Linux with a Python 3.11+ runtime. The heavy
lifting (Python provisioning, isolated env, dependency resolution, PATH
shim) is all handled by `uv tool install`, which the script just bootstraps.
The actual LLM backends (`claude`, `agy`) are the user's own separate
installs and out of scope here.

### M1 - Packaging

- `pyproject.toml` for real distribution: confirm the project `name`
  (PyPI-safe; `agentflow` if the name is free, else `rem029-agentflow` -
  affects every install URL), single-source `version`, `license`, `readme`,
  classifiers, `requires-python = ">=3.11"`. The `[project.scripts]`
  `agentflow = "agentflow:main"` entry point already exists.
- Verify `uv build` includes the vendored web assets
  (`src/agentflow/web/static/*`) and `tests/fixtures/fake_mcp_server.py` is
  NOT shipped. `uv_build` is already the backend; check the wheel contents
  (`unzip -l dist/*.whl`).
- `uv build` -> `dist/agentflow-<v>-py3-none-any.whl` + `dist/*.tar.gz`.
- Decide: publish to PyPI as well? Not required for the `install.sh` path
  (which pulls the wheel straight from the GitHub Release), but it makes
  `uv tool install agentflow` / `pipx install agentflow` work for everyone.
  If yes, reserve the name now.

### M2 - GitHub Release automation

- `.github/workflows/release.yml`: trigger on pushing a `v*` tag.
  Steps: checkout, `astral-sh/setup-uv`, `uv build`, generate `SHA256SUMS`,
  `gh release create v<x> dist/* SHA256SUMS install.sh --generate-notes`.
- Guard: fail the job if the tag != `project.version` in `pyproject.toml`.
- Release notes: `--generate-notes` plus a hand-curated summary pulled from
  the PLAN "Phase N done" lines since the last tag.
- Artifacts on the release: the wheel, the sdist, `SHA256SUMS`, and a copy
  of `install.sh` (so a release-pinned script URL exists).

### M3 - `install.sh` (repo root)

POSIX `sh`, `set -eu`, no bashisms. Flow:

1. `uname -s` must be `Linux` (else print "only Linux is supported right
   now" and exit 1). `uname -m` -> `x86_64` | `aarch64`; anything else exits
   with a clear message. (The wheel is arch-independent, but the check keeps
   the promise honest and lets a future arch-specific asset slot in.)
2. Need one of `curl` / `wget`, plus `sh`, `uname`, `mktemp` - all base.
3. Ensure `uv`: if absent, install via `curl -LsSf https://astral.sh/uv/install.sh | sh`
   (or honour `AGENTFLOW_NO_UV_BOOTSTRAP=1` and error out). `uv` provides its
   own Python, so that's the only prerequisite.
4. Resolve the version: `AGENTFLOW_VERSION` env if set, else the latest tag
   from `https://api.github.com/repos/<owner>/<repo>/releases/latest`.
5. `uv tool install --force agentflow \
     --from https://github.com/<owner>/<repo>/releases/download/v<v>/agentflow-<v>-py3-none-any.whl`
   (`--force` makes re-runs an upgrade). Optionally verify the wheel against
   `SHA256SUMS` first.
6. Check `~/.local/bin` (or `uv tool dir`'s bin) is on `PATH`; if not, print
   the exact `export PATH=...` line and the shell-rc snippet.
7. `agentflow --version` sanity check; then print "next: `agentflow --check`".

- Idempotent, safe to pipe from `curl`. Keep it short and auditable.
- `agentflow --version` already exists and prints
  `importlib.metadata.version("agentflow")` (so the packaged dist name is
  `agentflow` - if that's taken on PyPI and we publish there, this call and
  the dist name both change together).

### M4 - Where to host `install.sh`

- **Source of truth:** `install.sh` in the repo root - versioned, code-reviewed,
  attached to each release by M2.
- **Served at `https://rem029.agentflow.com/install.sh` via, in order of
  preference:**
  1. **GitHub Pages** on this repo (a `/docs` dir or a `gh-pages` branch)
     with a `CNAME` file containing `rem029.agentflow.com`, and a DNS
     `CNAME rem029 -> <owner>.github.io`. Free, automatic HTTPS, no server to
     run, updates on push. The release job copies the tagged `install.sh` to
     the Pages source so the hosted script is always a released version.
     **Recommended.**
  2. **The existing Coolify host** (already serving `agentui.app.rem029.com`
     for this project) - add a tiny static route / nginx container serving
     `install.sh` from a checkout. Uses infra already operated; good if the
     script should track a branch without a Pages deploy. Second choice.
  3. **Cloudflare Pages / Workers** if `agentflow.com` DNS is already on
     Cloudflare - same upsides as GitHub Pages plus redirect control.
  4. **No custom domain / zero setup:** document
     `https://raw.githubusercontent.com/<owner>/<repo>/v<v>/install.sh`
     directly - works today, just an uglier URL.
- **Release artifacts** (wheel, sdist, checksums) are always hosted by
  GitHub Releases - no decision needed there.

### M5 - Docs

- README "Install" section: the `curl | sh` one-liner, the manual
  `uv tool install` path, and the "grab the wheel from Releases" path.
- Security note: reading the script before piping to `sh`, verifying
  `SHA256SUMS`, and pinning `AGENTFLOW_VERSION`.

### Open questions for the user

- Do you control `agentflow.com` / the `rem029.agentflow.com` DNS record?
  (Decides M4: option 1/3 need it, option 2 can use `*.rem029.com`.)
- Publish to PyPI too, or GitHub Releases only?
- Will the repo stay `rem029/test-ai-agent` or be renamed to
  `rem029/agentflow` before the first release? Every install URL depends on
  this - worth settling first.
- Confirm `aarch64` matters for v1, or is `x86_64`-only fine to start?

## Housekeeping

- Gitignore `.agentflow-test-todo/` (a stray workflow output artifact committed
  to the repo root).
- Credential handling: secrets moved from `agentflow.config.yaml` to `.env` (env vars only). Added `.env.example` and hand-rolled `agentflow.dotenv` loader/writer.

