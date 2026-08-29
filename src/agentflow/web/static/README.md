# Static Assets for AgentFlow Web Console

These files implement the "Transport" visual direction for the agentflow web SPA, served by the FastAPI app in `src/agentflow/web/app.py`.

- `index.html` — single-page master console layout with embedded direction contract and SVG icon symbols.
- `console.css` — dark-only Transport theme styling with near-black emissive ground, single amber signal accent, monospace cell grid, hairline borders, and responsive rules.
- `app.js` — vanilla JavaScript orchestrating the session rail, playhead scrubbed timeline, SSE live streaming via `EventSource`, inline diff rendering, docked transport bar, `@`-file mention completer, command palette (⌘K), and slide-in overlay panels.
- `md.js` — zero-dependency safe Markdown-to-HTML parser.
- `fonts/` — vendored JetBrains Mono webfonts (`woff2` formats: Regular 400, Medium 500, Bold 700) and the Open Font License (`OFL.txt`).
