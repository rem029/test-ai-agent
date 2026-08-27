---
name: agentflow
description: Clean, high-contrast, developer-focused admin panel for local multi-agent workflows
colors:
  primary: "#2563eb"
  primary-hover: "#1d4ed8"
  bg: "#f8fafc"
  surface: "#ffffff"
  fg: "#0f172a"
  muted: "#64748b"
  border: "#e2e8f0"
  ok: "#16a34a"
  ok-bg: "#dcfce7"
  fail: "#dc2626"
  fail-bg: "#fee2e2"
  warn: "#d97706"
  warn-bg: "#fef3c7"
typography:
  body:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: "14px"
    lineHeight: "1.5"
  mono:
    fontFamily: "ui-monospace, 'SF Mono', Menlo, Consolas, monospace"
rounded:
  sm: "4px"
  md: "8px"
  pill: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
---

## Overview
agentflow's web admin panel is designed for clarity, high contrast, fast scanability, and density appropriate for developer tooling. It operates in both light and dark environments with zero runtime external dependencies.

## Colors
- **Foreground / Background**: Deep slate on soft light background for light mode; high-contrast off-white on deep slate/navy for dark mode.
- **Surface**: Pure white cards in light mode, elevated slate container surfaces in dark mode.
- **Status & Feedback**:
  - `Running`: Warm amber pill and highlight.
  - `Pushed / OK`: Vibrant emerald green pill and highlight.
  - `Failed / Not Pushed`: Crisp red pill and alert state.
- **Accent**: Refined royal blue for interactive elements, links, and primary actions.

## Typography
- **UI & Content**: Clean system sans-serif hierarchy (`system-ui, -apple-system, sans-serif`).
- **Code, Run IDs & Data**: Monospaced font (`ui-monospace, Menlo, monospace`) with tabular figures (`font-variant-numeric: tabular-nums`) for aligned numerical data, timestamps, and commit hashes.
- **Scale**:
  - Page Titles (h1): 1.5rem, bold (700).
  - Section Headings (h2): 1.15rem, semi-bold (600).
  - Body: 0.925rem (14.8px), regular (400).
  - Captions / Meta / Badges: 0.75rem – 0.85rem.

## Layout
- Centered container constrained to 960px max width for optimal line lengths and scanability.
- Header with clear branding, navigation items, and active route indication.
- Consistent vertical rhythm with 24px–32px separation between major sections.
- Responsive table design with horizontal scroll containers when viewports are constrained.

## Elevation & Depth
- Flat, modern surface separation using subtle 1px border lines and faint ambient elevation (`0 1px 3px rgba(0,0,0,0.05)`).
- Clear contrast hierarchy between the page background and content cards.

## Shapes
- Input controls, buttons, cards, and banners use a 6px–8px radius.
- Status badges use pill shapes (rounded 9999px).

## Components
- **Banners**: Used for active run alerts and action confirmations with tinted backgrounds and icons.
- **Tables**: Clean border-bottom dividers, bold headers with subtle background, zebra hover states, and monospaced cells for identifiers and costs.
- **Forms**: Vertical stack with clear labels, helpful focus outlines (`focus-visible: 2px solid var(--accent)`), and distinct fieldsets.
- **Step Logs**: Collapsible step views with structured headers (Role, Mode, Backend, Cost, Status) and expandable preformatted output.

## Do's and Don'ts
- **Do** format run IDs, hashes, and costs with monospaced typography.
- **Do** ensure focus rings are visible on all interactive elements.
- **Don't** add decorative animations or heavy layout shifts that distract from real-time monitoring.
- **Don't** use low-contrast grays for secondary text or status labels.

## Tool Integration

The web UI surfaces tool calls as first-class events in the run detail view:

- **Tool call timeline**: a chronological list of every tool invoked during a
  step, showing tool name, status (success/failure), and execution time.
- **Expandable output**: large outputs (file contents, diffs, command output)
  are collapsed by default and expand on demand.
- **Visual diff viewer**: file changes are rendered with added/removed line
  highlighting and links to the full file content.
- **Real-time updates**: the run detail page polls the `/api/runs/{run_id}`
  endpoint so new tool calls appear as the orchestrator executes them.

Tool calls are persisted in the run state JSON and in a dedicated
`tool_calls` SQLite table for querying and auditing. The orchestrator remains
in control of the workflow: it parses tool requests from agent responses,
executes the tools, and returns the results.
