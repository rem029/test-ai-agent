// ===================================================================
// agentflow web console — "Transport" Frontend (Phase K)
// Zero-build, vanilla JS, SSE-driven, playhead-scrubbed master console.
// ===================================================================

const RUNS_PAGE_SIZE = 25;
const PROJECT_KEY = 'af_project';
const NOTIFY_KEY = 'af_notify';

// App Global State
let projectsList = [];
let currentProject = null;
let sessionsList = [];
let currentSessionId = null;
let currentSessionData = null;
let currentRunId = null;
let currentRun = null;
let currentEvents = [];
let currentToolCalls = [];
let playheadIndex = -1; // -1 means live/end edge
let isLive = true;
let eventSource = null;
let projectFiles = [];
let openPanelName = null;
let stopConfirmTimer = null;
let clockInterval = null;
let brailleIdx = 0;
const BRAILLE_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

// Notification state tracking
let lastNotifiedRunId = null;

// Config state
let mcpServersList = [];
let requirementsAwaiting = false;

// Chat thread auto-scroll state
let isFollowing = true;
let userPausedScroll = false;

// -------------------------------------------------------------------
// Helper: Is Run Live & Run State
// -------------------------------------------------------------------
function isRunLive() {
    return Boolean(currentRun && !currentRun.finished_at && !currentRun.interrupted && currentRun.is_active !== false);
}

function updateLiveClass() {
    if (typeof document === 'undefined') return;
    const live = isRunLive();
    document.body.classList.toggle('run-live', live);
    if (live) {
        startLiveClock();
    } else {
        stopLiveClock();
    }
}

function startLiveClock() {
    if (clockInterval) return;
    clockInterval = setInterval(() => {
        if (!isRunLive()) {
            stopLiveClock();
            updateTimelineLanes();
            updatePlayheadUI();
            updateTransportUI();
            return;
        }
        brailleIdx = (brailleIdx + 1) % BRAILLE_FRAMES.length;
        updateTimelineLanes();
        if (isLive) updatePlayheadUI();
        updateTransportStatus();
    }, 120);
}

function stopLiveClock() {
    if (clockInterval) {
        clearInterval(clockInterval);
        clockInterval = null;
    }
}

// -------------------------------------------------------------------
// Project & Query Helpers
// -------------------------------------------------------------------
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

// -------------------------------------------------------------------
// Desktop Notifications
// -------------------------------------------------------------------
function notifyEnabled() {
    try {
        return localStorage.getItem(NOTIFY_KEY) === 'on';
    } catch (e) {
        return false;
    }
}

function updateNotifyIcon() {
    const iconUse = document.querySelector('#notify-icon use');
    const btn = document.getElementById('notify-toggle');
    const on = notifyEnabled();
    if (iconUse) {
        iconUse.setAttribute('href', on ? '#icon-bell' : '#icon-bell-off');
    }
    if (btn) {
        btn.classList.toggle('active', on);
    }
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

// -------------------------------------------------------------------
// Sessions & Navigation
// -------------------------------------------------------------------
function cleanSessionTitle(title, fallback) {
    if (!title) return fallback || 'session';
    let clean = String(title).replace(/^@\S+\s*/, '').trim();
    return clean || title || fallback || 'session';
}

async function loadSessions(selectFirst = false) {
    try {
        const pq = projectQuery();
        const url = pq ? `/api/sessions?${pq}` : '/api/sessions';
        const res = await fetch(url);
        if (!res.ok) return;
        const data = await res.json();
        const rawSessions = data.sessions || [];

        const detailedSessions = await Promise.all(rawSessions.map(async (s) => {
            try {
                const sRes = await fetch(`/api/sessions/${encodeURIComponent(s.session_id)}` + (pq ? `?${pq}` : ''));
                if (sRes.ok) {
                    const detail = await sRes.json();
                    return { ...s, ...detail };
                }
            } catch (err) {}
            return s;
        }));

        sessionsList = detailedSessions;
        renderSessionRail();

        if (selectFirst && sessionsList.length > 0 && !currentSessionId) {
            selectSession(sessionsList[0].session_id);
        }
    } catch (e) {
        sessionsList = [];
        renderSessionRail();
    }
}

function renderSessionRail() {
    const container = document.getElementById('session-list');
    if (!container) return;

    if (sessionsList.length === 0) {
        container.innerHTML = `<div style="padding: 1.5ch; color: var(--text-faint); font-size: 11px;">no sessions yet</div>`;
        return;
    }

    container.innerHTML = sessionsList.map(s => {
        const isActive = s.session_id === currentSessionId;
        const marker = isActive ? '›' : '·';
        const rawTitle = s.title || s.first_goal || (s.runs && s.runs[0] && s.runs[0].goal) || s.session_id;
        const title = escapeHtml(cleanSessionTitle(rawTitle, s.session_id));
        const runsCount = s.run_count || (s.runs && s.runs.length) || 1;
        const costStr = typeof s.total_cost === 'number' ? `$${s.total_cost.toFixed(2)}` : '$0.00';
        const ts = typeof s.updated_at === 'number' ? s.updated_at : (typeof s.created_at === 'number' ? s.created_at : (s.runs && s.runs[0] && s.runs[0].started_at));
        const dateInfo = formatSessionDate(ts);

        return `
            <div class="session-row ${isActive ? 'active' : ''}" data-session-id="${escapeHtml(s.session_id)}" title="${escapeHtml(rawTitle)}">
                <div class="session-row-title">
                    <span class="session-marker">${marker}</span>
                    <span class="session-title-text">${title}</span>
                </div>
                <div class="session-row-meta tabular-nums">
                    <span>${runsCount} run${runsCount === 1 ? '' : 's'}</span>
                    <span>·</span>
                    <span>${costStr}</span>
                    ${dateInfo ? `<span class="session-date" title="${escapeHtml(dateInfo.fullTitle)}">${escapeHtml(dateInfo.text)}</span>` : ''}
                </div>
            </div>
        `;
    }).join('');

    container.querySelectorAll('.session-row').forEach(row => {
        row.addEventListener('click', () => {
            const sid = row.dataset.sessionId;
            if (sid) selectSession(sid);
        });
    });
}

function setSessionRailOpen(open) {
    const rail = document.getElementById('session-rail');
    const toggle = document.getElementById('mobile-rail-toggle');
    const backdrop = document.getElementById('rail-backdrop');
    if (rail) rail.classList.toggle('mobile-open', open);
    if (toggle) toggle.setAttribute('aria-expanded', String(open));
    if (backdrop) backdrop.hidden = !open;
    document.body.classList.toggle('rail-open', open);
}

async function selectSession(sessionId) {
    currentSessionId = sessionId;
    renderSessionRail();

    setSessionRailOpen(false);

    try {
        const pq = projectQuery();
        const url = pq ? `/api/sessions/${encodeURIComponent(sessionId)}?${pq}` : `/api/sessions/${encodeURIComponent(sessionId)}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error('Session not found');
        currentSessionData = await res.json();

        const rawTitle = currentSessionData.title || (currentSessionData.runs && currentSessionData.runs[0] && currentSessionData.runs[0].goal) || currentSessionData.session_id;
        const title = cleanSessionTitle(rawTitle);

        const topbarSession = document.getElementById('topbar-session');
        if (topbarSession) {
            topbarSession.textContent = `session · "${title}"`;
            topbarSession.title = rawTitle;
        }

        const runs = currentSessionData.runs || [];
        if (runs.length > 0) {
            const latestRun = runs[runs.length - 1];
            await selectRun(latestRun.run_id);
        } else {
            resetRunView();
        }
    } catch (e) {
        console.error('Failed to load session:', e);
        resetRunView();
    }
}

function startNewSession() {
    currentSessionId = null;
    currentSessionData = null;
    currentRunId = null;
    currentRun = null;
    currentEvents = [];
    currentToolCalls = [];
    playheadIndex = -1;
    isLive = true;

    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }

    renderSessionRail();
    setSessionRailOpen(false);

    const topbarSession = document.getElementById('topbar-session');
    if (topbarSession) {
        topbarSession.textContent = 'new session';
        topbarSession.removeAttribute('title');
    }

    resetRunView();

    const input = document.getElementById('transport-input');
    if (input) {
        input.placeholder = 'describe a run to start…';
        input.value = '';
        input.focus();
    }
}

function resetRunView() {
    updateLiveClass();
    updateTimelineLanes();
    renderRulerTicks();
    updatePlayheadUI();

    const thread = document.getElementById('thread-content');
    if (thread) {
        thread.innerHTML = `
            <div class="empty-thread-block">
                <div class="empty-thread-title mono">no runs yet</div>
                <div class="empty-thread-sub mono">describe a run in the bar below to start ↓</div>
            </div>
        `;
    }

    const stopBtn = document.getElementById('transport-stop');
    if (stopBtn) stopBtn.disabled = true;

    const queuePill = document.getElementById('transport-queue-pill');
    if (queuePill) queuePill.hidden = true;

    const statusText = document.getElementById('transport-status-text');
    if (statusText) statusText.textContent = 'idle';

    const input = document.getElementById('transport-input');
    if (input) input.placeholder = 'describe a run to start…';
}

// -------------------------------------------------------------------
// Run Loading & SSE Stream
// -------------------------------------------------------------------
async function selectRun(runId) {
    currentRunId = runId;
    isLive = true;
    playheadIndex = -1;

    try {
        const pq = projectQuery();
        const q = pq ? `?${pq}` : '';
        const [runRes, eventsRes, callsRes] = await Promise.all([
            fetch(`/api/runs/${encodeURIComponent(runId)}${q}`),
            fetch(`/api/runs/${encodeURIComponent(runId)}/events${q}`),
            fetch(`/api/runs/${encodeURIComponent(runId)}/tool_calls${q}`)
        ]);

        if (runRes.ok) {
            currentRun = await runRes.json();
            // Classify zombie runs up front
            if (currentRun.finished_at === null && (currentRun.is_active === false || currentRun.interrupted)) {
                currentRun.interrupted = true;
            }
            const topbarSession = document.getElementById('topbar-session');
            if (topbarSession && currentRun.goal) {
                topbarSession.title = currentRun.goal;
            }
        }

        if (eventsRes.ok) {
            const evData = await eventsRes.json();
            currentEvents = evData.events || [];
        } else {
            currentEvents = [];
        }

        if (callsRes.ok) {
            const callData = await callsRes.json();
            currentToolCalls = callData.tool_calls || [];
        } else {
            currentToolCalls = [];
        }

        // Set playhead default to the end edge
        playheadIndex = currentEvents.length > 0 ? currentEvents.length - 1 : -1;
        isLive = true;

        updateLiveClass();

        // Connect SSE stream only if run is active
        if (isRunLive()) {
            connectRunStream(runId);
        } else if (eventSource) {
            eventSource.close();
            eventSource = null;
        }

        // Update UI
        updateTimelineLanes();
        renderRulerTicks();
        updatePlayheadUI();
        renderThread();
        updateTransportUI();
    } catch (e) {
        console.error('Failed to load run detail:', e);
    }
}

function connectRunStream(runId) {
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }

    if (!isRunLive()) {
        return;
    }

    const pq = projectQuery();
    const url = `/api/runs/${encodeURIComponent(runId)}/stream` + (pq ? `?${pq}` : '');
    eventSource = new EventSource(url);

    eventSource.onopen = () => {
        updateTransportStatus();
    };

    eventSource.onerror = () => {
        if (eventSource && eventSource.readyState === EventSource.CONNECTING) {
            const statusText = document.getElementById('transport-status-text');
            if (statusText) statusText.textContent = 'reconnecting…';
        }
    };

    // Generic message handler
    eventSource.onmessage = (e) => {
        try {
            const payload = JSON.parse(e.data);
            handleStreamEvent({ type: 'message', payload });
        } catch (err) {}
    };

    // Named SSE event listeners
    const eventTypes = [
        'run_started',
        'step_started',
        'step_finished',
        'tool_call',
        'blocker',
        'user_message',
        'interrupted',
        'run_finished',
        'requirements_pending',
        'requirements_answer',
        'done'
    ];

    eventTypes.forEach(type => {
        eventSource.addEventListener(type, (e) => {
            if (type === 'done') {
                if (eventSource) {
                    eventSource.close();
                    eventSource = null;
                }
                onRunEnded();
                return;
            }

            let payload = {};
            try {
                payload = JSON.parse(e.data);
            } catch (err) {}

            const seq = parseInt(e.lastEventId || e.id || (currentEvents.length + 1), 10);
            const newEvent = { seq, type, payload, ts: Date.now() / 1000 };
            handleStreamEvent(newEvent);
        });
    });
}

function handleStreamEvent(event) {
    if (event.seq && currentEvents.some(ev => ev.seq === event.seq)) {
        return;
    }

    currentEvents.push(event);

    if (event.type === 'run_started') {
        if (currentRun) {
            currentRun.goal = event.payload.goal || currentRun.goal;
            currentRun.started_at = event.payload.started_at || currentRun.started_at;
        }
    } else if (event.type === 'step_finished') {
        if (currentRun) {
            if (!currentRun.steps) currentRun.steps = [];
            currentRun.steps.push(event.payload);
        }
    } else if (event.type === 'tool_call') {
        currentToolCalls.push(event.payload);
    } else if (event.type === 'blocker') {
        if (currentRun) {
            if (!currentRun.blockers) currentRun.blockers = [];
            currentRun.blockers.push(event.payload);
        }
        if (event.payload.fatal) {
            maybeNotify('agentflow — run blocked', event.payload.detail || '');
        }
    } else if (event.type === 'user_message') {
        const queuePill = document.getElementById('transport-queue-pill');
        if (queuePill) queuePill.hidden = true;
    } else if (event.type === 'requirements_pending') {
        requirementsAwaiting = true;
        const input = document.getElementById('transport-input');
        if (input) input.placeholder = 'answer the requirements question…';
        const statusText = document.getElementById('transport-status-text');
        if (statusText) statusText.textContent = 'awaiting your requirements';
    } else if (event.type === 'requirements_answer') {
        requirementsAwaiting = false;
        const input = document.getElementById('transport-input');
        if (input) input.placeholder = 'describe a run to start…';
    } else if (event.type === 'interrupted') {
        if (currentRun) {
            currentRun.interrupted = true;
        }
        onRunEnded();
        return;
    } else if (event.type === 'run_finished') {
        if (currentRun) {
            currentRun.finished_at = event.payload.finished_at || (Date.now() / 1000);
            currentRun.pushed = event.payload.pushed || currentRun.pushed;
        }
        onRunEnded();
        return;
    }

    updateLiveClass();

    if (isLive) {
        playheadIndex = currentEvents.length - 1;
        updateTimelineLanes();
        renderRulerTicks();
        updatePlayheadUI();
        renderThread();
    } else {
        renderRulerTicks();
    }

    updateTransportUI();
}

function onRunEnded() {
    if (currentRun && !currentRun.finished_at && !currentRun.interrupted) {
        currentRun.finished_at = Date.now() / 1000;
    }

    updateLiveClass();
    updateTimelineLanes();
    updatePlayheadUI();
    renderThread();
    updateTransportUI();

    if (currentRun && currentRun.run_id !== lastNotifiedRunId) {
        lastNotifiedRunId = currentRun.run_id;
        const status = runStatus(currentRun);
        maybeNotify(`agentflow — run ${status.label.toLowerCase()}`, (currentRun.goal || '').slice(0, 100));
    }

    loadSessions(false);
}

// -------------------------------------------------------------------
// Timeline & Playhead
// -------------------------------------------------------------------
function updateTimelineLanes() {
    const roles = ['review', 'build', 'verify'];
    const steps = (currentRun && currentRun.steps) || [];
    const maxIterations = (currentRun && currentRun.config && currentRun.config.max_iterations) || 3;
    const running = isRunLive();

    let activeRole = null;
    if (running) {
        const lastEv = currentEvents[currentEvents.length - 1];
        if (lastEv && lastEv.type === 'step_started') {
            activeRole = lastEv.payload.role;
        } else if (steps.length > 0) {
            activeRole = steps[steps.length - 1].role;
        } else {
            activeRole = 'review';
        }
    }

    roles.forEach(role => {
        const fillEl = document.getElementById(`lane-fill-${role}`);
        const statusEl = document.getElementById(`lane-status-${role}`);
        const costEl = document.getElementById(`lane-cost-${role}`);
        if (!fillEl || !statusEl || !costEl) return;

        const roleSteps = steps.filter(s => s.role === role);
        const roleCost = roleSteps.reduce((sum, s) => sum + (Number(s.usage && s.usage.cost_usd) || 0), 0);

        costEl.textContent = roleCost > 0 ? `$${roleCost.toFixed(roleCost < 0.01 ? 4 : 2)}` : '';

        // Reset classes
        fillEl.className = 'lane-fill';
        statusEl.className = 'lane-status mono';

        if (role === 'review') {
            const hasReviewStep = roleSteps.length > 0;
            if (activeRole === 'review') {
                fillEl.style.width = '100%';
                fillEl.classList.add('running');
                statusEl.textContent = BRAILLE_FRAMES[brailleIdx];
                statusEl.classList.add('active');
            } else if (hasReviewStep) {
                fillEl.style.width = '100%';
                statusEl.textContent = '✓';
            } else if (currentRun && currentRun.interrupted) {
                fillEl.style.width = '0%';
                statusEl.textContent = '·';
            } else {
                fillEl.style.width = '0%';
                statusEl.textContent = '·';
            }
        } else if (role === 'build') {
            const hasBuildSteps = roleSteps.length > 0;
            const currentIter = roleSteps.length;
            const progressPct = Math.min(100, Math.round((currentIter / maxIterations) * 100));

            if (activeRole === 'build') {
                fillEl.style.width = `${Math.max(15, progressPct)}%`;
                fillEl.classList.add('running');
                statusEl.textContent = BRAILLE_FRAMES[brailleIdx];
                statusEl.classList.add('active');
            } else if (currentRun && currentRun.interrupted) {
                fillEl.style.width = `${progressPct || 0}%`;
                fillEl.classList.add('interrupted');
                statusEl.textContent = '·';
            } else if (currentRun && currentRun.stopped) {
                fillEl.style.width = `${progressPct}%`;
                fillEl.classList.add('stopped');
                statusEl.textContent = '▪';
            } else if (currentRun && currentRun.error) {
                fillEl.style.width = `${progressPct || 50}%`;
                fillEl.classList.add('failed');
                statusEl.textContent = '!';
                statusEl.classList.add('failed');
            } else if (hasBuildSteps) {
                fillEl.style.width = '100%';
                statusEl.textContent = '✓';
            } else {
                fillEl.style.width = '0%';
                statusEl.textContent = '·';
            }
        } else if (role === 'verify') {
            const hasVerifyStep = roleSteps.length > 0;
            const lastVerify = roleSteps[roleSteps.length - 1];
            const verdict = lastVerify ? verifyVerdict(lastVerify.text) : null;

            if (activeRole === 'verify') {
                fillEl.style.width = '100%';
                fillEl.classList.add('running');
                statusEl.textContent = BRAILLE_FRAMES[brailleIdx];
                statusEl.classList.add('active');
            } else if (hasVerifyStep) {
                if (verdict === 'PASS' || (lastVerify && lastVerify.success)) {
                    fillEl.style.width = '100%';
                    statusEl.textContent = '✓';
                } else if (verdict === 'FAIL' || (lastVerify && lastVerify.success === false)) {
                    fillEl.style.width = '100%';
                    fillEl.classList.add('failed');
                    statusEl.textContent = '!';
                    statusEl.classList.add('failed');
                } else {
                    fillEl.style.width = '100%';
                    statusEl.textContent = '✓';
                }
            } else {
                fillEl.style.width = '0%';
                statusEl.textContent = '·';
            }
        }
    });
}

function renderRulerTicks() {
    const ticksContainer = document.getElementById('ruler-ticks');
    if (!ticksContainer) return;

    if (currentEvents.length <= 1) {
        ticksContainer.innerHTML = '';
        return;
    }

    const total = currentEvents.length - 1;
    ticksContainer.innerHTML = currentEvents.map((ev, i) => {
        const pct = (i / total) * 100;
        const isStep = ev.type === 'step_started' || ev.type === 'step_finished';
        return `<div class="ruler-tick ${isStep ? 'step' : ''}" style="left: ${pct}%;"></div>`;
    }).join('');
}

function updatePlayheadUI() {
    const thumb = document.getElementById('playhead-thumb');
    const rulerTime = document.getElementById('ruler-time');
    const snapBtn = document.getElementById('btn-snap-live');
    const scrubBanner = document.getElementById('scrub-banner');
    const scrubLabel = document.getElementById('scrub-label');

    let pct = 100;
    if (!isLive && currentEvents.length > 1 && playheadIndex >= 0) {
        pct = (playheadIndex / (currentEvents.length - 1)) * 100;
    }

    if (thumb) {
        thumb.style.left = `${pct}%`;
    }

    const running = isRunLive();

    if (snapBtn) {
        if (!running) {
            snapBtn.hidden = true;
        } else {
            if (!isLive) {
                snapBtn.hidden = false;
                snapBtn.classList.add('live');
            } else {
                snapBtn.hidden = true;
            }
        }
    }

    const start = (currentRun && currentRun.started_at) || 0;
    let elapsedSec = 0;

    if (isLive) {
        if (currentRun && currentRun.finished_at && start) {
            elapsedSec = Math.max(0, currentRun.finished_at - start);
        } else if (currentRun && currentRun.interrupted && start) {
            const lastEv = currentEvents.length > 0 ? currentEvents[currentEvents.length - 1] : null;
            elapsedSec = (lastEv && lastEv.ts && lastEv.ts > start) ? Math.max(0, lastEv.ts - start) : 0;
        } else if (running && start) {
            elapsedSec = Math.max(0, (Date.now() / 1000) - start);
        }
        if (scrubBanner) scrubBanner.hidden = true;
    } else {
        const currentEv = currentEvents[playheadIndex];
        const evTs = (currentEv && currentEv.ts) || (start + (playheadIndex * 2));
        elapsedSec = start ? Math.max(0, evTs - start) : 0;

        if (scrubBanner) {
            scrubBanner.hidden = false;
            if (scrubLabel) {
                scrubLabel.textContent = `scrubbed to ${formatUnifiedDuration(elapsedSec)}`;
            }
        }
    }

    if (rulerTime) {
        rulerTime.textContent = formatUnifiedDuration(elapsedSec);
    }
}

function setPlayhead(index, isUserInteraction = true) {
    if (index < 0 || index >= currentEvents.length - 1) {
        isLive = true;
        playheadIndex = currentEvents.length > 0 ? currentEvents.length - 1 : -1;
        userPausedScroll = false;
        isFollowing = true;
    } else {
        isLive = false;
        playheadIndex = index;
    }

    updatePlayheadUI();
    renderThread();

    if (isUserInteraction && !isLive && currentEvents.length > 0) {
        const visibleEvents = currentEvents.slice(0, playheadIndex + 1);
        let targetStepIndex = -1;
        visibleEvents.forEach(ev => {
            if (ev.type === 'step_started') targetStepIndex++;
        });
        if (targetStepIndex >= 0) {
            setTimeout(() => scrollToStepBubble(targetStepIndex), 0);
        }
    }
}

function setupPlayheadControls() {
    const ruler = document.getElementById('playhead-ruler');
    const snapBtn = document.getElementById('btn-snap-live');
    const returnLiveBtn = document.getElementById('btn-return-live');

    if (snapBtn) {
        snapBtn.addEventListener('click', () => setPlayhead(-1));
    }
    if (returnLiveBtn) {
        returnLiveBtn.addEventListener('click', () => setPlayhead(-1));
    }

    if (!ruler) return;

    function handleRulerClick(e) {
        if (currentEvents.length <= 1) return;
        const rect = ruler.getBoundingClientRect();
        const clientX = e.clientX || (e.touches && e.touches[0] && e.touches[0].clientX);
        if (clientX === undefined) return;
        const fraction = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
        const targetIndex = Math.round(fraction * (currentEvents.length - 1));
        setPlayhead(targetIndex);
    }

    let isDragging = false;

    ruler.addEventListener('mousedown', (e) => {
        isDragging = true;
        handleRulerClick(e);
    });

    window.addEventListener('mousemove', (e) => {
        if (isDragging) handleRulerClick(e);
    });

    window.addEventListener('mouseup', () => {
        isDragging = false;
    });

    ruler.addEventListener('touchstart', (e) => {
        isDragging = true;
        handleRulerClick(e);
    }, { passive: true });

    window.addEventListener('touchmove', (e) => {
        if (isDragging) handleRulerClick(e);
    }, { passive: true });

    window.addEventListener('touchend', () => {
        isDragging = false;
    });

    ruler.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowLeft') {
            e.preventDefault();
            const curr = isLive ? currentEvents.length - 1 : playheadIndex;
            setPlayhead(Math.max(0, curr - 1));
        } else if (e.key === 'ArrowRight') {
            e.preventDefault();
            const curr = isLive ? currentEvents.length - 1 : playheadIndex;
            setPlayhead(curr + 1);
        } else if (e.key === ' ') {
            e.preventDefault();
            setPlayhead(isLive ? (playheadIndex >= 0 ? playheadIndex : 0) : -1);
        }
    });
}

// -------------------------------------------------------------------
// Thread & Run Header Rendering
// -------------------------------------------------------------------
function getRoleModels(run) {
    if (!run) return '';
    const cfg = run.config || {};
    const roles = ['review', 'build', 'verify'];
    const parts = [];
    roles.forEach(r => {
        const rCfg = cfg[r] || {};
        const b = rCfg.backend || cfg[`${r}_backend`] || '';
        const m = rCfg.model || cfg[`${r}_model`] || '';
        let desc = '';
        if (b && m) desc = `${b}/${m}`;
        else if (b) desc = b;
        else if (m) desc = m;

        if (!desc && Array.isArray(run.steps)) {
            const step = run.steps.find(s => s.role === r);
            if (step) {
                const u = step.usage || {};
                const sb = u.backend || step.backend || '';
                const sm = u.model || step.model || '';
                if (sb && sm) desc = `${sb}/${sm}`;
                else desc = sb || sm || '';
            }
        }
        if (desc) {
            parts.push(`${r} ${desc}`);
        }
    });
    return parts.join(' · ');
}

function renderRunHeader(run) {
    if (!run) return '';
    const goal = run.goal || '';
    const st = runStatus(run);
    const start = run.started_at || 0;
    let duration = 0;
    if (run.finished_at && start) {
        duration = Math.max(0, run.finished_at - start);
    } else if (run.interrupted && start) {
        const lastEv = currentEvents.length > 0 ? currentEvents[currentEvents.length - 1] : null;
        duration = (lastEv && lastEv.ts && lastEv.ts > start) ? Math.max(0, lastEv.ts - start) : 0;
    } else if (isRunLive() && start) {
        duration = Math.max(0, (Date.now() / 1000) - start);
    }

    const durStr = formatUnifiedDuration(duration);
    const steps = run.steps || [];
    const totalCost = steps.reduce((sum, s) => sum + (Number(s.usage && s.usage.cost_usd) || 0), 0);
    const costStr = totalCost > 0 ? `$${totalCost.toFixed(totalCost < 0.01 ? 4 : 2)}` : '$0.00';
    const roleModels = getRoleModels(run);
    const shortRunId = run.run_id ? (run.run_id.startsWith('run-') ? run.run_id : `run-${run.run_id.slice(0, 8)}`) : '';

    return `
        <div class="run-header mono">
            <div class="run-goal-wrap">
                <span class="run-goal-label">GOAL</span>
                <div class="run-goal-text">${escapeHtml(goal || 'No goal recorded.')}</div>
            </div>
            <div class="run-meta-line tabular-nums">
                <span class="run-id">${escapeHtml(shortRunId || run.run_id || '')}</span>
                <span>·</span>
                <span class="run-state-badge ${st.cls}">${escapeHtml(st.label.toLowerCase())}</span>
                <span>·</span>
                <span>${durStr}</span>
                <span>·</span>
                <span>${costStr}</span>
                ${roleModels ? `<span>·</span><span class="run-models">${escapeHtml(roleModels)}</span>` : ''}
            </div>
        </div>
    `;
}

function renderThread() {
    const container = document.getElementById('thread-content');
    if (!container) return;

    if (!currentRun && currentEvents.length === 0) {
        container.innerHTML = `
            <div class="empty-thread-block">
                <div class="empty-thread-title mono">no runs yet</div>
                <div class="empty-thread-sub mono">describe a run in the bar below to start ↓</div>
            </div>
        `;
        return;
    }

    const visibleEvents = isLive ? currentEvents : currentEvents.slice(0, playheadIndex + 1);

    // Build chronological chat turns from the visible event slice.
    const turns = [];
    let assistantTurn = null;
    let assistantStepIndex = 0;
    const fatalBlockers = [];

    visibleEvents.forEach(ev => {
        if (ev.type === 'user_message') {
            turns.push({ type: 'user', payload: ev.payload, seq: ev.seq, ts: ev.ts });
        } else if (ev.type === 'step_started') {
            assistantTurn = {
                type: 'assistant',
                role: ev.payload.role,
                model: ev.payload.model,
                backend: ev.payload.backend,
                iteration: ev.payload.iteration,
                started_at: ev.ts,
                seq: ev.seq,
                liveText: '',
                toolCalls: [],
                stepIndex: assistantStepIndex
            };
        } else if (ev.type === 'text_delta') {
            if (assistantTurn) assistantTurn.liveText += (ev.payload.delta || '');
        } else if (ev.type === 'tool_call') {
            if (assistantTurn) assistantTurn.toolCalls.push(ev.payload);
        } else if (ev.type === 'step_finished') {
            if (assistantTurn) {
                turns.push({ ...assistantTurn, ...ev.payload, finished_at: ev.ts, toolCalls: assistantTurn.toolCalls });
                assistantTurn = null;
                assistantStepIndex++;
            } else {
                turns.push({ type: 'assistant', ...ev.payload, finished_at: ev.ts, toolCalls: [], stepIndex: assistantStepIndex });
                assistantStepIndex++;
            }
        } else if (ev.type === 'blocker') {
            if (ev.payload.fatal) fatalBlockers.push(ev.payload);
            turns.push({ type: 'blocker', payload: ev.payload, seq: ev.seq, ts: ev.ts });
        } else if (ev.type === 'requirements_pending') {
            turns.push({ type: 'requirements_pending', payload: ev.payload, seq: ev.seq, ts: ev.ts });
        } else if (ev.type === 'requirements_answer') {
            turns.push({ type: 'requirements_answer', payload: ev.payload, seq: ev.seq, ts: ev.ts });
        }
    });

    // Also surface persisted blockers for completed runs when the event log lacks them.
    if (currentRun && Array.isArray(currentRun.blockers) && fatalBlockers.length === 0) {
        currentRun.blockers.forEach(b => {
            if (b.fatal) fatalBlockers.push(b);
        });
    }

    let html = '';

    if (currentRun) {
        html += renderRunHeader(currentRun);
    }

    if (currentSessionData && Array.isArray(currentSessionData.runs) && currentSessionData.runs.length > 1) {
        html += renderSessionThread(currentSessionData, currentRunId);
    }

    if (fatalBlockers.length > 0) {
        html += '<div class="blockers">';
        fatalBlockers.forEach(b => {
            const reason = blockerLabel(b.reason);
            const { prose: cleanDetail } = splitToolBlocks(b.detail || '');
            html += `
                <div class="blocker fatal">
                    <span class="blocker-reason">${escapeHtml(reason)}</span>
                    <span class="blocker-detail">${escapeHtml(cleanDetail || b.detail || '')}</span>
                </div>
            `;
        });
        html += '</div>';
    }

    html += '<div class="chat-thread">';

    if (turns.length === 0 && !assistantTurn) {
        html += `<div class="chat-bubble system"><p class="md step-noresponse">Waiting for step events…</p></div>`;
    } else {
        turns.forEach((turn, idx) => {
            if (turn.type === 'user') {
                html += renderUserBubble(turn.payload);
            } else if (turn.type === 'assistant') {
                html += renderAssistantBubble(turn, idx, turn.toolCalls);
            } else if (turn.type === 'blocker') {
                const b = turn.payload;
                const reason = blockerLabel(b.reason);
                const { prose: cleanDetail } = splitToolBlocks(b.detail || '');
                html += `
                    <div class="chat-bubble system ${b.fatal ? 'fatal' : ''}">
                        <div class="system-blocker">
                            <span class="system-blocker-reason">${escapeHtml(reason)}${b.fatal ? ' (fatal)' : ''}</span>
                            <span class="system-blocker-detail">${escapeHtml(cleanDetail || b.detail || '')}</span>
                        </div>
                    </div>
                `;
            } else if (turn.type === 'requirements_pending') {
                const questions = (turn.payload.questions || []).map(q => `<li>${escapeHtml(String(q))}</li>`).join('');
                html += `
                    <div class="chat-bubble system requirements">
                        <div class="system-requirements-title">awaiting your requirements</div>
                        ${questions ? `<ol class="system-requirements-list">${questions}</ol>` : `<p class="md">${escapeHtml(turn.payload.body || '')}</p>`}
                    </div>
                `;
            } else if (turn.type === 'requirements_answer') {
                html += renderUserBubble({ body: turn.payload.body || '' }, 'answer');
            }
        });
    }

    // Live in-progress assistant turn appended at the bottom.
    if (assistantTurn) {
        html += renderLiveAssistantBubble(assistantTurn);
    }

    html += '</div>';

    container.innerHTML = html;

    container.querySelectorAll('.step-header').forEach(hdr => {
        hdr.addEventListener('click', () => {
            const item = hdr.closest('.step-item');
            const body = item.querySelector('.step-body');
            const caret = hdr.querySelector('.step-caret');
            if (body) {
                const hidden = body.hidden;
                body.hidden = !hidden;
                if (caret) caret.classList.toggle('open', !body.hidden);
            }
        });
    });

    container.querySelectorAll('.tool-call-header').forEach(hdr => {
        hdr.addEventListener('click', () => {
            const item = hdr.closest('.tool-call-item');
            const body = item.querySelector('.tool-call-body');
            const caret = hdr.querySelector('.step-caret');
            if (body) {
                const hidden = body.hidden;
                body.hidden = !hidden;
                if (caret) caret.classList.toggle('open', !body.hidden);
            }
        });
    });

    container.querySelectorAll('.bubble-tool-toggle').forEach(btn => {
        btn.addEventListener('click', () => {
            const group = btn.closest('.bubble-tool-group');
            const list = group.querySelector('.bubble-tool-list');
            const caret = btn.querySelector('.step-caret');
            if (list) {
                const hidden = list.hidden;
                list.hidden = !hidden;
                btn.setAttribute('aria-expanded', String(!hidden));
                if (caret) caret.classList.toggle('open', !list.hidden);
            }
        });
    });

    container.querySelectorAll('.session-turn:not(.current)').forEach(turn => {
        turn.addEventListener('click', () => {
            const targetRunId = turn.dataset.runId;
            if (targetRunId) selectRun(targetRunId);
        });
    });

    maybeScrollThreadToBottom();
}

function renderStep(step, index) {
    const role = step.role || 'step';
    const isVerify = role === 'verify';
    const isBuild = role === 'build';
    const isFailedBuild = isBuild && step.success === false;
    const verdict = isVerify ? verifyVerdict(step.text) : (step.success === false ? 'FAIL' : (step.success ? 'PASS' : null));

    let verdictHtml = '';
    if (verdict) {
        verdictHtml = `<span class="step-verdict ${verdict === 'PASS' ? 'pass' : 'fail'}">${verdict}</span>`;
    }

    const usage = step.usage || {};
    const backend = usage.backend || step.backend || '';
    const model = usage.model || step.model || '';
    const backendModel = (backend && model) ? `${backend} · ${model}` : (backend || model || '');
    const cost = fmtCost(usage) || (typeof step.cost_usd === 'number' ? `$${step.cost_usd.toFixed(4)}` : '');
    const iter = step.iteration ? `iter ${step.iteration}` : '';

    const { prose, requests } = splitToolBlocks(step.text);
    const isEmptyStep = Boolean(step.no_response || (!prose && !requests.length));

    let bodyHtml = '';
    if (isEmptyStep) {
        bodyHtml = `<div class="md step-noresponse">${renderMarkdown(prose || step.text || 'No written response recorded.')}</div>`;
    } else {
        if (requests.length) {
            bodyHtml += `<div class="tool-reqs">${requests.map(renderToolReq).join('')}</div>`;
        }
        if (prose) {
            bodyHtml += `<div class="md">${renderMarkdown(prose)}</div>`;
        }
    }

    const metaParts = [];
    if (backendModel) metaParts.push(`<span>${escapeHtml(backendModel)}</span>`);
    if (isFailedBuild && step.text && step.text.startsWith('Stopped:')) {
        metaParts.push(`<span class="mono" style="color: var(--status-red);">${escapeHtml(step.text.slice(0, 90))}</span>`);
    } else if (isFailedBuild) {
        metaParts.push(`<span class="mono" style="color: var(--status-red);">failed</span>`);
    }
    if (cost) metaParts.push(`<span>${cost}</span>`);

    return `
        <div class="step-item ${isEmptyStep ? 'empty-step' : ''} ${isFailedBuild ? 'failed-build-step' : ''}">
            <div class="step-header">
                <div class="step-header-left">
                    <span class="step-caret ${isFailedBuild ? '' : 'open'}"><svg class="icon"><use href="#icon-chevron-right"/></svg></span>
                    <span class="step-role mono">${escapeHtml(role)}</span>
                    ${iter ? `<span class="mono" style="color: var(--text-faint);">· ${escapeHtml(iter)}</span>` : ''}
                    ${verdictHtml}
                </div>
                <div class="step-meta mono">
                    ${metaParts.join(' · ')}
                </div>
            </div>
            <div class="step-body" ${isFailedBuild ? 'hidden' : ''}>${bodyHtml}</div>
        </div>
    `;
}

function summarizeStepChanges(step, toolCalls) {
    const files = [];
    const seenPaths = new Set();
    const writeTools = ['WriteFile', 'EditFile', 'ApplyDiff', 'PatchFile'];

    (toolCalls || []).forEach(call => {
        const name = call.tool_name || '';
        const result = (call.result && typeof call.result === 'object') ? call.result : {};
        const structured = result.structured || {};
        const args = call.args || {};

        if (structured.current !== undefined && structured.path) {
            if (!seenPaths.has(structured.path)) {
                seenPaths.add(structured.path);
                files.push({ path: structured.path, op: structured.previous !== undefined ? 'edit' : 'write' });
            }
        } else if (writeTools.includes(name) && args.path) {
            if (!seenPaths.has(args.path)) {
                seenPaths.add(args.path);
                files.push({ path: args.path, op: 'write' });
            }
        } else if (name === 'Shell' && args.command) {
            const cmd = String(args.command);
            const m = cmd.match(/git\s+diff(?:\s+--[^\s]+)*\s+(.+)/);
            if (m && m[1]) {
                m[1].trim().split(/\s+/).forEach(p => {
                    if (p && !p.startsWith('-') && !seenPaths.has(p)) {
                        seenPaths.add(p);
                        files.push({ path: p, op: 'edit' });
                    }
                });
            }
        }
    });

    return { files, toolCount: (toolCalls || []).length };
}

function renderChangesFooter(step, toolCalls) {
    const { files, toolCount } = summarizeStepChanges(step, toolCalls);
    if (files.length === 0 && toolCount === 0) return '';

    const fileChips = files.map(f => {
        const glyph = f.op === 'write' ? '+' : '±';
        return `<span class="change-chip ${f.op}"><span class="change-glyph">${glyph}</span><span class="change-path mono">${escapeHtml(f.path)}</span></span>`;
    }).join('');

    const toolSummary = toolCount > 0
        ? `<span class="change-tool-count mono">${toolCount} tool call${toolCount === 1 ? '' : 's'}</span>`
        : '';

    return `
        <div class="changes-footer">
            ${fileChips ? `<div class="change-files">${fileChips}</div>` : ''}
            ${toolSummary}
        </div>
    `;
}

function renderBubbleToolCalls(toolCalls) {
    if (!toolCalls || toolCalls.length === 0) return '';
    const count = toolCalls.length;
    const listHtml = toolCalls.map((call, idx) => renderToolCall(call, idx)).join('');
    return `
        <div class="bubble-tool-group">
            <button class="bubble-tool-toggle" type="button" aria-expanded="false">
                <span class="step-caret"><svg class="icon"><use href="#icon-chevron-right"/></svg></span>
                <span class="mono">tool callings (${count})</span>
            </button>
            <div class="bubble-tool-list" hidden>
                ${listHtml}
            </div>
        </div>
    `;
}

function renderAssistantBubble(step, index, toolCalls) {
    const role = step.role || 'step';
    const isLive = step.in_progress === true;
    const bubbleClass = isLive ? 'assistant live' : 'assistant';
    const stepHtml = renderStep(step, index);
    const changesFooter = renderChangesFooter(step, toolCalls);
    const toolsHtml = renderBubbleToolCalls(toolCalls);

    return `
        <div class="chat-bubble ${bubbleClass}" data-step-index="${index}" data-role="${escapeHtml(role)}">
            <div class="bubble-header">
                <span class="bubble-avatar mono">${escapeHtml(role[0] || '·')}</span>
                <span class="bubble-role mono">${escapeHtml(role)}</span>
                ${isLive ? '<span class="bubble-live-pulse" aria-label="working"></span>' : ''}
            </div>
            <div class="bubble-body">
                ${stepHtml}
                ${toolsHtml}
                ${changesFooter}
            </div>
        </div>
    `;
}

function renderLiveAssistantBubble(activeTurn) {
    const role = activeTurn.role || 'step';
    const liveProse = (activeTurn.liveText || '');
    const { prose: liveClean } = splitToolBlocks(liveProse);
    const hasToolCalls = activeTurn.toolCalls && activeTurn.toolCalls.length > 0;

    let bodyHtml = '';
    if (liveClean.trim()) {
        bodyHtml += `<div class="md">${renderMarkdown(liveClean.trim())}</div>`;
    } else if (hasToolCalls) {
        bodyHtml += `<div class="md step-live">working (${activeTurn.toolCalls.length} tool call${activeTurn.toolCalls.length === 1 ? '' : 's'} so far)…</div>`;
    } else {
        bodyHtml += `<div class="md step-live thinking">thinking…</div>`;
    }

    const toolsHtml = renderBubbleToolCalls(activeTurn.toolCalls);

    return `
        <div class="chat-bubble assistant live" data-role="${escapeHtml(role)}">
            <div class="bubble-header">
                <span class="bubble-avatar mono">${escapeHtml(role[0] || '·')}</span>
                <span class="bubble-role mono">${escapeHtml(role)}</span>
                <span class="bubble-live-pulse" aria-label="working"></span>
            </div>
            <div class="bubble-body">
                <div class="step-item step-live-item">
                    <div class="step-header">
                        <div class="step-header-left">
                            <span class="step-caret open"><svg class="icon"><use href="#icon-chevron-right"/></svg></span>
                            <span class="step-role mono">${escapeHtml(role)}</span>
                            <span class="step-verdict pass">…</span>
                            ${activeTurn.iteration ? `<span class="mono" style="color: var(--text-faint);">· iter ${activeTurn.iteration}</span>` : ''}
                        </div>
                        <div class="step-meta mono"><span>working…</span></div>
                    </div>
                    <div class="step-body">${bodyHtml}</div>
                </div>
                ${toolsHtml}
            </div>
        </div>
    `;
}

function renderUserBubble(msg, kind) {
    const body = msg.body || msg.text || '';
    const label = kind === 'answer' ? 'answer' : 'you';
    return `
        <div class="chat-bubble user">
            <div class="bubble-header user-header">
                <span class="bubble-role mono">${label}</span>
            </div>
            <div class="bubble-body user-body">
                <p class="md">${escapeHtml(body)}</p>
            </div>
        </div>
    `;
}

function isThreadNearBottom() {
    const threadArea = document.getElementById('thread-area');
    if (!threadArea) return true;
    const threshold = 60;
    return threadArea.scrollHeight - threadArea.scrollTop - threadArea.clientHeight <= threshold;
}

function updateFollowIndicator() {
    const indicator = document.getElementById('follow-indicator');
    if (!indicator) return;
    const paused = !isFollowing || userPausedScroll;
    indicator.textContent = paused ? 'paused' : 'following';
    indicator.classList.toggle('paused', paused);
    indicator.classList.toggle('following', !paused);
}

function maybeScrollThreadToBottom() {
    const threadArea = document.getElementById('thread-area');
    if (!threadArea) return;
    if (isFollowing && !userPausedScroll) {
        threadArea.scrollTop = threadArea.scrollHeight;
    }
    updateFollowIndicator();
}

function scrollToStepBubble(stepIndex) {
    const bubble = document.querySelector(`.chat-bubble[data-step-index="${stepIndex}"]`);
    if (bubble) {
        bubble.scrollIntoView({ behavior: 'smooth', block: 'center' });
        bubble.classList.add('highlighted');
        setTimeout(() => bubble.classList.remove('highlighted'), 1200);
    }
}

function renderToolCall(call, index) {
    const result = (call.result && typeof call.result === 'object') ? call.result : {};
    const isSuccess = call.status === 'success' || call.status === 'OK' || result.success === true;
    const statusGlyph = isSuccess ? '✓' : '!';
    const glyphClass = isSuccess ? 'success' : 'error';

    const ms = call.execution_time_ms;
    const timeText = (ms !== undefined && ms !== null) ? `${ms}ms` : '';

    const structured = result.structured || {};
    const hasDiff = structured.current !== undefined;
    const argsText = fmtArgs(call.args);
    const output = (result.output || '').trim();
    const rawError = (call.error || result.error || '').trim();
    const { prose: cleanError } = splitToolBlocks(rawError);

    let primaryArg = '';
    if (call.args) {
        if (call.args.path) primaryArg = call.args.path;
        else if (call.args.command) primaryArg = call.args.command;
        else if (call.args.query) primaryArg = call.args.query;
    }

    let resultSection = '';
    if (hasDiff) {
        resultSection = `
            <div class="tool-section">
                <h4>${structured.previous !== undefined ? 'Diff' : 'New file'} — <span class="mono">${escapeHtml(structured.path || '')}</span></h4>
                <div class="diff-view">${renderDiff(structured.previous || '', structured.current || '')}</div>
            </div>
        `;
    } else if (output) {
        resultSection = `
            <div class="tool-section">
                <h4>Output</h4>
                <pre class="tool-output">${escapeHtml(output)}</pre>
            </div>
        `;
    }

    const errorSection = cleanError ? `
        <div class="tool-section">
            <h4>Error</h4>
            <pre class="tool-output tool-error">${escapeHtml(cleanError)}</pre>
        </div>
    ` : '';

    return `
        <div class="tool-call-item">
            <div class="tool-call-header">
                <div class="tool-call-header-left">
                    <span class="step-caret"><svg class="icon"><use href="#icon-chevron-right"/></svg></span>
                    <span class="tool-call-name mono">${escapeHtml(call.tool_name)}</span>
                    ${primaryArg ? `<span class="tool-call-arg-preview mono">${escapeHtml(primaryArg)}</span>` : ''}
                </div>
                <div class="tool-call-header-right mono">
                    ${timeText ? `<span>${timeText}</span>` : ''}
                    <span class="tool-call-status-glyph ${glyphClass}">${statusGlyph}</span>
                </div>
            </div>
            <div class="tool-call-body" hidden>
                ${argsText ? `
                <div class="tool-section">
                    <h4>Arguments</h4>
                    <pre class="tool-args">${argsText}</pre>
                </div>` : ''}
                ${resultSection}
                ${errorSection}
            </div>
        </div>
    `;
}

function renderDiff(oldText, newText) {
    const oldLines = (oldText || '').split('\n');
    const newLines = (newText || '').split('\n');
    let html = '<div class="diff-line diff-header"><span class="diff-lineno">-</span><span class="diff-lineno">+</span><span class="diff-content"></span></div>';

    const maxLines = Math.max(oldLines.length, newLines.length);
    for (let i = 0; i < maxLines; i++) {
        const oldLine = oldLines[i];
        const newLine = newLines[i];
        const oldNum = i < oldLines.length ? i + 1 : '';
        const newNum = i < newLines.length ? i + 1 : '';

        if (oldLine === newLine) {
            html += `<div class="diff-line diff-context"><span class="diff-lineno">${oldNum}</span><span class="diff-lineno">${newNum}</span><span class="diff-content"> ${escapeHtml(oldLine)}</span></div>`;
        } else if (i < oldLines.length && i >= newLines.length) {
            html += `<div class="diff-line diff-remove"><span class="diff-lineno">${oldNum}</span><span class="diff-lineno"></span><span class="diff-content">- ${escapeHtml(oldLine)}</span></div>`;
        } else if (i >= oldLines.length && i < newLines.length) {
            html += `<div class="diff-line diff-add"><span class="diff-lineno"></span><span class="diff-lineno">${newNum}</span><span class="diff-content">+ ${escapeHtml(newLine)}</span></div>`;
        } else {
            html += `<div class="diff-line diff-remove"><span class="diff-lineno">${oldNum}</span><span class="diff-lineno"></span><span class="diff-content">- ${escapeHtml(oldLine)}</span></div>`;
            html += `<div class="diff-line diff-add"><span class="diff-lineno"></span><span class="diff-lineno">${newNum}</span><span class="diff-content">+ ${escapeHtml(newLine)}</span></div>`;
        }
    }
    return html;
}

function renderSessionThread(sessionData, curRunId) {
    if (!sessionData || !Array.isArray(sessionData.runs) || sessionData.runs.length <= 1) {
        return '';
    }
    const turns = sessionData.runs.map((r, idx) => {
        const isCurrent = r.run_id === curRunId;
        const currentCls = isCurrent ? ' current' : '';
        const { label: statusLabel, cls: statusCls } = runStatus(r);
        const goalSnippet = cleanSessionTitle(r.goal || '', '').slice(0, 80);
        const dataAttr = !isCurrent ? ` data-run-id="${escapeHtml(r.run_id)}"` : '';
        const fullGoal = escapeHtml(r.goal || '');
        return `     <li class="session-turn${currentCls}"${dataAttr} title="${fullGoal}">` +
            `<span class="turn-num">#${idx + 1}</span>` +
            `<span class="badge ${statusCls}">${escapeHtml(statusLabel)}</span>` +
            `<span class="turn-goal">${escapeHtml(goalSnippet)}</span>` +
            `</li>`;
    }).join('\n');

    return `
<div class="session-thread">
  <h3>Session — ${sessionData.runs.length} turns</h3>
  <ol class="session-turns">
${turns}
  </ol>
</div>
`;
}

// -------------------------------------------------------------------
// Transport Bar & Composer
// -------------------------------------------------------------------
function updateTransportUI() {
    const running = isRunLive();
    const stopBtn = document.getElementById('transport-stop');
    const input = document.getElementById('transport-input');

    if (stopBtn) {
        stopBtn.disabled = !running;
        if (!running) {
            stopBtn.classList.remove('confirming');
            const stopText = document.getElementById('stop-btn-text');
            if (stopText) stopText.textContent = 'stop';
        }
    }

    if (input && !input.value) {
        input.placeholder = running ? 'steer this run…' : 'describe a run to start…';
    }

    updateTransportStatus();
}

function updateTransportStatus() {
    const running = isRunLive();
    const statusText = document.getElementById('transport-status-text');
    const spinner = document.getElementById('transport-spinner');

    if (spinner) {
        if (running) {
            spinner.textContent = BRAILLE_FRAMES[brailleIdx];
            spinner.style.display = 'inline-block';
        } else {
            spinner.style.display = 'none';
        }
    }

    if (!statusText) return;

    if (!currentRun) {
        statusText.textContent = 'idle';
        return;
    }

    const steps = currentRun.steps || [];
    const totalCost = steps.reduce((sum, s) => sum + (Number(s.usage && s.usage.cost_usd) || 0), 0);
    const costStr = totalCost > 0 ? `$${totalCost.toFixed(totalCost < 0.01 ? 4 : 2)}` : '$0.00';

    const start = currentRun.started_at || 0;
    const st = runStatus(currentRun);

    if (running) {
        const elapsed = start ? Math.max(0, (Date.now() / 1000) - start) : 0;
        let activeRole = 'run';
        if (steps.length > 0) activeRole = steps[steps.length - 1].role;
        statusText.textContent = `${activeRole} · ${formatUnifiedDuration(elapsed)} · ${costStr}`;
    } else {
        let duration = 0;
        if (currentRun.finished_at && start) {
            duration = Math.max(0, currentRun.finished_at - start);
        } else if (currentRun.interrupted && start) {
            const lastEv = currentEvents.length > 0 ? currentEvents[currentEvents.length - 1] : null;
            duration = (lastEv && lastEv.ts && lastEv.ts > start) ? Math.max(0, lastEv.ts - start) : 0;
        }
        statusText.textContent = `${st.label.toLowerCase()} · ${formatUnifiedDuration(duration)} · ${costStr}`;
    }
}

let isSubmitting = false;
let feedbackTimer = null;

function showTransportFeedback(message, type = 'info', durationMs = 6000) {
    const el = document.getElementById('transport-feedback');
    if (!el) return;
    el.textContent = message;
    el.className = `transport-feedback mono ${type}`;
    el.hidden = false;
    clearTimeout(feedbackTimer);
    feedbackTimer = setTimeout(() => {
        el.hidden = true;
    }, durationMs);
}

async function handleTransportSubmit() {
    if (isSubmitting) return;

    const input = document.getElementById('transport-input');
    if (!input) return;
    const text = input.value.trim();
    if (!text) return;

    const running = isRunLive();
    isSubmitting = true;

    try {
        if (running) {
            const pq = projectQuery();
            const url = `/api/runs/${encodeURIComponent(currentRunId)}/messages` + (pq ? `?${pq}` : '');
            const kind = requirementsAwaiting ? 'answer' : 'steer';
            const res = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ body: text, kind })
            });
            if (res.ok) {
                input.value = '';
                if (kind === 'answer') {
                    requirementsAwaiting = false;
                    input.placeholder = 'describe a run to start…';
                    showTransportFeedback('requirements answered', 'info');
                } else {
                    const queuePill = document.getElementById('transport-queue-pill');
                    if (queuePill) {
                        queuePill.textContent = 'steer queued — applied at the next step';
                        queuePill.hidden = false;
                    }
                }
            } else {
                const data = await res.json().catch(() => ({}));
                showTransportFeedback(data.detail || 'Failed to send steer message', 'error');
            }
        } else {
            const payload = {
                goal: text,
                session_id: currentSessionId || null,
                project: currentProject || null
            };

            const res = await fetch('/api/runs', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json().catch(() => ({}));

            if (res.ok) {
                if (data.run_id) {
                    input.value = '';
                    if (data.session_id) currentSessionId = data.session_id;
                    await loadSessions(false);
                    await selectRun(data.run_id);
                } else if (data.status === 'queued') {
                    input.value = '';
                    await loadSessions(false);
                    showTransportFeedback('queued — starts after the current run', 'info');
                } else {
                    input.value = '';
                    await loadSessions(false);
                }
            } else {
                showTransportFeedback(data.detail || 'Failed to create run', 'error');
            }
        }
    } catch (e) {
        showTransportFeedback(`Network error: ${e.message}`, 'error');
    } finally {
        isSubmitting = false;
    }
}

function handleStopClick() {
    if (!isRunLive() || !currentRunId) return;

    const stopBtn = document.getElementById('transport-stop');
    const stopText = document.getElementById('stop-btn-text');

    if (!stopBtn.classList.contains('confirming')) {
        stopBtn.classList.add('confirming');
        if (stopText) stopText.textContent = 'confirm?';
        clearTimeout(stopConfirmTimer);
        stopConfirmTimer = setTimeout(() => {
            stopBtn.classList.remove('confirming');
            if (stopText) stopText.textContent = 'stop';
        }, 3000);
    } else {
        clearTimeout(stopConfirmTimer);
        stopBtn.classList.remove('confirming');
        if (stopText) stopText.textContent = 'stopping…';

        const pq = projectQuery();
        const url = `/api/runs/${encodeURIComponent(currentRunId)}/stop` + (pq ? `?${pq}` : '');
        fetch(url, { method: 'POST' })
            .then(async (res) => {
                if (!res.ok) {
                    const data = await res.json().catch(() => ({}));
                    showTransportFeedback(data.detail || 'Failed to stop run', 'error');
                    if (stopText) stopText.textContent = 'stop';
                }
            })
            .catch(err => {
                showTransportFeedback(`Stop error: ${err.message}`, 'error');
                if (stopText) stopText.textContent = 'stop';
            });
    }
}

// -------------------------------------------------------------------
// @ Mention File Completion
// -------------------------------------------------------------------
async function loadProjectFiles() {
    try {
        const pq = projectQuery();
        const url = pq ? `/api/files?${pq}` : '/api/files';
        const res = await fetch(url);
        if (res.ok) {
            const data = await res.json();
            projectFiles = data.files || [];
        }
    } catch (e) {
        projectFiles = [];
    }
}

function setupFileMention() {
    const input = document.getElementById('transport-input');
    const popup = document.getElementById('file-mention-popup');
    if (!input || !popup) return;

    let selectedIdx = 0;
    let matchingFiles = [];

    function closePopup() {
        popup.hidden = true;
        popup.innerHTML = '';
        matchingFiles = [];
    }

    input.addEventListener('input', () => {
        const cursor = input.selectionStart;
        const val = input.value.slice(0, cursor);
        const atMatch = val.match(/@([^\s]*)$/);

        if (!atMatch) {
            closePopup();
            return;
        }

        const query = atMatch[1].toLowerCase();
        if (projectFiles.length === 0) loadProjectFiles();

        matchingFiles = projectFiles.filter(f => {
            if (!query) return true;
            return f.toLowerCase().includes(query);
        }).slice(0, 12);

        if (matchingFiles.length === 0) {
            closePopup();
            return;
        }

        selectedIdx = 0;
        renderMentionPopup();
    });

    function renderMentionPopup() {
        popup.hidden = false;
        popup.innerHTML = matchingFiles.map((file, idx) => `
            <div class="mention-item ${idx === selectedIdx ? 'selected' : ''}" data-index="${idx}">
                <svg class="icon"><use href="#icon-folder"/></svg>
                <span>${escapeHtml(file)}</span>
            </div>
        `).join('');

        popup.querySelectorAll('.mention-item').forEach(item => {
            item.addEventListener('click', () => {
                const idx = parseInt(item.dataset.index, 10);
                insertMention(matchingFiles[idx]);
            });
        });
    }

    function insertMention(filePath) {
        const cursor = input.selectionStart;
        const val = input.value;
        const before = val.slice(0, cursor);
        const after = val.slice(cursor);
        const atIdx = before.lastIndexOf('@');

        if (atIdx !== -1) {
            input.value = before.slice(0, atIdx) + '@' + filePath + ' ' + after;
            input.focus();
            const newCursor = atIdx + filePath.length + 2;
            input.setSelectionRange(newCursor, newCursor);
        }
        closePopup();
    }

    input.addEventListener('keydown', (e) => {
        if (popup.hidden || matchingFiles.length === 0) {
            if (e.key === 'Enter') {
                e.preventDefault();
                handleTransportSubmit();
            }
            return;
        }

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            selectedIdx = (selectedIdx + 1) % matchingFiles.length;
            renderMentionPopup();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            selectedIdx = (selectedIdx - 1 + matchingFiles.length) % matchingFiles.length;
            renderMentionPopup();
        } else if (e.key === 'Enter' || e.key === 'Tab') {
            e.preventDefault();
            if (matchingFiles[selectedIdx]) {
                insertMention(matchingFiles[selectedIdx]);
            }
        } else if (e.key === 'Escape') {
            e.preventDefault();
            closePopup();
        }
    });
}

// -------------------------------------------------------------------
// Command Palette (⌘K / Ctrl-K)
// -------------------------------------------------------------------
function setupCommandPalette() {
    const palette = document.getElementById('cmd-palette');
    const input = document.getElementById('cmd-palette-input');
    const results = document.getElementById('cmd-palette-results');
    const btnCmd = document.getElementById('btn-cmd-palette');

    if (!palette || !input || !results) return;

    let selectedIndex = 0;
    let filteredCommands = [];

    const baseCommands = [
        { id: 'model', name: 'model', desc: 'Open config scrolled to models section', action: () => { openOverlay('config'); scrollToConfigModels(); } },
        { id: 'config', name: 'config', desc: 'Open settings & configuration panel', action: () => openOverlay('config') },
        { id: 'tools', name: 'tools', desc: 'View available tools catalogue', action: () => openOverlay('tools') },
        { id: 'mcp', name: 'mcp', desc: 'View MCP server connection status', action: () => openOverlay('mcp') },
        { id: 'cost', name: 'cost', desc: 'Show session cost summary', action: () => showCostAlert() },
        { id: 'clear', name: 'clear', desc: 'Start a new session', action: () => startNewSession() },
        { id: 'stop', name: 'stop', desc: 'Stop currently active run', action: () => handleStopClick() }
    ];

    function openPalette() {
        palette.hidden = false;
        input.value = '';
        input.focus();
        filterPalette('');
    }

    function closePalette() {
        palette.hidden = true;
    }

    function filterPalette(q) {
        const query = (q || '').trim().toLowerCase();

        let list = [...baseCommands];
        sessionsList.forEach(s => {
            const cleanTitle = cleanSessionTitle(s.title || s.first_goal || s.session_id);
            list.push({
                id: `resume-${s.session_id}`,
                name: `resume ${s.session_id.slice(0, 16)}…`,
                desc: `Switch to session: ${cleanTitle}`,
                action: () => selectSession(s.session_id)
            });
        });

        if (!query) {
            filteredCommands = list;
        } else {
            filteredCommands = list.filter(cmd => cmd.name.toLowerCase().includes(query) || cmd.desc.toLowerCase().includes(query));
        }

        selectedIndex = 0;
        renderResults();
    }

    function renderResults() {
        if (filteredCommands.length === 0) {
            results.innerHTML = `<div style="padding: 1ch; color: var(--text-faint); font-size: 11px;">no matching commands</div>`;
            return;
        }

        results.innerHTML = filteredCommands.map((cmd, idx) => `
            <div class="cmd-palette-item ${idx === selectedIndex ? 'selected' : ''}" data-index="${idx}">
                <span class="cmd-name">${escapeHtml(cmd.name)}</span>
                <span class="cmd-desc">${escapeHtml(cmd.desc)}</span>
            </div>
        `).join('');

        results.querySelectorAll('.cmd-palette-item').forEach(item => {
            item.addEventListener('click', () => {
                const idx = parseInt(item.dataset.index, 10);
                if (filteredCommands[idx]) {
                    closePalette();
                    filteredCommands[idx].action();
                }
            });
        });
    }

    input.addEventListener('input', () => filterPalette(input.value));

    input.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            selectedIndex = (selectedIndex + 1) % filteredCommands.length;
            renderResults();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            selectedIndex = (selectedIndex - 1 + filteredCommands.length) % filteredCommands.length;
            renderResults();
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (filteredCommands[selectedIndex]) {
                closePalette();
                filteredCommands[selectedIndex].action();
            }
        } else if (e.key === 'Escape') {
            e.preventDefault();
            closePalette();
        }
    });

    if (btnCmd) btnCmd.addEventListener('click', openPalette);

    // Global Hotkeys
    window.addEventListener('keydown', (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
            e.preventDefault();
            if (palette.hidden) openPalette();
            else closePalette();
        } else if (e.key === 'Escape') {
            if (!palette.hidden) closePalette();
            else if (openPanelName) closeOverlay();
        } else if (e.key === '/' && document.activeElement !== document.getElementById('transport-input') && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
            e.preventDefault();
            const transportInput = document.getElementById('transport-input');
            if (transportInput) transportInput.focus();
        }
    });

    // Dismiss on click outside
    window.addEventListener('click', (e) => {
        if (!palette.hidden && !palette.contains(e.target) && e.target !== btnCmd) {
            closePalette();
        }
    });
}

function showCostAlert() {
    if (!currentSessionData) return;
    const cost = typeof currentSessionData.total_cost === 'number' ? currentSessionData.total_cost : 0;
    const runs = (currentSessionData.runs || []).length;
    const title = cleanSessionTitle(currentSessionData.title || currentSessionData.session_id);
    alert(`Session: ${title}\nRuns: ${runs}\nTotal Cost: $${cost.toFixed(4)}`);
}

function scrollToConfigModels() {
    const el = document.getElementById('config-review_backend');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

// -------------------------------------------------------------------
// Overlay Panels (Config / Tools / MCP)
// -------------------------------------------------------------------
function setupOverlayPanels() {
    const btnTools = document.getElementById('btn-open-tools');
    const btnMcp = document.getElementById('btn-open-mcp');
    const btnConfig = document.getElementById('btn-open-config');
    const closeBtn = document.getElementById('overlay-close');
    const recheckBtn = document.getElementById('btn-recheck-mcp');
    const addMcpBtn = document.getElementById('btn-add-mcp-server');
    const configForm = document.getElementById('config-form');
    const testEmailBtn = document.getElementById('send-test-email');

    if (btnTools) btnTools.addEventListener('click', () => toggleOverlay('tools'));
    if (btnMcp) btnMcp.addEventListener('click', () => toggleOverlay('mcp'));
    if (btnConfig) btnConfig.addEventListener('click', () => toggleOverlay('config'));
    if (closeBtn) closeBtn.addEventListener('click', closeOverlay);
    if (recheckBtn) recheckBtn.addEventListener('click', loadMcpPanel);
    if (addMcpBtn) addMcpBtn.addEventListener('click', () => addMcpServerRow());
    if (configForm) configForm.addEventListener('submit', submitConfig);

    if (testEmailBtn) {
        testEmailBtn.addEventListener('click', async () => {
            const statusSpan = document.getElementById('test-email-status');
            if (statusSpan) {
                statusSpan.textContent = 'Sending...';
                statusSpan.className = 'test-email-status mono';
            }
            try {
                const res = await fetch('/api/notifications/test', { method: 'POST' });
                const data = await res.json();
                if (statusSpan) {
                    statusSpan.textContent = data.result || 'No response';
                    statusSpan.className = data.result && data.result.startsWith('sent') ? 'test-email-status success mono' : 'test-email-status error mono';
                }
            } catch (err) {
                if (statusSpan) {
                    statusSpan.textContent = `error: ${err.message}`;
                    statusSpan.className = 'test-email-status error mono';
                }
            }
        });
    }
}

function toggleOverlay(name) {
    if (openPanelName === name) {
        closeOverlay();
    } else {
        openOverlay(name);
    }
}

function openOverlay(name) {
    openPanelName = name;
    const container = document.getElementById('overlay-panel-container');
    const titleEl = document.getElementById('overlay-title');
    const configPanel = document.getElementById('panel-config');
    const toolsPanel = document.getElementById('panel-tools');
    const mcpPanel = document.getElementById('panel-mcp');

    if (!container) return;
    container.hidden = false;

    document.getElementById('btn-open-config')?.classList.toggle('active', name === 'config');
    document.getElementById('btn-open-tools')?.classList.toggle('active', name === 'tools');
    document.getElementById('btn-open-mcp')?.classList.toggle('active', name === 'mcp');

    if (configPanel) configPanel.hidden = name !== 'config';
    if (toolsPanel) toolsPanel.hidden = name !== 'tools';
    if (mcpPanel) mcpPanel.hidden = name !== 'mcp';

    if (name === 'config') {
        if (titleEl) titleEl.textContent = 'CONFIGURATION';
        loadConfig();
        loadMemory();
    } else if (name === 'tools') {
        if (titleEl) titleEl.textContent = 'TOOLS';
        loadToolsPanel();
    } else if (name === 'mcp') {
        if (titleEl) titleEl.textContent = 'MCP SERVERS';
        loadMcpPanel();
    }
}

function closeOverlay() {
    openPanelName = null;
    const container = document.getElementById('overlay-panel-container');
    if (container) container.hidden = true;

    document.getElementById('btn-open-config')?.classList.remove('active');
    document.getElementById('btn-open-tools')?.classList.remove('active');
    document.getElementById('btn-open-mcp')?.classList.remove('active');
}

// -------------------------------------------------------------------
// Config & Memory Logic
// -------------------------------------------------------------------
async function loadConfig() {
    try {
        const res = await fetch('/api/config');
        if (!res.ok) return;
        const config = await res.json();

        if (config.review) {
            const revB = document.getElementById('config-review_backend');
            const revM = document.getElementById('config-review_model');
            if (revB) revB.value = config.review.backend;
            if (revM) revM.value = config.review.model || '';
        }
        if (config.build) {
            const bldB = document.getElementById('config-build_backend');
            const bldM = document.getElementById('config-build_model');
            if (bldB) bldB.value = config.build.backend;
            if (bldM) bldM.value = config.build.model || '';
        }
        if (config.verify) {
            const verB = document.getElementById('config-verify_backend');
            const verM = document.getElementById('config-verify_model');
            if (verB) verB.value = config.verify.backend;
            if (verM) verM.value = config.verify.model || '';
        }

        const maxI = document.getElementById('config-max_iterations');
        if (maxI && config.max_iterations !== undefined) maxI.value = config.max_iterations;

        const reqRounds = document.getElementById('config-max_requirements_rounds');
        if (reqRounds && config.max_requirements_rounds !== undefined) reqRounds.value = config.max_requirements_rounds;

        const maxCalls = document.getElementById('config-max_tool_calls');
        if (maxCalls && config.max_tool_calls !== undefined) maxCalls.value = config.max_tool_calls;

        const maxRead = document.getElementById('config-max_read_tool_calls');
        if (maxRead && config.max_read_tool_calls !== undefined) maxRead.value = config.max_read_tool_calls;

        const buildReview = document.getElementById('config-build_review');
        if (buildReview && config.build_review !== undefined) buildReview.checked = !!config.build_review;

        const perms = document.getElementById('config-permissions');
        if (perms && config.permissions) perms.value = config.permissions;

        const maxCost = document.getElementById('config-max_cost_usd');
        if (maxCost) maxCost.value = config.max_cost_usd !== null && config.max_cost_usd !== undefined ? config.max_cost_usd : '';

        const statusEl = document.getElementById('config-openrouter-status');
        if (statusEl) {
            if (config.openrouter_key && config.openrouter_key.set) {
                const masked = escapeHtml(config.openrouter_key.masked || '');
                const source = config.openrouter_key.source === 'env' ? 'environment' : 'config file';
                statusEl.innerHTML = `<span class="badge success">Set</span> <span class="mono">${masked}</span> <span class="key-source">(from ${source})</span>`;
            } else {
                statusEl.innerHTML = '<span class="badge warning">Not set</span>';
            }
        }

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
            if (config.smtp_password && config.smtp_password.set) {
                const masked = escapeHtml(config.smtp_password.masked || '');
                const source = config.smtp_password.source === 'env' ? 'environment' : 'config file';
                smtpStatusEl.innerHTML = `<span class="badge success">Set</span> <span class="mono">${masked}</span> <span class="key-source">(from ${source})</span>`;
            } else {
                smtpStatusEl.innerHTML = '<span class="badge warning">Not set</span>';
            }
        }

        mcpServersList = config.mcp_servers || [];
        renderMcpServersEditor();
    } catch (e) {
        console.error('Failed to load config:', e);
    }
}

function renderMcpServersEditor() {
    const container = document.getElementById('mcp-servers-list');
    if (!container) return;

    if (mcpServersList.length === 0) {
        container.innerHTML = `<p class="form-hint">No MCP servers configured yet.</p>`;
        return;
    }

    container.innerHTML = mcpServersList.map((srv, idx) => {
        const isUrl = !!srv.url;
        const cmdOrUrl = isUrl ? (srv.url || '') : (srv.command || '');
        const argsStr = Array.isArray(srv.args) ? srv.args.join(' ') : (srv.args || '');
        const envStr = srv.env ? Object.entries(srv.env).map(([k, v]) => `${k}=${v}`).join('\n') : '';
        const autoApproveStr = Array.isArray(srv.auto_approve) ? srv.auto_approve.join(', ') : (srv.auto_approve || '');

        return `
            <div class="mcp-server-card mono" data-index="${idx}">
                <div class="mcp-server-top">
                    <input type="text" class="mcp-name" value="${escapeHtml(srv.name || '')}" placeholder="Server name" required>
                    <label class="checkbox-label" style="font-size: 11px;">
                        <input type="checkbox" class="mcp-enabled" ${srv.enabled !== false ? 'checked' : ''}> enabled
                    </label>
                    <button type="button" class="btn-danger btn-remove-mcp" data-index="${idx}">remove</button>
                </div>
                <div class="mcp-type-toggle">
                    <label><input type="radio" name="mcp_type_${idx}" value="command" ${!isUrl ? 'checked' : ''}> stdio command</label>
                    <label><input type="radio" name="mcp_type_${idx}" value="url" ${isUrl ? 'checked' : ''}> SSE URL</label>
                </div>
                <div class="form-group">
                    <input type="text" class="mcp-cmd-url" value="${escapeHtml(cmdOrUrl)}" placeholder="${isUrl ? 'https://mcp.example.com/sse' : 'Executable (e.g. npx, python, /path/to/binary)'}">
                </div>
                <div class="form-group">
                    <label style="font-size: 11px;">Arguments</label>
                    <input type="text" class="mcp-args" value="${escapeHtml(argsStr)}" placeholder="Space separated arguments">
                </div>
                <div class="form-group">
                    <label style="font-size: 11px;">Environment (KEY=VALUE per line)</label>
                    <textarea class="mcp-env" rows="2" placeholder="API_KEY=xyz&#10;DEBUG=1">${escapeHtml(envStr)}</textarea>
                </div>
                <div class="form-group">
                    <label style="font-size: 11px;">Auto-approve Tools</label>
                    <input type="text" class="mcp-auto-approve" value="${escapeHtml(autoApproveStr)}" placeholder="Comma-separated tool names or 'all'">
                </div>
            </div>
        `;
    }).join('');

    container.querySelectorAll('.btn-remove-mcp').forEach(btn => {
        btn.addEventListener('click', () => {
            const idx = parseInt(btn.dataset.index, 10);
            mcpServersList.splice(idx, 1);
            renderMcpServersEditor();
        });
    });
}

function addMcpServerRow() {
    mcpServersList.push({
        name: `server-${mcpServersList.length + 1}`,
        command: '',
        args: [],
        env: {},
        enabled: true,
        auto_approve: []
    });
    renderMcpServersEditor();
}

function collectMcpServersFromDOM() {
    const cards = document.querySelectorAll('.mcp-server-card');
    const servers = [];

    cards.forEach((card, idx) => {
        const name = (card.querySelector('.mcp-name')?.value || '').trim();
        const enabled = card.querySelector('.mcp-enabled')?.checked ?? true;
        const typeRadio = card.querySelector(`input[name="mcp_type_${idx}"]:checked`);
        const isUrl = typeRadio && typeRadio.value === 'url';
        const cmdUrl = (card.querySelector('.mcp-cmd-url')?.value || '').trim();
        const argsRaw = (card.querySelector('.mcp-args')?.value || '').trim();
        const autoApproveRaw = (card.querySelector('.mcp-auto-approve')?.value || '').trim();
        const envRaw = (card.querySelector('.mcp-env')?.value || '').trim();

        if (!name) return;

        const envDict = {};
        if (envRaw) {
            envRaw.split('\n').forEach(line => {
                const trimmed = line.trim();
                if (!trimmed || trimmed.startsWith('#')) return;
                const eqIdx = trimmed.indexOf('=');
                if (eqIdx !== -1) {
                    const k = trimmed.slice(0, eqIdx).trim();
                    const v = trimmed.slice(eqIdx + 1).trim();
                    if (k) envDict[k] = v;
                }
            });
        }

        const serverObj = {
            name,
            enabled,
            env: envDict
        };

        if (isUrl) {
            serverObj.url = cmdUrl;
        } else {
            serverObj.command = cmdUrl;
            serverObj.args = argsRaw ? argsRaw.split(/\s+/).filter(Boolean) : [];
        }

        if (autoApproveRaw.toLowerCase() === 'all') {
            serverObj.auto_approve = ['all'];
        } else if (autoApproveRaw) {
            serverObj.auto_approve = autoApproveRaw.split(',').map(s => s.trim()).filter(Boolean);
        } else {
            serverObj.auto_approve = [];
        }

        servers.push(serverObj);
    });

    return servers;
}

async function submitConfig(e) {
    if (e && e.preventDefault) e.preventDefault();
    const form = document.getElementById('config-form');
    if (!form) return;

    const keyVal = form.openrouter_api_key ? form.openrouter_api_key.value : '';
    const smtpPwVal = (form.smtp_password && form.smtp_password.value) || '';

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

    const maxCostRaw = form.max_cost_usd ? form.max_cost_usd.value.trim() : '';
    const maxCostVal = maxCostRaw ? parseFloat(maxCostRaw) : null;

    const payload = {
        review_backend: form.review_backend ? form.review_backend.value : '',
        review_model: (form.review_model && form.review_model.value) || null,
        build_backend: form.build_backend ? form.build_backend.value : '',
        build_model: (form.build_model && form.build_model.value) || null,
        verify_backend: form.verify_backend ? form.verify_backend.value : '',
        verify_model: (form.verify_model && form.verify_model.value) || null,
        max_iterations: form.max_iterations ? parseInt(form.max_iterations.value, 10) : 3,
        max_requirements_rounds: form.max_requirements_rounds ? parseInt(form.max_requirements_rounds.value, 10) : 3,
        max_tool_calls: form.max_tool_calls ? parseInt(form.max_tool_calls.value, 10) : 10,
        max_read_tool_calls: form.max_read_tool_calls ? parseInt(form.max_read_tool_calls.value, 10) : 40,
        build_review: form.build_review ? form.build_review.checked : true,
        permissions: (form.permissions && form.permissions.value) || 'auto',
        max_cost_usd: maxCostVal,
        openrouter_api_key: keyVal.trim() || null,
        notifications: notifications,
        smtp_password: smtpPwVal.trim() || null,
        mcp_servers: collectMcpServersFromDOM()
    };

    const globalEl = document.getElementById('config-memory-global');
    const projectEl = document.getElementById('config-memory-project');
    const memoryPayload = {
        global: globalEl ? globalEl.value : '',
        project: projectEl ? projectEl.value : ''
    };

    const statusEl = document.getElementById('config-status');
    if (statusEl) {
        statusEl.textContent = 'Saving…';
        statusEl.className = 'status-message mono';
    }

    try {
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) {
            if (statusEl) {
                statusEl.textContent = `Error: ${data.detail || 'Failed'}`;
                statusEl.className = 'status-message error mono';
            }
            return;
        }

        const pq = projectQuery();
        const memUrl = pq ? `/api/memory?${pq}` : '/api/memory';
        await fetch(memUrl, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(memoryPayload)
        });

        if (statusEl) {
            statusEl.textContent = 'Saved successfully.';
            statusEl.className = 'status-message success mono';
        }

        if (form.openrouter_api_key) form.openrouter_api_key.value = '';
        if (form.smtp_password) form.smtp_password.value = '';

        loadConfig();
        loadMemory();
    } catch (error) {
        if (statusEl) {
            statusEl.textContent = `Network error: ${error.message}`;
            statusEl.className = 'status-message error mono';
        }
    }
}

async function loadMemory() {
    try {
        const pq = projectQuery();
        const url = pq ? `/api/memory?${pq}` : '/api/memory';
        const res = await fetch(url);
        if (!res.ok) return;
        const data = await res.json();
        const globalEl = document.getElementById('config-memory-global');
        if (globalEl) globalEl.value = data.global || '';
        const projectEl = document.getElementById('config-memory-project');
        if (projectEl) projectEl.value = data.project || '';
    } catch (e) {}
}

async function populateModelOptions() {
    try {
        const res = await fetch('/api/models');
        if (!res.ok) return;
        const data = await res.json();

        const datalistIds = ['review-model-datalist', 'build-model-datalist', 'verify-model-datalist'];
        datalistIds.forEach((id, index) => {
            let datalist = document.getElementById(id);
            if (!datalist) {
                datalist = document.createElement('datalist');
                datalist.id = id;
                document.body.appendChild(datalist);
            }
            datalist.innerHTML = '';
            const backends = ['openrouter', 'claude-code', 'antigravity'];
            const backend = backends[index];
            const models = data[backend] || [];
            models.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m.id;
                opt.textContent = m.name;
                datalist.appendChild(opt);
            });
        });

        document.getElementById('config-review_model')?.setAttribute('list', 'review-model-datalist');
        document.getElementById('config-build_model')?.setAttribute('list', 'build-model-datalist');
        document.getElementById('config-verify_model')?.setAttribute('list', 'verify-model-datalist');
    } catch (e) {}
}

// -------------------------------------------------------------------
// Tools & MCP Panels Logic
// -------------------------------------------------------------------
async function loadToolsPanel() {
    const listEl = document.getElementById('tools-list');
    if (!listEl) return;
    listEl.innerHTML = '<p class="form-hint">Loading tools…</p>';

    try {
        const res = await fetch('/api/tools');
        if (!res.ok) throw new Error('Failed to load tools');
        const data = await res.json();
        const tools = data.tools || [];

        if (tools.length === 0) {
            listEl.innerHTML = '<p class="form-hint">No tools exposed.</p>';
            return;
        }

        listEl.innerHTML = `
            <table class="mono-table mono">
                <thead>
                    <tr>
                        <th>Tool</th>
                        <th>Access</th>
                        <th>Description</th>
                    </tr>
                </thead>
                <tbody>
                    ${tools.map((t, idx) => `
                        <tr class="tool-row" data-index="${idx}" style="cursor: pointer;">
                            <td style="font-weight: 500; color: var(--text-primary);">${escapeHtml(t.name)}</td>
                            <td><span class="badge ${t.read_only ? 'info' : 'warning'}">${t.read_only ? 'RO' : 'RW'}</span></td>
                            <td style="color: var(--text-secondary);">${escapeHtml(t.description || '')}</td>
                        </tr>
                        <tr id="tool-schema-${idx}" hidden>
                            <td colspan="3">
                                <pre class="tool-schema-pre">${escapeHtml(JSON.stringify(t.schema, null, 2))}</pre>
                            </td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        `;

        listEl.querySelectorAll('.tool-row').forEach(row => {
            row.addEventListener('click', () => {
                const idx = row.dataset.index;
                const schemaRow = document.getElementById(`tool-schema-${idx}`);
                if (schemaRow) schemaRow.hidden = !schemaRow.hidden;
            });
        });
    } catch (e) {
        listEl.innerHTML = `<p class="form-hint" style="color: var(--status-red);">Error: ${e.message}</p>`;
    }
}

async function loadMcpPanel() {
    const listEl = document.getElementById('mcp-list');
    if (!listEl) return;
    listEl.innerHTML = '<p class="form-hint">Querying MCP servers…</p>';

    try {
        const pq = projectQuery();
        const url = pq ? `/api/mcp?${pq}` : '/api/mcp';
        const res = await fetch(url);
        if (!res.ok) throw new Error('Failed to load MCP status');
        const data = await res.json();
        const servers = data.servers || [];

        if (servers.length === 0) {
            listEl.innerHTML = '<p class="form-hint">No MCP servers configured. Add one in the Config panel.</p>';
            return;
        }

        listEl.innerHTML = servers.map((s, idx) => {
            let glyph = '·';
            let glyphClass = 'faint';
            let label = 'disabled';

            if (s.enabled) {
                if (s.connected) {
                    glyph = '✓';
                    glyphClass = 'success';
                    label = 'connected';
                } else {
                    glyph = '!';
                    glyphClass = 'error';
                    label = 'error';
                }
            }

            const toolsCount = (s.tools && s.tools.length) || 0;

            return `
                <div class="mcp-server-view-item mono">
                    <div class="mcp-server-view-header">
                        <span class="mcp-server-view-name">${escapeHtml(s.name)}</span>
                        <div class="mcp-server-view-status">
                            <span class="tool-call-status-glyph ${glyphClass}">${glyph}</span>
                            <span>${label}</span>
                            <span>(${toolsCount} tool${toolsCount === 1 ? '' : 's'})</span>
                        </div>
                    </div>
                    ${s.error ? `<div class="tool-output tool-error">${escapeHtml(s.error)}</div>` : ''}
                    ${toolsCount > 0 ? `
                        <div class="mcp-tools-chip-list">
                            ${s.tools.map(t => `<span class="mcp-tool-chip mono">${escapeHtml(t.name)}</span>`).join('')}
                        </div>
                    ` : ''}
                </div>
            `;
        }).join('');
    } catch (e) {
        listEl.innerHTML = `<p class="form-hint" style="color: var(--status-red);">Error: ${e.message}</p>`;
    }
}

// -------------------------------------------------------------------
// String Formatting & Parsing Helpers
// -------------------------------------------------------------------
function fmtCost(usage) {
    if (!usage) return '';
    const c = usage.cost_usd;
    if (c === null || c === undefined) return '';
    const n = Number(c);
    if (Number.isNaN(n)) return '';
    return '$' + n.toFixed(n > 0 && n < 0.01 ? 6 : 4);
}

function formatUnifiedDuration(sec) {
    const s = Math.max(0, Math.round(sec || 0));
    if (s < 3600) {
        const m = Math.floor(s / 60);
        const remS = s % 60;
        return `${String(m).padStart(2, '0')}:${String(remS).padStart(2, '0')}`;
    }
    const h = Math.floor(s / 3600);
    const remM = Math.floor((s % 3600) / 60);
    return `${h}h ${String(remM).padStart(2, '0')}m`;
}

function formatSessionDate(ts) {
    if (ts === null || ts === undefined || typeof ts !== 'number' || isNaN(ts) || ts <= 0) {
        return null;
    }
    const d = new Date(ts * 1000);
    if (isNaN(d.getTime())) return null;

    const now = new Date();
    const isToday = d.getFullYear() === now.getFullYear() &&
                    d.getMonth() === now.getMonth() &&
                    d.getDate() === now.getDate();
    const isSameYear = d.getFullYear() === now.getFullYear();

    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const month = MONTHS[d.getMonth()];
    const day = d.getDate();
    const year = d.getFullYear();
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');

    let text = '';
    if (isToday) {
        text = `${hh}:${mm}`;
    } else if (isSameYear) {
        text = `${month} ${day}`;
    } else {
        text = `${month} ${day} ${year}`;
    }
    const fullTitle = d.toLocaleString();
    return { text, fullTitle };
}

function verifyVerdict(text) {
    const m = String(text || '').match(/VERIFY_RESULT:\s*(PASS|FAIL)/i);
    return m ? m[1].toUpperCase() : null;
}

function blockerLabel(reason) {
    if (reason === 'budget') return 'Budget limit reached';
    if (reason === 'permission') return 'Permission denied';
    if (reason === 'backend_error') return 'Backend error';
    return reason || 'Blocker';
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
    return `<div class="tool-req"><span class="tool-req-name">[requested: ${name}]</span>${argsHtml}</div>`;
}

function extractBalancedJson(text) {
    const results = [];
    let inString = false;
    let escape = false;
    let depth = 0;
    let start = -1;

    for (let i = 0; i < text.length; i++) {
        const char = text[i];
        if (inString) {
            if (escape) {
                escape = false;
            } else if (char === '\\') {
                escape = true;
            } else if (char === '"') {
                inString = false;
            }
        } else {
            if (char === '"') {
                inString = true;
            } else if (char === '{') {
                if (depth === 0) start = i;
                depth++;
            } else if (char === '}') {
                if (depth > 0) {
                    depth--;
                    if (depth === 0 && start !== -1) {
                        results.push(text.slice(start, i + 1));
                        start = -1;
                    }
                }
            }
        }
    }
    return results;
}

function splitToolBlocks(text) {
    if (!text) return { prose: '', requests: [] };

    let str = String(text);
    const requests = [];
    const nameRe = /^[A-Za-z_][\w-]*$/;

    function parseSingleUnit(unit) {
        if (!unit) return;
        let name = '';
        let args = {};

        const jsonCandidates = extractBalancedJson(unit);

        for (const raw of jsonCandidates) {
            try {
                const parsed = JSON.parse(raw);
                if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
                    if (typeof parsed.name === 'string' && nameRe.test(parsed.name)) {
                        name = parsed.name;
                        if (parsed.args && typeof parsed.args === 'object' && !Array.isArray(parsed.args)) {
                            args = parsed.args;
                        }
                        requests.push({ name, args });
                        return;
                    }
                }
            } catch (e) {}
        }

        const m2 = unit.match(/invoke\s+name\s*=\s*["']([A-Za-z_][\w-]*)["']/i);
        if (m2 && nameRe.test(m2[1])) {
            name = m2[1];
        }

        if (!name) {
            const m3 = unit.match(/<[^>]*?invoke[^>]*?>\s*([A-Za-z_][\w-]*)\s*</i);
            if (m3 && nameRe.test(m3[1])) {
                name = m3[1];
            }
        }

        if (!name) {
            const m4 = unit.match(/function\s*[｜|]\s*sep\s*[｜|]\s*([A-Za-z_][\w-]*)/i);
            if (m4 && nameRe.test(m4[1])) {
                name = m4[1];
            }
        }

        if (!name) return;

        for (const raw of jsonCandidates) {
            try {
                const parsed = JSON.parse(raw);
                if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
                    args = parsed;
                    break;
                }
            } catch (e) {}
        }

        requests.push({ name, args });
    }

    function parseBlock(block) {
        if (!block) return;
        const invokeNameMatches = [...block.matchAll(/<[^>]*?invoke\b[^>]*?name\s*=\s*["']([A-Za-z_][\w-]*)["'][^>]*>/gi)];
        if (invokeNameMatches.length > 0) {
            for (let i = 0; i < invokeNameMatches.length; i++) {
                const startIdx = invokeNameMatches[i].index;
                const endIdx = (i + 1 < invokeNameMatches.length) ? invokeNameMatches[i + 1].index : block.length;
                parseSingleUnit(block.slice(startIdx, endIdx));
            }
            return;
        }

        const bareInvokeMatches = [...block.matchAll(/<[^>]*?invoke[^>]*?>\s*([A-Za-z_][\w-]*)\s*</gi)];
        if (bareInvokeMatches.length > 1) {
            for (let i = 0; i < bareInvokeMatches.length; i++) {
                const startIdx = bareInvokeMatches[i].index;
                const endIdx = (i + 1 < bareInvokeMatches.length) ? bareInvokeMatches[i + 1].index : block.length;
                parseSingleUnit(block.slice(startIdx, endIdx));
            }
            return;
        }

        parseSingleUnit(block);
    }

    const closedToolCallRe = /<[^>]*?tool_calls?\b[^>]*>([\s\S]*?)<\s*\/[^>]*?tool_calls?\b[^>]*>/gi;
    str = str.replace(closedToolCallRe, (_, inner) => {
        parseBlock(inner);
        return '';
    });

    const unclosedToolCallRe = /<[^>]*?tool_calls?\b[^>]*>([\s\S]*)$/i;
    str = str.replace(unclosedToolCallRe, (_, inner) => {
        parseBlock(inner);
        return '';
    });

    const closedInvokeRe = /<[^>]*?invoke\b[^>]*>([\s\S]*?)<\s*\/[^>]*?invoke\b[^>]*>/gi;
    str = str.replace(closedInvokeRe, (_, inner) => {
        parseBlock(inner);
        return '';
    });

    const lines = str.split('\n');
    const keptLines = [];
    for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
            try {
                const parsed = JSON.parse(trimmed);
                if (parsed && typeof parsed === 'object' && !Array.isArray(parsed) && parsed.name && typeof parsed.name === 'string' && nameRe.test(parsed.name)) {
                    const args = (parsed.args && typeof parsed.args === 'object' && !Array.isArray(parsed.args))
                        ? parsed.args
                        : {};
                    requests.push({ name: parsed.name, args });
                    continue;
                }
            } catch (e) {}
        }
        const stripped = line.replace(/<[^>]*?(?:DSML|tool_calls?|invoke|parameter)[^>]*>/gi, '').replace(/<\/[^>]*?(?:DSML|tool_calls?|invoke|parameter)[^>]*>/gi, '');
        if (stripped.trim() || !line.trim()) {
            keptLines.push(stripped);
        }
    }

    const prose = keptLines.join('\n').trim();
    return { prose, requests };
}

function runStatus(run) {
    if (!run) return { label: 'Idle', cls: 'info' };
    if (run.interrupted || (run.finished_at === null && run.is_active === false)) {
        return { label: 'Interrupted', cls: 'danger' };
    }
    if (run.pushed && run.pushed.pushed) return { label: 'Pushed', cls: 'success' };
    if (run.pushed && run.pushed.pushed === false) return { label: 'Push failed', cls: 'danger' };
    if (run.stopped) return { label: 'Stopped', cls: 'warning' };
    if (run.error) return { label: 'Error', cls: 'danger' };
    if (Array.isArray(run.blockers) && run.blockers.some(b => b.fatal)) return { label: 'Blocked', cls: 'danger' };
    if (run.finished_at) {
        const steps = run.steps || [];
        const verifySteps = steps.filter(s => s.role === 'verify');
        if (verifySteps.length > 0) {
            const lastV = verifySteps[verifySteps.length - 1];
            const verdict = verifyVerdict(lastV.text);
            if (verdict === 'FAIL' || lastV.success === false) {
                return { label: 'Failed', cls: 'danger' };
            }
        }
        return { label: 'Completed', cls: 'info' };
    }
    if (run.finished_at === null && (run.is_active || run.is_active === undefined)) {
        return { label: 'Running', cls: 'warning' };
    }
    return { label: 'Idle', cls: 'info' };
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

// -------------------------------------------------------------------
// Router & Compatibility Helpers
// -------------------------------------------------------------------
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
    if (route.view === 'config') {
        openOverlay('config');
    } else if (route.view === 'run-detail' && route.runId) {
        selectRun(route.runId);
    }
}

function showTab(tabName) {
    if (tabName === 'config') openOverlay('config');
    else if (tabName === 'health') openOverlay('mcp');
    else closeOverlay();
}

function loadRuns(page = 1) {
    loadSessions(false);
}

function setupComposer(run) {
    updateTransportUI();
}

// -------------------------------------------------------------------
// App Initialization
// -------------------------------------------------------------------
if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', async () => {
        const notifyBtn = document.getElementById('notify-toggle');
        if (notifyBtn) notifyBtn.addEventListener('click', toggleNotify);
        updateNotifyIcon();

        const projSelect = document.getElementById('project-select');
        if (projSelect) {
            projSelect.addEventListener('change', (e) => {
                currentProject = e.target.value;
                try {
                    localStorage.setItem(PROJECT_KEY, currentProject);
                } catch (err) {}
                loadSessions(true);
                loadMemory();
                loadProjectFiles();
            });
        }

        const btnNewSession = document.getElementById('btn-new-session');
        if (btnNewSession) btnNewSession.addEventListener('click', startNewSession);

        const stopBtn = document.getElementById('transport-stop');
        if (stopBtn) stopBtn.addEventListener('click', handleStopClick);

        const sendBtn = document.getElementById('transport-send');
        if (sendBtn) sendBtn.addEventListener('click', handleTransportSubmit);

        const mobileToggle = document.getElementById('mobile-rail-toggle');
        if (mobileToggle) {
            mobileToggle.addEventListener('click', () => {
                const rail = document.getElementById('session-rail');
                setSessionRailOpen(!(rail && rail.classList.contains('mobile-open')));
            });
        }

        const railClose = document.getElementById('rail-close');
        if (railClose) railClose.addEventListener('click', () => setSessionRailOpen(false));

        const railBackdrop = document.getElementById('rail-backdrop');
        if (railBackdrop) railBackdrop.addEventListener('click', () => setSessionRailOpen(false));

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') setSessionRailOpen(false);
        });

        const threadArea = document.getElementById('thread-area');
        if (threadArea) {
            threadArea.addEventListener('scroll', () => {
                const nearBottom = isThreadNearBottom();
                if (!nearBottom && isFollowing) {
                    isFollowing = false;
                    userPausedScroll = true;
                    updateFollowIndicator();
                } else if (nearBottom && !isFollowing && userPausedScroll) {
                    isFollowing = true;
                    userPausedScroll = false;
                    updateFollowIndicator();
                }
            }, { passive: true });
        }

        const followIndicator = document.getElementById('follow-indicator');
        if (followIndicator) {
            followIndicator.addEventListener('click', () => {
                userPausedScroll = false;
                isFollowing = true;
                maybeScrollThreadToBottom();
            });
        }

        setupPlayheadControls();
        setupFileMention();
        setupCommandPalette();
        setupOverlayPanels();
        populateModelOptions();

        await loadProjects();
        await loadSessions(true);
        loadProjectFiles();

        window.addEventListener('popstate', renderRoute);
        renderRoute();
    });
}

// -------------------------------------------------------------------
// Module Exports (Node environment compatibility for test suites)
// -------------------------------------------------------------------
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        splitToolBlocks,
        formatToolReqArgs,
        renderToolReq,
        renderStep,
        renderAssistantBubble,
        renderLiveAssistantBubble,
        renderUserBubble,
        renderChangesFooter,
        summarizeStepChanges,
        renderRunHeader,
        getRoleModels,
        renderSessionThread,
        setupComposer,
        escapeHtml,
        currentRoute,
        navigate,
        renderRoute,
        showTab,
        loadRuns,
        runStatus,
        formatUnifiedDuration,
        formatSessionDate,
        showTransportFeedback,
        collectMcpServersFromDOM,
        handleTransportSubmit,
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
