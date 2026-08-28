// ------------------------------------------------------------------
// AgentFlow Web UI - Enhanced Frontend
// ------------------------------------------------------------------

const RUNS_PAGE_SIZE = 25;
let currentRunsPage = 1;

const PROJECT_KEY = 'af_project';
let projectsList = [];
let currentProject = null;

function projectQuery() {
    return currentProject ? ('project=' + encodeURIComponent(currentProject)) : '';
}

async function loadProjects() {
    try {
        const response = await fetch('/api/projects');
        if (response.ok) {
            projectsList = await response.json();
        }
    } catch (e) {
        projectsList = [];
    }

    let stored = null;
    try {
        stored = localStorage.getItem(PROJECT_KEY);
    } catch (e) {}

    if (stored && projectsList.some(p => p.path === stored)) {
        currentProject = stored;
    } else {
        currentProject = (projectsList[0] && projectsList[0].path) || null;
    }

    const select = document.getElementById('project-select');
    if (select) {
        select.innerHTML = '';
        projectsList.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.path;
            opt.textContent = p.name;
            select.appendChild(opt);
        });
        if (currentProject) {
            select.value = currentProject;
        }
        select.hidden = projectsList.length <= 1;
    }
}

if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', async () => {
        // Initialize theme
        loadThemeFromStorage();

        // Set up tab switching
        setupTabs();

        // Project selector change handler
        const projSelect = document.getElementById('project-select');
        if (projSelect) {
            projSelect.addEventListener('change', (e) => {
                currentProject = e.target.value;
                try {
                    localStorage.setItem(PROJECT_KEY, currentProject);
                } catch (err) {}
                renderRoute();
                loadMemory();
            });
        }

        // Form submission handlers
        document.getElementById('config-form').addEventListener('submit', submitConfig);
        document.getElementById('run-form').addEventListener('submit', submitRun);
        document.getElementById('back-to-runs').addEventListener('click', (e) => {
            e.preventDefault();
            navigate('/runs');
        });

        // Theme toggle
        document.getElementById('theme-toggle').addEventListener('click', toggleTheme);

        // Test email button
        const testEmailBtn = document.getElementById('send-test-email');
        if (testEmailBtn) {
            testEmailBtn.addEventListener('click', async () => {
                const statusSpan = document.getElementById('test-email-status');
                if (statusSpan) {
                    statusSpan.textContent = 'Sending...';
                    statusSpan.className = 'test-email-status';
                }
                try {
                    const res = await fetch('/api/notifications/test', { method: 'POST' });
                    const data = await res.json();
                    if (statusSpan) {
                        statusSpan.textContent = data.result || 'No response';
                        if (data.result && data.result.startsWith('sent')) {
                            statusSpan.className = 'test-email-status success';
                        } else {
                            statusSpan.className = 'test-email-status error';
                        }
                    }
                } catch (err) {
                    if (statusSpan) {
                        statusSpan.textContent = `error: ${err.message}`;
                        statusSpan.className = 'test-email-status error';
                    }
                }
            });
        }

        // Notify toggle
        const notifyBtn = document.getElementById('notify-toggle');
        if (notifyBtn) {
            notifyBtn.addEventListener('click', toggleNotify);
        }
        updateNotifyIcon();

        // Load backend dropdowns with models
        populateModelOptions();

        // Load projects first so currentProject is set before first renderRoute/loadMemory
        await loadProjects();

        // Load memory
        loadMemory();

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
// Desktop Notifications
// ------------------------------------------------------------------
const NOTIFY_KEY = 'af_notify';

function notifyEnabled() {
    try {
        return localStorage.getItem(NOTIFY_KEY) === 'on';
    } catch (e) {
        return false;
    }
}

function updateNotifyIcon() {
    const b = document.getElementById('notify-toggle');
    if (b) b.textContent = notifyEnabled() ? '🔔' : '🔕';
}

function toggleNotify() {
    if (!notifyEnabled()) {
        if (!('Notification' in window)) {
            console.warn('Notifications unsupported');
            return;
        }
        Notification.requestPermission().then(p => {
            if (p === 'granted') {
                try {
                    localStorage.setItem(NOTIFY_KEY, 'on');
                } catch (e) {}
                updateNotifyIcon();
            }
        });
    } else {
        try {
            localStorage.setItem(NOTIFY_KEY, 'off');
        } catch (e) {}
        updateNotifyIcon();
    }
}

function maybeNotify(title, body) {
    if ('Notification' in window && Notification.permission === 'granted' && notifyEnabled()) {
        try {
            new Notification(title, { body });
        } catch (e) {}
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
        loadMemory();
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
            const revB = document.getElementById('config-review_backend');
            if (revB && config.review) revB.value = config.review.backend;
            const revM = document.getElementById('config-review_model');
            if (revM && config.review) revM.value = config.review.model || '';
            const bldB = document.getElementById('config-build_backend');
            if (bldB && config.build) bldB.value = config.build.backend;
            const bldM = document.getElementById('config-build_model');
            if (bldM && config.build) bldM.value = config.build.model || '';
            const verB = document.getElementById('config-verify_backend');
            if (verB && config.verify) verB.value = config.verify.backend;
            const verM = document.getElementById('config-verify_model');
            if (verM && config.verify) verM.value = config.verify.model || '';
            const maxI = document.getElementById('config-max_iterations');
            if (maxI && config.max_iterations !== undefined) maxI.value = config.max_iterations;

            const statusEl = document.getElementById('config-openrouter-status');
            if (statusEl) {
                if (config.openrouter_key) {
                    if (config.openrouter_key.set) {
                        const masked = escapeHtml(config.openrouter_key.masked || '');
                        const source = config.openrouter_key.source === 'env' ? 'environment' : 'config file';
                        statusEl.innerHTML = `<span class="badge success">Set</span> <span class="mono">${masked}</span> <span class="key-source">(from ${source})</span>`;
                    } else {
                        statusEl.innerHTML = '<span class="badge warning">Not set</span>';
                    }
                } else {
                    statusEl.innerHTML = '';
                }
            }

            // Notifications config
            const n = config.notifications || {};
            const notifyEnabledEl = document.getElementById('config-notify-enabled');
            if (notifyEnabledEl) notifyEnabledEl.checked = !!n.enabled;

            const notifyEmailToEl = document.getElementById('config-notify-email_to');
            if (notifyEmailToEl) notifyEmailToEl.value = n.email_to || '';

            const notifyEmailFromEl = document.getElementById('config-notify-email_from');
            if (notifyEmailFromEl) notifyEmailFromEl.value = n.email_from || '';

            const notifySmtpHostEl = document.getElementById('config-notify-smtp_host');
            if (notifySmtpHostEl) notifySmtpHostEl.value = n.smtp_host || '';

            const notifySmtpPortEl = document.getElementById('config-notify-smtp_port');
            if (notifySmtpPortEl) notifySmtpPortEl.value = n.smtp_port !== undefined ? n.smtp_port : 587;

            const notifySmtpUserEl = document.getElementById('config-notify-smtp_username');
            if (notifySmtpUserEl) notifySmtpUserEl.value = n.smtp_username || '';

            const notifyBaseUrlEl = document.getElementById('config-notify-base_url');
            if (notifyBaseUrlEl) notifyBaseUrlEl.value = n.base_url || '';

            const notifyTlsEl = document.getElementById('config-notify-tls');
            if (notifyTlsEl) notifyTlsEl.checked = n.smtp_use_tls !== undefined ? !!n.smtp_use_tls : true;

            const notifyOn = n.notify_on || ['finished'];
            const notifyOnFinishedEl = document.getElementById('config-notify-on-finished');
            if (notifyOnFinishedEl) notifyOnFinishedEl.checked = notifyOn.includes('finished');

            const notifyOnBlockedEl = document.getElementById('config-notify-on-blocked');
            if (notifyOnBlockedEl) notifyOnBlockedEl.checked = notifyOn.includes('blocked');

            const smtpStatusEl = document.getElementById('config-smtp-status');
            if (smtpStatusEl) {
                if (config.smtp_password) {
                    if (config.smtp_password.set) {
                        const masked = escapeHtml(config.smtp_password.masked || '');
                        const source = config.smtp_password.source === 'env' ? 'environment' : 'config file';
                        smtpStatusEl.innerHTML = `<span class="badge success">Set</span> <span class="mono">${masked}</span> <span class="key-source">(from ${source})</span>`;
                    } else {
                        smtpStatusEl.innerHTML = '<span class="badge warning">Not set</span>';
                    }
                } else {
                    smtpStatusEl.innerHTML = '';
                }
            }
        })
        .catch(error => {
            showStatus('config-status', `Failed to load config: ${error.message}`, 'error');
        });
}

async function loadMemory() {
    try {
        const pq = projectQuery();
        const url = pq ? `/api/memory?${pq}` : '/api/memory';
        const response = await fetch(url);
        if (!response.ok) return;
        const data = await response.json();
        const globalEl = document.getElementById('config-memory-global');
        if (globalEl) {
            globalEl.value = data.global || '';
        }
        const projectEl = document.getElementById('config-memory-project');
        if (projectEl) {
            projectEl.value = data.project || '';
        }
    } catch (error) {
        // Silently ignore network/parsing errors
    }
}

async function submitConfig(e) {
    e.preventDefault();
    const form = e.target;
    const keyVal = form.openrouter_api_key ? form.openrouter_api_key.value : '';

    const notifyOn = [];
    const onFinished = document.getElementById('config-notify-on-finished');
    if (onFinished && onFinished.checked) notifyOn.push('finished');
    const onBlocked = document.getElementById('config-notify-on-blocked');
    if (onBlocked && onBlocked.checked) notifyOn.push('blocked');

    const notifyEnabledEl = document.getElementById('config-notify-enabled');
    const emailToEl = document.getElementById('config-notify-email_to');
    const emailFromEl = document.getElementById('config-notify-email_from');
    const smtpHostEl = document.getElementById('config-notify-smtp_host');
    const smtpPortEl = document.getElementById('config-notify-smtp_port');
    const smtpUserEl = document.getElementById('config-notify-smtp_username');
    const baseUrlEl = document.getElementById('config-notify-base_url');
    const tlsEl = document.getElementById('config-notify-tls');
    const smtpPwEl = document.getElementById('config-smtp_password');

    const notifPort = smtpPortEl && smtpPortEl.value ? parseInt(smtpPortEl.value, 10) : 587;
    const notifications = {
        enabled: notifyEnabledEl ? notifyEnabledEl.checked : false,
        email_to: (emailToEl && emailToEl.value.trim()) || null,
        email_from: (emailFromEl && emailFromEl.value.trim()) || null,
        smtp_host: (smtpHostEl && smtpHostEl.value.trim()) || null,
        smtp_port: isNaN(notifPort) ? 587 : notifPort,
        smtp_username: (smtpUserEl && smtpUserEl.value.trim()) || null,
        smtp_use_tls: tlsEl ? tlsEl.checked : true,
        notify_on: notifyOn,
        base_url: (baseUrlEl && baseUrlEl.value.trim()) || null,
    };
    const smtpPwVal = (smtpPwEl && smtpPwEl.value) || '';

    const payload = {
        review_backend: form.review_backend ? form.review_backend.value : '',
        review_model: (form.review_model && form.review_model.value) || null,
        build_backend: form.build_backend ? form.build_backend.value : '',
        build_model: (form.build_model && form.build_model.value) || null,
        verify_backend: form.verify_backend ? form.verify_backend.value : '',
        verify_model: (form.verify_model && form.verify_model.value) || null,
        max_iterations: form.max_iterations ? parseInt(form.max_iterations.value, 10) : 3,
        openrouter_api_key: keyVal.trim() || null,
        notifications: notifications,
        smtp_password: smtpPwVal.trim() || null
    };

    const globalEl = document.getElementById('config-memory-global');
    const projectEl = document.getElementById('config-memory-project');
    const memoryPayload = {
        global: globalEl ? globalEl.value : '',
        project: projectEl ? projectEl.value : ''
    };

    try {
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) {
            showStatus('config-status', `Error saving config: ${data.detail}`, 'error');
            return;
        }

        const pq = projectQuery();
        const memUrl = pq ? `/api/memory?${pq}` : '/api/memory';
        const memResponse = await fetch(memUrl, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(memoryPayload)
        });
        const memData = await memResponse.json();
        if (!memResponse.ok) {
            showStatus('config-status', `Config saved, but memory failed: ${memData.detail}`, 'error');
            return;
        }

        showStatus('config-status', 'Configuration saved successfully!', 'success');
        if (form.openrouter_api_key) {
            form.openrouter_api_key.value = '';
        }
        if (smtpPwEl) {
            smtpPwEl.value = '';
        }
        loadConfig();
        loadMemory();
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
        const pq = projectQuery();
        const url = `/api/runs?limit=${RUNS_PAGE_SIZE}&offset=${offset}` + (pq ? `&${pq}` : '');
        const response = await fetch(url);
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
        project: currentProject || null,
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
let _lastDetailState = null;

function showRunDetail(runId) {
    currentRunId = runId;
    showTab('run-detail');
    loadRunDetail(runId);
    startDetailPolling(runId);
}

async function loadRunDetail(runId) {
    const container = document.getElementById('run-detail-content');
    try {
        const pq = projectQuery();
        const q = pq ? `?${pq}` : '';
        const [runResp, callsResp] = await Promise.all([
            fetch(`/api/runs/${runId}${q}`),
            fetch(`/api/runs/${runId}/tool_calls${q}`)
        ]);
        if (!runResp.ok) throw new Error('Failed to load run');
        const run = await runResp.json();
        const callsData = callsResp.ok ? await callsResp.json() : { tool_calls: [] };

        if (_lastDetailState && _lastDetailState.runId === runId) {
            if (!_lastDetailState.finished_at && run.finished_at) {
                maybeNotify('agentflow — run ' + runStatus(run).label, (run.goal || '').slice(0, 120));
            }
            if ((run.blockers || []).some(b => b.fatal) && _lastDetailState.fatalBlockers === 0) {
                maybeNotify('agentflow — run blocked', (run.blockers.find(b => b.fatal) || {}).detail || '');
            }
        }
        _lastDetailState = {
            runId: runId,
            finished_at: run.finished_at,
            fatalBlockers: (run.blockers || []).filter(b => b.fatal).length
        };

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

        if (Array.isArray(run.blockers) && run.blockers.length) {
            function blockerLabel(reason) {
                if (reason === 'budget') return 'Budget limit reached';
                if (reason === 'permission') return 'Permission denied';
                if (reason === 'backend_error') return 'Backend error';
                return reason || '';
            }
            html += '<div class="blockers">';
            run.blockers.forEach(b => {
                const fatalClass = b.fatal ? 'fatal' : '';
                html += `<div class="blocker ${fatalClass}"><span class="blocker-reason">${escapeHtml(blockerLabel(b.reason))}</span> <span class="blocker-detail">${escapeHtml(b.detail || '')}</span></div>`;
            });
            html += '</div>';
        }

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
    if (step.no_response || (!prose && !requests.length)) {
        body = `<div class="md step-noresponse">${renderMarkdown(step.text || 'No response recorded for this step.')}</div>`;
    } else {
        if (requests.length) {
            body += `<div class="tool-reqs">${requests.map(renderToolReq).join('')}</div>`;
        }
        if (prose) {
            body += `<div class="md">${renderMarkdown(prose)}</div>`;
        }
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
    _lastDetailState = null;
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
    if (run.finished_at && !(run.pushed && run.pushed.pushed) && Array.isArray(run.blockers) && run.blockers.some(b => b.fatal)) return { label: 'Blocked', cls: 'danger' };
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
        runStatus,
        loadMemory,
        submitConfig,
        NOTIFY_KEY,
        notifyEnabled,
        toggleNotify,
        maybeNotify,
        updateNotifyIcon,
        PROJECT_KEY,
        loadProjects,
        projectQuery,
    };
}
