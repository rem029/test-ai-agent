// ------------------------------------------------------------------
// AgentFlow Web UI - Enhanced Frontend
// ------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    // Initialize theme
    loadThemeFromStorage();

    // Set up tab switching
    setupTabs();

    // Load initial data
    loadConfig();
    loadHealth();
    loadRuns();

    // Form submission handlers
    document.getElementById('config-form').addEventListener('submit', submitConfig);
    document.getElementById('run-form').addEventListener('submit', submitRun);
    document.getElementById('back-to-runs').addEventListener('click', (e) => {
        e.preventDefault();
        showTab('runs');
        stopDetailPolling();
    });

    // Theme toggle
    document.getElementById('theme-toggle').addEventListener('click', toggleTheme);

    // Load backend dropdowns with models
    populateModelOptions();

    // Poll runs list while on the runs tab
    startRunsPolling();
});

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
// Tabs
// ------------------------------------------------------------------
function setupTabs() {
    const navLinks = document.querySelectorAll('.nav-link');
    const tabContents = document.querySelectorAll('.tab-content');

    navLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const targetTab = link.dataset.tab;

            // Deactivate all links
            navLinks.forEach(nav => nav.classList.remove('active'));
            link.classList.add('active');

            // Show corresponding content
            tabContents.forEach(tab => {
                if (tab.id === `tab-${targetTab}`) {
                    tab.classList.add('active');
                } else {
                    tab.classList.remove('active');
                }
            });
        });
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
async function loadRuns() {
    const container = document.getElementById('runs-list');
    container.innerHTML = '<p>Loading runs...</p>';
    try {
        const response = await fetch('/api/runs');
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Failed to load runs');

        const runs = data.runs || [];
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
            const status = run.pushed && run.pushed.pushed ? 'Pushed' : (run.finished_at ? 'Completed' : 'Running');
            const statusClass = run.pushed && run.pushed.pushed ? 'success' : (run.finished_at ? 'info' : 'warning');

            div.innerHTML = `
                <h3>${escapeHtml(run.goal).substring(0, 60)}...</h3>
                <div class="run-meta">
                    <span class="badge ${statusClass}">${status}</span>
                    <span>Run ID: ${run.run_id}</span>
                    <span>Started: ${formatTime(run.started_at)}</span>
                    ${run.finished_at ? `<span>Finished: ${formatTime(run.finished_at)}</span>` : ''}
                </div>
                <div class="run-goal">${escapeHtml(run.goal)}</div>
            `;
            div.addEventListener('click', () => showRunDetail(run.run_id));
            container.appendChild(div);
        });
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
            // Refresh runs after a short delay to show the new run
            setTimeout(() => loadRuns(), 2000);
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

function showTab(tabName) {
    document.querySelectorAll('.nav-link').forEach(nav => {
        nav.classList.toggle('active', nav.dataset.tab === tabName);
    });
    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.classList.toggle('active', tab.id === `tab-${tabName}`);
    });
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

        const status = run.pushed && run.pushed.pushed
            ? 'Pushed'
            : (run.finished_at ? 'Completed' : 'Running');
        const statusClass = run.pushed && run.pushed.pushed
            ? 'success'
            : (run.finished_at ? 'info' : 'warning');

        let html = `
            <div class="card run-summary">
                <div class="run-meta">
                    <span class="badge ${statusClass}">${status}</span>
                    <span>Run ID: ${run.run_id}</span>
                    <span>Started: ${formatTime(run.started_at)}</span>
                    ${run.finished_at ? `<span>Finished: ${formatTime(run.finished_at)}</span>` : ''}
                </div>
                ${run.error ? `<div class="detail-error">Error: ${escapeHtml(run.error)}</div>` : ''}
            </div>
        `;

        html += '<h3>Steps</h3>';
        if (run.steps && run.steps.length) {
            run.steps.forEach((step, index) => {
                html += renderStep(step, index);
            });
        } else {
            html += '<p>No steps yet.</p>';
        }

        html += '<h3>Tool Calls</h3>';
        const calls = callsData.tool_calls || [];
        if (calls.length) {
            html += '<div class="tool-timeline">';
            calls.forEach((call, index) => {
                html += renderToolCall(call, index);
            });
            html += '</div>';
        } else {
            html += '<p>No tool calls recorded.</p>';
        }

        container.innerHTML = html;
        attachToolToggleListeners();
    } catch (error) {
        container.innerHTML = `<p class="error">Failed to load run detail: ${error.message}</p>`;
    }
}

function renderStep(step, index) {
    const statusClass = step.success ? 'success' : 'danger';
    const statusText = step.success ? 'OK' : 'FAIL';
    return `
        <div class="step-item">
            <div class="step-header">
                <span class="step-number">${index + 1}</span>
                <span class="step-role">${escapeHtml(step.role)}</span>
                <span class="badge ${statusClass}">${statusText}</span>
                <span class="step-mode">${escapeHtml(step.mode || '')}</span>
            </div>
            <div class="step-body">
                <pre>${escapeHtml(step.text || '')}</pre>
            </div>
        </div>
    `;
}

function renderToolCall(call, index) {
    const isSuccess = call.status === 'success' || call.status === 'OK' || (call.result && call.result.success === true) || call.success === true;
    const statusClass = isSuccess ? 'success' : 'danger';
    const statusText = isSuccess ? 'OK' : 'FAIL';
    const args = JSON.stringify(call.args || {}, null, 2);
    const result = call.result || '(no output)';
    const resultObj = typeof result === 'object' ? result : {};
    const hasDiff = resultObj.structured && resultObj.structured.previous !== undefined;
    const timeText = call.execution_time_ms !== undefined && call.execution_time_ms !== null ? `${call.execution_time_ms}ms` : (call.timestamp ? formatTime(call.timestamp) : '-');

    let diffHtml = '';
    if (hasDiff) {
        diffHtml = renderDiff(resultObj.structured.previous, resultObj.structured.current);
    }

    return `
        <div class="tool-call-item">
            <div class="tool-call-header" data-index="${index}">
                <span class="tool-call-name">${escapeHtml(call.tool_name)}</span>
                <span class="badge ${statusClass}">${statusText}</span>
                <span class="tool-call-time">${timeText}</span>
                <span class="tool-toggle">+</span>
            </div>
            <div class="tool-call-body" id="tool-body-${index}" style="display: none;">
                <div class="tool-section">
                    <h4>Arguments</h4>
                    <pre>${escapeHtml(args)}</pre>
                </div>
                ${hasDiff ? `
                <div class="tool-section">
                    <h4>Diff</h4>
                    <div class="diff-view">${diffHtml}</div>
                </div>
                ` : `
                <div class="tool-section">
                    <h4>Result</h4>
                    <pre>${escapeHtml(typeof result === 'object' ? JSON.stringify(result, null, 2) : result)}</pre>
                </div>
                `}
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
            if (body.style.display === 'none') {
                body.style.display = 'block';
                toggle.textContent = '-';
            } else {
                body.style.display = 'none';
                toggle.textContent = '+';
            }
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
            loadRuns();
        }
    }, 5000);
}

function formatTime(ts) {
    if (!ts) return '-';
    return new Date(ts * 1000).toLocaleString();
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
