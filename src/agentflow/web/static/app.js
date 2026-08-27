// ------------------------------------------------------------------
// AgentFlow Web UI - Enhanced Frontend
// ------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    // Initialize theme
    initTheme();

    // Set up tab switching
    setupTabs();

    // Load initial data
    loadConfig();
    loadHealth();
    loadRuns();

    // Form submission handlers
    document.getElementById('config-form').addEventListener('submit', submitConfig);
    document.getElementById('run-form').addEventListener('submit', submitRun);

    // Theme toggle
    document.getElementById('theme-toggle').addEventListener('click', toggleTheme);

    // Load backend dropdowns with models
    populateModelOptions();
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

            // Determine status label
            const status = run.pushed && run.pushed.pushed ? 'Pushed' : (run.finished_at ? 'Completed' : 'Running');
            const statusClass = run.pushed && run.pushed.pushed ? 'success' : (run.finished_at ? 'info' : 'warning');

            div.innerHTML = `
                <h3>${run.goal.substring(0, 60)}...</h3>
                <div class="run-meta">
                    <span class="badge ${statusClass}">${status}</span>
                    <span>Run ID: ${run.run_id}</span>
                    <span>Started: ${new Date(run.started_at * 1000).toLocaleString()}</span>
                    ${run.finished_at ? `<span>Finished: ${new Date(run.finished_at * 1000).toLocaleString()}</span>` : ''}
                </div>
                <div class="run-goal">${run.goal}</div>
            `;
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

        // Attach datalists to model inputs
        const modelInputs = ['review_model', 'build_model', 'verify_model'];
        modelInputs.forEach((inputId, index) => {
            const input = document.getElementById(inputId);
            if (input) {
                input.setAttribute('list', ['review-model-dat', 'build-model-dat', 'verify-model-dat'][index]);
            }
        });
    } catch (error) {
        console.warn('Could not load model suggestions:', error);
    }
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
