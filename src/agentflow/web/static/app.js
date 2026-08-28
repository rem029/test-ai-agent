// ------------------------------------------------------------------
// AgentFlow Web UI - Enhanced Frontend
// ------------------------------------------------------------------

const RUNS_PAGE_SIZE = 25;
let currentRunsPage = 1;

if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => {
        // Initialize theme
        loadThemeFromStorage();

        // Set up tab switching
        setupTabs();

        // Form submission handlers
        document.getElementById('config-form').addEventListener('submit', submitConfig);
        document.getElementById('run-form').addEventListener('submit', submitRun);
        document.getElementById('back-to-runs').addEventListener('click', (e) => {
            e.preventDefault();
            navigate('/runs');
        });

        // Theme toggle
        document.getElementById('theme-toggle').addEventListener('click', toggleTheme);

        // Load backend dropdowns with models
        populateModelOptions();

        // Router setup & initial render
        window.addEventListener('popstate', renderRoute);
        renderRoute();

        // Poll runs list while on the runs tab
        startRunsPolling();
    });
}

// ------------------------------------------------------------------
// Theme Toggle
// ------------------------------------------------------------------
function toggleTheme() {
    const html = document.documentElement;
    html.classList.toggle('dark');
    const theme = html.classList.contains('dark') ? 'dark' : 'light';
    localStorage.setItem('theme', theme);
    updateThemeIcon();
}

function loadThemeFromStorage() {
    const savedTheme = localStorage.getItem('theme');
    if (savedTheme) {
        document.documentElement.classList.toggle('dark', savedTheme === 'dark');
    } else {
        // Default to light mode
        document.documentElement.classList.remove('dark');
    }
    updateThemeIcon();
}

function updateThemeIcon() {
    const icon = document.getElementById('theme-toggle');
    if (icon) {
        const isDark = document.documentElement.classList.contains('dark');
        icon.textContent = isDark ? '☀️' : '🌙';
    }
}

// ------------------------------------------------------------------
// Router & Tabs
// ------------------------------------------------------------------
function currentRoute() {
    if (typeof window === 'undefined' || typeof location === 'undefined') {
        return { view: 'run' };
    }
    const pathname = window.location.pathname;
    if (pathname === '/' || pathname === '/run') {
        return { view: 'run' };
    }
    if (pathname === '/runs') {
        const params = new URLSearchParams(window.location.search);
        const pageNum = parseInt(params.get('page'), 10);
        const page = (Number.isInteger(pageNum) && pageNum >= 1) ? pageNum : 1;
        return { view: 'runs', page };
    }
    const matchRunDetail = pathname.match(/^\/runs\/(.+)$/);
    if (matchRunDetail) {
        return { view: 'run-detail', runId: decodeURIComponent(matchRunDetail[1]) };
    }
    if (pathname === '/config') {
        return { view: 'config' };
    }
    if (pathname === '/health') {
        return { view: 'health' };
    }
    return { view: 'run' };
}

function navigate(path, { replace = false } = {}) {
    if (typeof window !== 'undefined' && window.history) {
        window.history[replace ? 'replaceState' : 'pushState']({}, '', path);
    }
    renderRoute();
}

function renderRoute() {
    if (typeof document === 'undefined') return;
    const route = currentRoute();
    if (route.view === 'run') {
        stopDetailPolling();
        showTab('run');
    } else if (route.view === 'config') {
        stopDetailPolling();
        showTab('config');
        loadConfig();
    } else if (route.view === 'health') {
        stopDetailPolling();
        showTab('health');
        loadHealth();
    } else if (route.view === 'runs') {
        stopDetailPolling();
        showTab('runs');
        loadRuns(route.page);
    } else if (route.view === 'run-detail') {
        showRunDetail(route.runId);
    }
}

function setupTabs() {
    const navLinks = document.querySelectorAll('.nav-link');
    navLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const href = link.getAttribute('href') || '/run';
            navigate(href);
        });
    });

    const logo = document.querySelector('.nav-logo');
    if (logo) {
        logo.addEventListener('click', (e) => {
            e.preventDefault();
            navigate('/');
        });
    }
}

function showTab(tabName) {
    document.querySelectorAll('.nav-link').forEach(nav => {
        nav.classList.toggle('active', nav.dataset.tab === tabName);
    });
    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.classList.toggle('active', tab.id === `tab-${tabName}`);
    });
}

// ------------------------------------------------------------------
// Config loading and submission
// ------------------------------------------------------------------
function loadConfig() {
    fetch('/api/config')
        .then(response => response.json())
        .then(config => {
            // Set role selections
            document.getElementById('config-review_backend').value = config.review.backend;
            document.getElementById('config-review_model').value = config.review.model || '';
            document.getElementById('config-build_backend').value = config.build.backend;
            document.getElementById('config-build_model').value = config.build.model || '';
            document.getElementById('config-verify_backend').value = config.verify.backend;
            document.getElementById('config-verify_model').value = config.verify.model || '';
            document.getElementById('config-max_iterations').value = config.max_iterations;
        })
        .catch(error => {
            showStatus('config-status', `Failed to load config: ${error.message}`, 'error');
        });
}

async function submitConfig(e) {
    e.preventDefault();
    const form = e.target;
    const payload = {
        review_backend: form.review_backend.value,
        review_model: form.review_model.value || null,
        build_backend: form.build_backend.value,
        build_model: form.build_model.value || null,
        verify_backend: form.verify_backend.value,
        verify_model: form.verify_model.value || null,
        max_iterations: parseInt(form.max_iterations.value, 10)
    };

    try {
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (response.ok) {
            showStatus('config-status', 'Configuration saved successfully!', 'success');
        } else {
            showStatus('config-status', `Error saving config: ${data.detail}`, 'error');
        }
    } catch (error) {
        showStatus('config-status', `Network error: ${error.message}`, 'error');
    }
}

// ------------------------------------------------------------------
// Health checks
// ------------------------------------------------------------------
async function loadHealth() {
    const healthList = document.getElementById('health-list');
    healthList.innerHTML = '<p>Loading health checks...</p>';
    try {
        const response = await fetch('/api/health');
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Failed to load health');

        const entries = Object.entries(data);
        if (entries.length === 0) {
            healthList.innerHTML = '<p>No backends configured.</p>';
            return;
        }

        healthList.innerHTML = '';
        entries.forEach(([key, health]) => {
            const div = document.createElement('div');
            div.className = 'health-item';
            const status = health.ok ? 'ok' : 'fail';
            const statusText = health.ok ? 'OK' : 'FAIL';

            div.innerHTML = `
                <h4>
                    <span>${health.backend || key}</span>
                    <span class="health-status ${status}">${statusText}</span>
                </h4>
                <div class="health-detail">${health.detail}</div>
            `;
            healthList.appendChild(div);
        });
    } catch (error) {
        healthList.innerHTML = `<p class="error">Health check failed: ${error.message}</p>`;
    }
}

// ------------------------------------------------------------------
// Runs loading
// ------------------------------------------------------------------
async function loadRuns(page = 1) {
    const pageNum = parseInt(page, 10);
    const validPage = (Number.isInteger(pageNum) && pageNum >= 1) ? pageNum : 1;
    currentRunsPage = validPage;

    const container = document.getElementById('runs-list');
    if (!container) return;
    container.innerHTML = '<p>Loading runs...</p>';

    const offset = (validPage - 1) * RUNS_PAGE_SIZE;
    try {
        const response = await fetch(`/api/runs?limit=${RUNS_PAGE_SIZE}&offset=${offset}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Failed to load runs');

        const runs = data.runs || [];
        const total = typeof data.total === 'number' ? data.total : runs.length;

        // If a page ends up empty because runs were removed, clamp to prev page
        if (validPage > 1 && runs.length === 0) {
            navigate('/runs?page=' + (validPage - 1), { replace: true });
            return;
        }

        if (runs.length === 0) {
            container.innerHTML = '<p>No runs found. Start a new one to see it here.</p>';
            return;
        }

        container.innerHTML = '';
        runs.forEach(run => {
            const div = document.createElement('div');
            div.className = 'run-item';
            div.style.cursor = 'pointer';

            // Determine status label
            const { label: status, cls: statusClass } = runStatus(run);

            div.innerHTML = `
                <div class="run-meta">
                    <span class="badge ${statusClass}">${status}</span>
                    <span class="mono">${escapeHtml(run.run_id)}</span>
                    <span>Started ${formatTime(run.started_at)}</span>
                    ${run.finished_at ? `<span>· ${fmtDuration(run.started_at, run.finished_at)}</span>` : ''}
                </div>
                <div class="run-goal">${escapeHtml(run.goal)}</div>
            `;
            div.addEventListener('click', () => navigate('/runs/' + encodeURIComponent(run.run_id)));
            container.appendChild(div);
        });

        // Pagination pager
        if (total > RUNS_PAGE_SIZE) {
            const totalPages = Math.max(1, Math.ceil(total / RUNS_PAGE_SIZE));
            const pager = document.createElement('div');
            pager.className = 'pager';

            const prevBtn = document.createElement('button');
            prevBtn.type = 'button';
            prevBtn.className = 'btn btn-secondary';
            prevBtn.textContent = '« Prev';
            prevBtn.disabled = (validPage <= 1);
            prevBtn.addEventListener('click', (e) => {
                e.preventDefault();
                if (validPage > 1) {
                    navigate('/runs?page=' + (validPage - 1));
                }
            });

            const pageLabel = document.createElement('span');
            pageLabel.className = 'page-label';
            pageLabel.textContent = `Page ${validPage} of ${totalPages}`;

            const nextBtn = document.createElement('button');
            nextBtn.type = 'button';
            nextBtn.className = 'btn btn-secondary';
            nextBtn.textContent = 'Next »';
            nextBtn.disabled = (validPage >= totalPages);
            nextBtn.addEventListener('click', (e) => {
                e.preventDefault();
                if (validPage < totalPages) {
                    navigate('/runs?page=' + (validPage + 1));
                }
            });

            pager.appendChild(prevBtn);
            pager.appendChild(pageLabel);
            pager.appendChild(nextBtn);
            container.appendChild(pager);
        }
    } catch (error) {
        container.innerHTML = `<p>Failed to load runs: ${error.message}</p>`;
    }
}

// ------------------------------------------------------------------
// Run creation
// ------------------------------------------------------------------
async function submitRun(e) {
    e.preventDefault();
    const form = e.target;
    const payload = {
        goal: form.goal.value,
        review_backend: form.review_backend.value || null,
        review_model: form.review_model.value || null,
        build_backend: form.build_backend.value || null,
        build_model: form.build_model.value || null,
        verify_backend: form.verify_backend.value || null,
        verify_model: form.verify_model.value || null,
        max_iterations: parseInt(form.max_iterations.value, 10) || null
    };

    // Disable button to prevent double submission
    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Starting...';

    try {
        const response = await fetch('/api/runs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (response.ok) {
            showStatus('run-status', `Run started with ID: ${data.run_id}`, 'success');
            form.reset();
            // Refresh runs after a short delay if on runs tab
            setTimeout(() => {
                const runsTab = document.getElementById('tab-runs');
                if (runsTab && runsTab.classList.contains('active')) {
                    loadRuns(currentRunsPage);
                }
            }, 2000);
        } else {
            showStatus('run-status', `Error starting run: ${data.detail}`, 'error');
        }
    } catch (error) {
        showStatus('run-status', `Network error: ${error.message}`, 'error');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Start Run';
    }
}

// ------------------------------------------------------------------
// Model options (populate from API asynchronously)
// ------------------------------------------------------------------
async function populateModelOptions() {
    try {
        const response = await fetch('/api/models');
        if (!response.ok) throw new Error('Failed to load models');
        const data = await response.json();

        // For each backend, we could populate a datalist for model suggestions
        const datalistIds = ['review-model-datalist', 'build-model-datalist', 'verify-model-datalist'];
        datalistIds.forEach((id, index) => {
            const datalist = document.createElement('datalist');
            datalist.id = id;
            const backends = ['openrouter', 'claude-code', 'antigravity'];
            const backend = backends[index];
            const models = data[backend] || [];
            models.forEach(model => {
                const option = document.createElement('option');
                option.value = model.id;
                option.textContent = model.name;
                datalist.appendChild(option);
            });
            document.body.appendChild(datalist);
        });

        // Attach datalists to model inputs (both run form and config form)
        const modelInputs = [
            ['review_model', 'config-review_model'],
            ['build_model', 'config-build_model'],
            ['verify_model', 'config-verify_model']
        ];
        modelInputs.forEach((inputIds, index) => {
            inputIds.forEach(inputId => {
                const input = document.getElementById(inputId);
                if (input) {
                    input.setAttribute('list', datalistIds[index]);
                }
            });
        });
    } catch (error) {
        console.warn('Could not load model suggestions:', error);
    }
}

// ------------------------------------------------------------------
// Run detail view
// ------------------------------------------------------------------
let detailPollingId = null;
let currentRunId = null;

function showRunDetail(runId) {
    currentRunId = runId;
    showTab('run-detail');
    loadRunDetail(runId);
    startDetailPolling(runId);
}

async function loadRunDetail(runId) {
    const container = document.getElementById('run-detail-content');
    try {
        const [runResp, callsResp] = await Promise.all([
            fetch(`/api/runs/${runId}`),
            fetch(`/api/runs/${runId}/tool_calls`)
        ]);
        if (!runResp.ok) throw new Error('Failed to load run');
        const run = await runResp.json();
        const callsData = callsResp.ok ? await callsResp.json() : { tool_calls: [] };

        document.getElementById('detail-goal').textContent = run.goal || 'Run Detail';

        const { label: status, cls: statusClass } = runStatus(run);

        const totalCost = (run.steps || []).reduce(
            (sum, s) => sum + (Number(s.usage && s.usage.cost_usd) || 0), 0);

        let html = `
            <div class="card run-summary">
                <div class="run-meta">
                    <span class="badge ${statusClass}">${status}</span>
                    <span class="mono">${escapeHtml(run.run_id)}</span>
                    <span>Started ${formatTime(run.started_at)}</span>
                    ${run.finished_at ? `<span>· ran ${fmtDuration(run.started_at, run.finished_at)}</span>` : ''}
                    ${totalCost > 0 ? `<span class="mono">· $${totalCost.toFixed(totalCost < 0.01 ? 6 : 4)}</span>` : ''}
                </div>
                ${run.pushed && run.pushed.commit ? `<div class="run-note">Committed <span class="mono">${escapeHtml(String(run.pushed.commit).slice(0, 8))}</span>${run.pushed.branch ? ` to <span class="mono">${escapeHtml(run.pushed.branch)}</span>` : ''}${run.pushed.pushed ? ' and pushed' : ' (push failed)'}.</div>` : ''}
                ${run.error ? `<div class="detail-error">${escapeHtml(run.error)}</div>` : ''}
            </div>
        `;

        html += '<h3>Steps</h3>';
        if (run.steps && run.steps.length) {
            run.steps.forEach((step, index) => {
                html += renderStep(step, index);
            });
        } else {
            html += '<p class="empty-note">Waiting for the first step to report back…</p>';
        }

        const calls = callsData.tool_calls || [];
        if (calls.length) {
            html += '<h3>Tool Calls</h3><div class="tool-timeline">';
            calls.forEach((call, index) => {
                html += renderToolCall(call, index);
            });
            html += '</div>';
        }

        container.innerHTML = html;
        attachToolToggleListeners();
    } catch (error) {
        container.innerHTML = `<p class="error">Failed to load run detail: ${error.message}</p>`;
    }
}

function fmtCost(usage) {
    if (!usage) return '';
    const c = usage.cost_usd;
    if (c === null || c === undefined) return '';
    const n = Number(c);
    if (Number.isNaN(n)) return '';
    return '$' + n.toFixed(n > 0 && n < 0.01 ? 6 : 4);
}

function verifyVerdict(text) {
    const m = String(text || '').match(/VERIFY_RESULT:\s*(PASS|FAIL)/i);
    return m ? m[1].toUpperCase() : null;
}

function fmtArgs(args) {
    const entries = Object.entries(args || {});
    if (!entries.length) return '';
    return entries
        .map(([k, v]) => {
            let val = typeof v === 'string' ? v : JSON.stringify(v);
            if (val.length > 300) val = val.slice(0, 300) + '…';
            return `<span class="arg-key">${escapeHtml(k)}</span> ${escapeHtml(val)}`;
        })
        .join('\n');
}

function formatToolReqArgs(args) {
    if (!args || typeof args !== 'object') return '';
    const entries = Object.entries(args);
    if (!entries.length) return '';
    return entries
        .map(([k, v]) => {
            let valStr = v === undefined ? 'undefined' : JSON.stringify(v);
            if (typeof valStr !== 'string') valStr = String(v);
            if (valStr.length > 120) {
                valStr = valStr.slice(0, 120) + '…';
            }
            return `${escapeHtml(k)}=${escapeHtml(valStr)}`;
        })
        .join(' ');
}

function renderToolReq(req) {
    const name = escapeHtml(req.name || '');
    const argsFormatted = formatToolReqArgs(req.args);
    const argsHtml = argsFormatted ? ` <span class="tool-req-args mono">${argsFormatted}</span>` : '';
    return `<div class="tool-req"><span class="tool-req-name">${name}</span>${argsHtml}</div>`;
}

function splitToolBlocks(text) {
    if (!text) return { prose: '', requests: [] };

    let str = String(text);
    const requests = [];

    function tryPushRequest(raw) {
        if (!raw) return;
        const trimmed = String(raw).trim();
        let parsed = null;
        try {
            parsed = JSON.parse(trimmed);
        } catch (e) {
            const m = trimmed.match(/\{[\s\S]*\}/);
            if (m) {
                try {
                    parsed = JSON.parse(m[0]);
                } catch (e2) {}
            }
        }
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed) && parsed.name && typeof parsed.name === 'string') {
            const args = (parsed.args && typeof parsed.args === 'object' && !Array.isArray(parsed.args))
                ? parsed.args
                : {};
            requests.push({ name: parsed.name, args });
        }
    }

    // 1. Strip closed tool_call blocks
    const toolCallBlockRe = /<[^\n>]*?tool_call\s*>([\s\S]*?)<\s*\/[^\n>]*?tool_call\s*>/gi;
    str = str.replace(toolCallBlockRe, (_, inner) => {
        tryPushRequest(inner);
        return '';
    });

    // Strip unclosed trailing tool_call block
    const unclosedBlockRe = /<[^\n>]*?tool_call\s*>([\s\S]*)$/i;
    str = str.replace(unclosedBlockRe, (_, inner) => {
        tryPushRequest(inner);
        return '';
    });

    // 2. Strip bare lines containing JSON object with name
    const lines = str.split('\n');
    const keptLines = [];
    for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
            try {
                const parsed = JSON.parse(trimmed);
                if (parsed && typeof parsed === 'object' && !Array.isArray(parsed) && parsed.name && typeof parsed.name === 'string') {
                    const args = (parsed.args && typeof parsed.args === 'object' && !Array.isArray(parsed.args))
                        ? parsed.args
                        : {};
                    requests.push({ name: parsed.name, args });
                    continue;
                }
            } catch (e) {}
        }
        keptLines.push(line);
    }

    const prose = keptLines.join('\n').trim();
    return { prose, requests };
}

function renderStep(step, index) {
    const ok = step.success;
    const statusClass = ok ? 'success' : 'danger';
    const statusText = ok ? 'OK' : 'FAIL';

    let verdictHtml = '';
    if (step.role === 'verify') {
        const v = verifyVerdict(step.text);
        if (v) verdictHtml = `<span class="verdict ${v === 'PASS' ? 'pass' : 'fail'}">${v}</span>`;
    }

    const usage = step.usage || {};
    const model = usage.model ? `${usage.backend} · ${usage.model}` : (usage.backend || '');
    const cost = fmtCost(usage);
    const iter = step.iteration ? `#${step.iteration}` : '';

    const { prose, requests } = splitToolBlocks(step.text);

    let body = '';
    if (requests.length) {
        body += `<div class="tool-reqs">${requests.map(renderToolReq).join('')}</div>`;
    }
    if (prose) {
        body += `<div class="md">${renderMarkdown(prose)}</div>`;
    }
    if (!requests.length && !prose) {
        body = `<p class="empty-note">No response text was recorded for this step.</p>`;
    }

    return `
        <div class="step-item">
            <div class="step-header">
                <span class="step-number">${index + 1}</span>
                <span class="step-role">${escapeHtml(step.role)}</span>
                ${iter ? `<span class="step-iter mono">${iter}</span>` : ''}
                <span class="step-mode">${escapeHtml(step.mode || '')}</span>
                ${verdictHtml}
                <span class="badge ${statusClass}">${statusText}</span>
                <span class="step-meta mono">
                    ${model ? `<span>${escapeHtml(model)}</span>` : ''}
                    ${cost ? `<span>${cost}</span>` : ''}
                </span>
            </div>
            <div class="step-body">${body}</div>
        </div>
    `;
}

function renderToolCall(call, index) {
    const result = (call.result && typeof call.result === 'object') ? call.result : {};
    const isSuccess = call.status === 'success' || call.status === 'OK' || result.success === true;
    const statusClass = isSuccess ? 'success' : 'danger';
    const statusText = isSuccess ? 'OK' : 'FAIL';

    const ms = call.execution_time_ms;
    const timeText = (ms !== undefined && ms !== null) ? `${ms} ms` : '';

    const structured = result.structured || {};
    const hasDiff = structured.current !== undefined;
    const argsText = fmtArgs(call.args);
    const output = (result.output || '').trim();
    const error = (call.error || result.error || '').trim();

    let resultSection = '';
    if (hasDiff) {
        resultSection = `
            <div class="tool-section">
                <h4>${structured.previous !== undefined ? 'Diff' : 'New file'} — <span class="mono">${escapeHtml(structured.path || '')}</span></h4>
                <div class="diff-view">${renderDiff(structured.previous || '', structured.current || '')}</div>
            </div>`;
    } else if (output) {
        resultSection = `
            <div class="tool-section">
                <h4>Output</h4>
                <pre class="tool-output">${escapeHtml(output)}</pre>
            </div>`;
    }

    const errorSection = error
        ? `<div class="tool-section">
               <h4>Error</h4>
               <pre class="tool-output tool-error">${escapeHtml(error)}</pre>
           </div>`
        : '';

    return `
        <div class="tool-call-item">
            <div class="tool-call-header" data-index="${index}">
                <span class="tool-call-name">${escapeHtml(call.tool_name)}</span>
                <span class="badge ${statusClass}">${statusText}</span>
                ${timeText ? `<span class="tool-call-time mono">${timeText}</span>` : ''}
                <span class="tool-toggle" aria-hidden="true">+</span>
            </div>
            <div class="tool-call-body" id="tool-body-${index}" hidden>
                ${argsText ? `
                <div class="tool-section">
                    <h4>Arguments</h4>
                    <pre class="tool-args">${argsText}</pre>
                </div>` : ''}
                ${resultSection}
                ${errorSection}
                ${(!resultSection && !errorSection && !argsText) ? '<p class="empty-note">No details recorded.</p>' : ''}
            </div>
        </div>
    `;
}

function renderDiff(oldText, newText) {
    const oldLines = (oldText || '').split('\n');
    const newLines = (newText || '').split('\n');
    let html = '<div class="diff-line diff-header"><span class="diff-lineno">-</span><span class="diff-lineno">+</span><span></span></div>';

    const maxLines = Math.max(oldLines.length, newLines.length);
    for (let i = 0; i < maxLines; i++) {
        const oldLine = oldLines[i];
        const newLine = newLines[i];
        const oldNum = i < oldLines.length ? i + 1 : '';
        const newNum = i < newLines.length ? i + 1 : '';

        if (oldLine === newLine) {
            html += `<div class="diff-line diff-context"><span class="diff-lineno">${oldNum}</span><span class="diff-lineno">${newNum}</span><span class="diff-content"> ${escapeHtml(oldLine)}</span></div>`;
        } else if (i < oldLines.length && i >= newLines.length) {
            html += `<div class="diff-line diff-remove"><span class="diff-lineno">${oldNum}</span><span class="diff-lineno"></span><span class="diff-content">-${escapeHtml(oldLine)}</span></div>`;
        } else if (i >= oldLines.length && i < newLines.length) {
            html += `<div class="diff-line diff-add"><span class="diff-lineno"></span><span class="diff-lineno">${newNum}</span><span class="diff-content">+${escapeHtml(newLine)}</span></div>`;
        } else {
            html += `<div class="diff-line diff-remove"><span class="diff-lineno">${oldNum}</span><span class="diff-lineno"></span><span class="diff-content">-${escapeHtml(oldLine)}</span></div>`;
            html += `<div class="diff-line diff-add"><span class="diff-lineno"></span><span class="diff-lineno">${newNum}</span><span class="diff-content">+${escapeHtml(newLine)}</span></div>`;
        }
    }
    return html;
}

function attachToolToggleListeners() {
    document.querySelectorAll('.tool-call-header').forEach(header => {
        header.addEventListener('click', () => {
            const index = header.dataset.index;
            const body = document.getElementById(`tool-body-${index}`);
            const toggle = header.querySelector('.tool-toggle');
            body.hidden = !body.hidden;
            toggle.textContent = body.hidden ? '+' : '−';
        });
    });
}

function startDetailPolling(runId) {
    stopDetailPolling();
    detailPollingId = setInterval(() => loadRunDetail(runId), 3000);
}

function stopDetailPolling() {
    if (detailPollingId) {
        clearInterval(detailPollingId);
        detailPollingId = null;
    }
    currentRunId = null;
}

function startRunsPolling() {
    setInterval(() => {
        const runsTab = document.getElementById('tab-runs');
        if (runsTab && runsTab.classList.contains('active')) {
            loadRuns(currentRunsPage);
        }
    }, 5000);
}

function formatTime(ts) {
    if (!ts) return '-';
    return new Date(ts * 1000).toLocaleString();
}

function fmtDuration(start, end) {
    if (!start || !end) return '';
    const s = Math.max(0, Math.round(end - start));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ${s % 60}s`;
    return `${Math.floor(m / 60)}h ${m % 60}m`;
}

function runStatus(run) {
    if (run.pushed && run.pushed.pushed) return { label: 'Pushed', cls: 'success' };
    if (run.pushed && run.pushed.pushed === false) return { label: 'Push failed', cls: 'danger' };
    if (run.stopped) return { label: 'Stopped', cls: 'warning' };
    if (run.error) return { label: 'Error', cls: 'danger' };
    if (run.finished_at) return { label: 'Completed', cls: 'info' };
    return { label: 'Running', cls: 'warning' };
}

function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// ------------------------------------------------------------------
// Utility helpers
// ------------------------------------------------------------------
function showStatus(containerId, message, type) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.className = 'status-message ' + (type || '');
    container.textContent = message;
    setTimeout(() => {
        container.textContent = '';
        container.className = 'status-message';
    }, 4000);
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        splitToolBlocks,
        formatToolReqArgs,
        renderToolReq,
        renderStep,
        escapeHtml,
        currentRoute,
        navigate,
        renderRoute,
        showTab,
        loadRuns,
    };
}
