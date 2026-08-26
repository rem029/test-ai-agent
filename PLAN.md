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
- Every step's `Usage` is persisted to `.agentflow/runs/<id>.json`
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
browser, and editing `agentflow.config.yaml` from the browser. Full design
in `plan-web-ui.md`. Built on `phase-d`, branched from `dev`:

- **Stack**: FastAPI + Jinja2 + htmx (vendored locally, no CDN at runtime),
  uvicorn dev server, bound to `127.0.0.1` by default, no auth (personal
  single-user tool). New CLI flags: `agentflow --serve [--host] [--port]`.
  Dev deployment note (this project runs inside a Coolify-hosted
  code-server): reachable at `https://agentui.app.rem029.com/` when started
  with `--host 0.0.0.0 --port 4200` — see `plan-web-ui.md`, "Development
  environment", for why `0.0.0.0` is needed here specifically.
- **Live progress**: htmx polls a run's `/fragment` endpoint every 2s, which
  just re-reads the same `.agentflow/runs/<id>.json` `orchestrator.py`
  already writes incrementally — polling stops itself once `finished_at` is
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
  `agentflow.config.yaml`) all render and work correctly.

### Phase E — Design polish pass (Impeccable) (not started)

Separate from Phase D's core functionality: a polish pass over what got
built, using [Impeccable](https://impeccable.style) (installed as a Claude
Code, Antigravity, and Gemini CLI skill via `npx impeccable install` — see
`.claude/skills/impeccable/`). `/impeccable init` hasn't been run yet to set
up its design context (`PRODUCT.md`/`DESIGN.md`), so this phase hasn't
actually started.

- **Web UI** — the natural target: `dashboard.html`, `run_detail.html`,
  `_run_fragment.html`, `config_edit.html`, and `style.css` under
  `src/agentflow/web/templates/` and `static/`. Impeccable reads
  `PRODUCT.md`/`DESIGN.md` before making targeted alignment/spacing/
  typography/color-consistency suggestions, so `/impeccable init` runs
  first, then something like `/impeccable polish the dashboard and run
  detail pages` / `/impeccable audit` against the templates.
- **CLI** — "if possible": Impeccable is built for visual/DOM surfaces (it
  screenshots and inspects rendered HTML), so it has no direct notion of a
  terminal's output. Worth trying `/impeccable audit` pointed at `cli.py`'s
  `--help`/error text anyway to see whether its copy-clarity commands
  (`clarify`, `distill`) produce anything useful on plain text — but don't
  expect the same kind of result as the web UI gets, and fall back to
  manual review of the help strings/error messages if it doesn't.
- A hook Impeccable's installer added (`.claude/settings.local.json`) runs a
  design-detector script after every `Edit`/`Write` and on session `Stop` —
  already active project-wide, not something this phase needs to set up.

## Status

Phases A, B, and C are done and merged into `dev`. Phase D (local admin web
UI) is implemented on `phase-d`, branched from `dev`, and not yet merged —
left for the user to review first, matching the merge-before-next-phase
workflow used for prior phases. See `plan-web-ui.md` for its design. Phase E
(design polish via Impeccable) is planned but not started. Also outstanding:
the "Open items before implementation" list above (Antigravity CLI headless
flags unconfirmed, default backend-per-role mapping undecided, commit
message template undefined, push branch convention unconfirmed).
