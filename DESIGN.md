---
name: agentflow
description: "Transport — a run is a recording you scrub, on an emissive monospace console for watching autonomous agent workflows"
seed: dfedc3e0
colors:
  bg-ground: "#0b0d10"
  bg-panel: "#12151a"
  bg-raised: "#171b21"
  bg-hover: "#1c2128"
  bg-active: "#222730"
  border-hairline: "#23272e"
  border-subtle: "#2d333b"
  text-primary: "#e6e8ea"
  text-secondary: "#8b939c"
  text-faint: "#5c636b"
  accent-amber: "#ffb020"
  status-red: "#e5484d"
  diff-add-bg: "rgba(63,185,80,0.09)"
  diff-del-bg: "rgba(248,81,73,0.09)"
typography:
  mono:
    fontFamily: "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace"
    fontSize: "13px"
    lineHeight: "1.5rem"
scale:
  micro: "11px"
  small: "12px"
  body: "13px"
  head: "14px"
rounded:
  none: "0"
  input: "2px"
  pill: "9999px"
spacing:
  unit-x: "1ch"
  unit-y: "1.5rem"
  xs: "0.4rem"
  sm: "0.6rem"
  md: "1rem"
  lg: "1.5rem"
---

## Overview

The agentflow web console is a dark-only operator surface for watching a
multi-agent run unfold — review → build → verify → iterate → push. It is not a
chat log with a status badge; it treats a run as a **recording on a transport**.
A shared timeline is the spine, a draggable playhead scrubs the persisted event
log, and a docked transport bar (stop / steer / live cost) is always present
like a mixing desk's master section. Zero build step, zero runtime CDN, one
self-hosted monospace face.

Direction locked from the Impeccable `operate` concept roll, seed `dfedc3e0`,
candidate 7 of 7. Full contract in the HTML comment at the top of
`src/agentflow/web/static/index.html`.

## Scene, not category

Dark is chosen from the use scene, not convention: a developer watching a
long-running agent build, often at night, with a terminal open beside this tab.
There is no light theme and no theme toggle.

## Colors

- **Ground**: near-black emissive `#0b0d10`; panels `#12151a`, raised
  `#171b21`. Surfaces separate by a 1px hairline (`#23272e`), never by shadow.
- **Text**: `#e6e8ea` primary, `#8b939c` secondary (tinted toward the ground's
  cool hue — never neutral gray), `#5c636b` faint.
- **One signal — amber `#ffb020`.** Reserved for the single live element and
  nothing else: the running lane's pulsing edge, the playhead dot, the
  `⟩ live` return control while scrubbed on an active run, `:focus-visible`
  rings, the input caret, `::selection`, and the primary action button
  (`send`, `save config`). Amber never colors links, headings, tool names,
  session titles, or any static label.
- **Status is lane treatment + a glyph, not a pill.** `done` → dim neutral
  fill (`#20242b` track / `#3a414b` fill), `✓` in secondary text. `running` →
  amber pulsing edge. `failed` → hatched fill, `!`. `queued` → empty lane,
  `·`. `stopped` → fill stops mid-lane, `▪`. `interrupted` → static, no clock.
- **Red `#e5484d`** appears only for a fatal blocker banner and a verify-FAIL
  verdict. **Green is not used** as a status color; a completed run reads
  neutral. A faint green/red tint distinguishes added/removed lines in diffs.

## Typography

- **One face: JetBrains Mono** (self-hosted woff2, weights 400 / 500 / 700,
  latin subset, `static/fonts/`, OFL). The whole console — labels, prose,
  telemetry, headings — is on it. No UI sans.
- Body `13px` / `1.5rem` line. Steps on `11–14px`. All numeric and time
  values carry `font-variant-numeric: tabular-nums`.
- Weight and case carry hierarchy: tracked lowercase-caps for region labels
  (`SESSIONS`, panel titles), 500–700 for active/heading rows, 400 for body.
  There is no large display type — this is an instrument panel.

## The cell grid

Horizontal rhythm is in `ch`, vertical in a fixed `--line: 1.5rem`. Timeline
lane labels, playhead ticks, cost columns, list rows and log lines all resolve
to the same column lattice, so the console reads as one ruled surface. This is
the teletext / terminal-grid discipline carried through from the concept
roll's challengers.

## Layout

```
topbar (2.5rem)  ── agentflow · [project] · session · "goal…" (hover = full)
├── session rail (26ch) ──┬── timeline area (full width) ───────────────────
│  › active  · inactive   │   review / build / verify lanes + playhead ruler
│  n runs · $cost         │  ─────────────────────────────────────────────
│  + new session          │   thread (centered, max 90ch, playhead-bound)
│                         │     GOAL header (full untruncated prompt)
│                         │     step blocks · tool calls · diffs · blockers
├─────────────────────────┴── transport bar (3.5rem, docked) ──────────────
   ⏹ stop   ⏭ steer / describe a run…   send      <state> · <dur> · $<cost>
```

- **Timeline** is sticky; the thread scrolls under it.
- **Playhead**: a `range` slider styled to the world; dragging / arrows / click
  rebind the thread to the event at that position; a `⟩ live` snap returns to
  the edge. New SSE events snap the playhead forward unless the user has
  scrubbed back.
- **Thread** is centered in the stage at a ~90ch measure; the timeline above
  spans full width.
- **Overlay panels** (Config, Tools, MCP) are in-flow `position: fixed`
  columns on the right edge — no scrim, no backdrop-filter, the console stays
  legible behind them. Esc closes.
- **Command palette** (`⌘K`): a focused input dropping from the top with a
  fuzzy action list — the web equivalent of the REPL prompt and its slash
  commands (`model`, `config`, `tools`, `mcp`, `resume <session>`, `cost`,
  `clear`, `stop`).

## Elevation & shape

Flat. Separation is a 1px hairline or a background-value step, never a shadow.
`border-radius: 0` on every container; `2px` only on inputs and buttons; `pill`
only where a true toggle needs it. No card has another card inside it.

## Motion

- The playhead advances on a shared real-time clock; the running lane's edge
  carries a 1px amber pulse; lane fill grows left-to-right. **Everything else
  is still.**
- All keyframe animation is gated behind `body.run-live` (added only while a
  run is actually executing) and `@media (prefers-reduced-motion:
  no-preference)`. On an idle, finished, or interrupted view
  `document.getAnimations()` is empty.
- `prefers-reduced-motion: reduce` freezes the pulse and playhead advance
  (static fill kept) and disables panel transitions.

## Iconography vs. state glyphs (deliberate)

- **Interactive controls** use the hand-authored SVG set (one 1.5px stroke,
  16px grid): `#icon-stop`, `#icon-play`, `#icon-pause`, `#icon-skip`,
  `#icon-chevron-*`, `#icon-close`, `#icon-wrench`, `#icon-plug`, `#icon-bell`,
  `#icon-folder`, `#icon-menu`, `#icon-plus`, `#icon-trash`, `#icon-alert`,
  `#icon-check`.
- **Timeline / status state marks** stay as monospace cell glyphs
  (`✓ ! ▪ · ⠋`) and the `›` active-session marker. In this committed
  terminal-grid world they are a notation that holds tabular alignment, not
  glyph-for-icon substitution. Nowhere else are unicode glyphs used as icons.

## Browser surfaces

Themed from the palette, not left to defaults: `::selection` (amber-dim),
`caret-color` amber, thin custom scrollbars (`#23272e` track / `#3a4048`
thumb), `:focus-visible` 2px amber outline with 1px offset.

## Components

- **Session rail row**: `‹marker› <title>` + a dim `<n runs · $total>` line;
  `title=` carries the full untruncated goal. Active row: amber `›`, primary
  text, subtle left border.
- **GOAL header** ("tape label"): pinned at the top of the thread — the full,
  wrapped, selectable prompt under a faint `GOAL` label, then a meta line:
  run id · state · duration · cost · `review <backend·model> · build … ·
  verify …`. Hairline bottom border, no card.
- **Step block**: collapsible; header `▾ <role> · <backend> · <model> ·
  $<cost> [· iter N] [· PASS/FAIL]`. A no-response step renders as a thin
  dimmed line (`opacity .55`), not a full card, so real content dominates.
- **Tool call**: one collapsed line `▸ <ToolName> <primary arg> …<result
  summary>`; expands to formatted args and — for a file write — an inline diff
  at a **fixed height** so iteration 1 and iteration 3 compare at a glance.
  Shell shows a capped `<pre>` + exit code. A parse-failed block becomes a
  `requested: <ToolName>` chip — raw `<tool_call>` XML never reaches the prose.
- **Transport bar**: `stop` is disabled/dim unless a run is live (no red when
  idle); the steer input doubles as the start-a-run input when nothing is
  active; the right side shows the state word, a real duration (`MM:SS`, or
  `Nh MMm` past an hour — one shared formatter with the playhead ruler), and
  running cost, with the braille spinner only while live.
- **Overlay panels**: hairline left border, own scroll, mono form controls.
  Config carries per-role backend + model, max iterations, permissions,
  `max_cost_usd` (empty clears it), a masked OpenRouter key indicator
  (`sk-or…c064`, write-only replace), global + project memory, email
  notifications, and the full **MCP servers editor** (name, stdio-command /
  SSE-URL, args, `KEY=VALUE` env, enabled, auto-approve). Tools panel: a
  read-only `name / RO·RW / description` table. MCP panel: per-server
  connected / error / disabled status with a wrapping tool-chip list and a
  recheck button.

## Responsive

One breakpoint at `700px`: the topbar drops the session title, timeline lanes
shrink to one line, overlay panels go full-width, and the transport bar wraps
with the status readout on its own row above the input. The session rail
becomes a **hidden-by-default drawer**: hidden on first viewport so the active
run owns the screen, opened by the `☰` toggle behind an on-hue `--scrim`
backdrop, closed by tapping the backdrop, the header `✕`, `Escape`, or
selecting a session. The toggle and close carry `aria-expanded`/`aria-controls`.
Session rows grow to ≥44px tall, use `:active` (touch) feedback instead of
hover-only, and truncate to two lines rather than one. The viewport meta uses
`viewport-fit=cover` with `env(safe-area-inset-*)` padding on the topbar and
transport so controls clear the notch/home indicator, and text inputs render at
16px to prevent the iOS focus-zoom.

## Do / don't

- **Do** keep amber for the one live element; **don't** spend it on links,
  headings, or static labels.
- **Do** gate every animation behind `body.run-live`; **don't** animate an
  idle or finished view.
- **Do** show the full goal in the GOAL header and on hover; **don't** leave a
  run with only truncated context.
- **Don't** reintroduce cards, shadows, rounded containers, a light theme, or
  a second typeface.

## Known follow-ups (not blockers)

- Interactive web permission prompts: with `permissions: prompt`, the headless
  web thread still denies mutating tools (the SSE stream has no permission
  round-trip yet).
- No session-level SSE stream; the sessions list is refetched.
- No queue-management UI (cancel / reorder queued runs).
- Mobile (~320–430px) navigation, drawer, touch targets and safe-area padding
  are implemented but were not render-verified in the sandbox this session
  (the container had no launchable browser); confirm on a real device or the
  hosted URL.
