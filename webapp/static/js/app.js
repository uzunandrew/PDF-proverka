/**
 * Audit Manager — SPA на Vue 3.
 * Маршрутизация, состояние, API-вызовы, live-статус.
 */
const { createApp, ref, computed, watch, onMounted, onUnmounted, nextTick } = Vue;

const app = createApp({
    setup() {
        // ─── State ───
        const currentView = ref('dashboard');
        const currentProjectId = ref(null);
        const currentProject = ref(null);
        const projects = ref([]);
        const loading = ref(false);

        // Findings
        const findingsData = ref(null);
        const filterSeverity = ref('');
        const filterSearch = ref('');
        const severityOptions = [
            'КРИТИЧЕСКОЕ', 'ЭКОНОМИЧЕСКОЕ', 'ЭКСПЛУАТАЦИОННОЕ',
            'РЕКОМЕНДАТЕЛЬНОЕ', 'ПРОВЕРИТЬ ПО СМЕЖНЫМ'
        ];

        // Tiles
        const tilesProjectId = ref('');
        const tilePages = ref([]);
        const selectedPage = ref(null);
        const selectedTile = ref(null);

        // Log
        const logProjectId = ref('');
        const logEntries = ref([]);
        const logAutoScroll = ref(true);
        const logContainer = ref(null);

        // WebSocket
        const wsConnected = ref(false);

        // ─── Live Status (polling) ───
        const liveStatus = ref({ running: {}, batches: {} });
        const elapsedTick = ref(0); // реактивный тик для обновления таймера
        let pollTimer = null;
        let tickTimer = null;

        // ─── Heartbeat ───
        const heartbeatData = ref({});       // {projectId: {stage, elapsed_sec, process_alive, eta_sec, ...}}
        const lastHeartbeatTime = ref({});   // {projectId: timestamp_ms последнего heartbeat}

        async function pollLiveStatus() {
            try {
                const resp = await fetch('/api/audit/live-status');
                if (resp.ok) {
                    const data = await resp.json();
                    liveStatus.value = data;

                    // Обновляем auditRunning на основе live-данных
                    const hasAny = Object.keys(data.running).length > 0;
                    auditRunning.value = hasAny;

                    // Backup heartbeat из polling (если WS не работает)
                    for (const [pid, info] of Object.entries(data.running || {})) {
                        if (info.last_heartbeat) {
                            const hbTime = new Date(info.last_heartbeat).getTime();
                            const current = lastHeartbeatTime.value[pid] || 0;
                            if (hbTime > current) {
                                lastHeartbeatTime.value = { ...lastHeartbeatTime.value, [pid]: hbTime };
                            }
                        }
                        if (info.eta_sec != null) {
                            heartbeatData.value = {
                                ...heartbeatData.value,
                                [pid]: { ...heartbeatData.value[pid], eta_sec: info.eta_sec },
                            };
                        }
                    }

                    // Очистка heartbeat для остановленных проектов
                    for (const pid of Object.keys(heartbeatData.value)) {
                        if (!data.running[pid]) {
                            const { [pid]: _, ...rest } = heartbeatData.value;
                            heartbeatData.value = rest;
                            const { [pid]: __, ...restTime } = lastHeartbeatTime.value;
                            lastHeartbeatTime.value = restTime;
                        }
                    }

                    // Обновляем batches в списке проектов (Dashboard)
                    if (currentView.value === 'dashboard' && projects.value.length > 0) {
                        for (const p of projects.value) {
                            if (data.batches[p.project_id]) {
                                p.completed_batches = data.batches[p.project_id].completed;
                                p.total_batches = data.batches[p.project_id].total;
                            }
                        }
                    }

                    // Обновляем текущий проект (Project Detail)
                    if (currentView.value === 'project' && currentProject.value) {
                        const pid = currentProject.value.project_id;
                        if (data.batches[pid]) {
                            currentProject.value.completed_batches = data.batches[pid].completed;
                            currentProject.value.total_batches = data.batches[pid].total;
                        }
                    }
                }
            } catch (e) {
                // Ignore polling errors
            }
        }

        function startPolling() {
            stopPolling();
            pollLiveStatus(); // сразу
            pollTimer = setInterval(pollLiveStatus, 5000);
            tickTimer = setInterval(() => { elapsedTick.value++; }, 1000);
        }

        function stopPolling() {
            if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
            if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
        }

        // ─── Helpers для live-статуса ───
        function isProjectRunning(projectId) {
            return !!(liveStatus.value.running && liveStatus.value.running[projectId]);
        }

        function getProjectLiveInfo(projectId) {
            const r = liveStatus.value.running ? liveStatus.value.running[projectId] : null;
            const b = liveStatus.value.batches ? liveStatus.value.batches[projectId] : null;
            if (!r && !b) return null;

            const info = { running: !!r };
            if (r) {
                info.stage = r.stage;
                info.status = r.status;
                info.progress_current = r.progress_current;
                info.progress_total = r.progress_total;
                info.started_at = r.started_at;
            }
            if (b) {
                info.batch_completed = b.completed;
                info.batch_total = b.total;
            }
            return info;
        }

        function stageLabel(stage) {
            const labels = {
                'prepare': 'Подготовка',
                'tile_batches': 'Генерация пакетов',
                'tile_audit': 'Анализ тайлов',
                'main_audit': 'Основной аудит',
                'merge': 'Слияние результатов',
                'norm_verify': 'Верификация норм',
                'norm_fix': 'Пересмотр замечаний',
                'full': 'Полный конвейер',
                'excel': 'Excel-отчёт',
            };
            return labels[stage] || stage || '';
        }

        function formatElapsed(startedAt) {
            if (!startedAt) return '';
            // elapsedTick обеспечивает реактивное обновление каждую секунду
            const _tick = elapsedTick.value;
            const start = new Date(startedAt);
            const now = new Date();
            const diff = Math.floor((now - start) / 1000);
            if (diff < 0) return '';
            const h = Math.floor(diff / 3600);
            const m = Math.floor((diff % 3600) / 60);
            const s = diff % 60;
            if (h > 0) {
                return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
            }
            return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
        }

        function batchPercent(projectId) {
            const b = liveStatus.value.batches ? liveStatus.value.batches[projectId] : null;
            if (!b || !b.total) return 0;
            return Math.round(b.completed / b.total * 100);
        }

        function batchProgressText(projectId) {
            const r = liveStatus.value.running ? liveStatus.value.running[projectId] : null;
            const b = liveStatus.value.batches ? liveStatus.value.batches[projectId] : null;

            if (r) {
                const pct = r.progress_total > 0
                    ? Math.round(r.progress_current / r.progress_total * 100)
                    : 0;
                if (r.stage === 'tile_audit' && b) {
                    return `${stageLabel(r.stage)}: пакет ${b.completed}/${b.total} (${Math.round(b.completed / b.total * 100)}%)`;
                }
                if (r.progress_total > 0) {
                    return `${stageLabel(r.stage)}: ${r.progress_current}/${r.progress_total} (${pct}%)`;
                }
                return `${stageLabel(r.stage)}...`;
            }
            return '';
        }

        // ─── Heartbeat helpers ───
        function secondsSinceHeartbeat(projectId) {
            const _tick = elapsedTick.value; // реактивность
            const lastTime = lastHeartbeatTime.value[projectId];
            if (!lastTime) return 999;
            return Math.floor((Date.now() - lastTime) / 1000);
        }

        function isHeartbeatStale(projectId) {
            return secondsSinceHeartbeat(projectId) > 60;
        }

        function getHeartbeatInfo(projectId) {
            return heartbeatData.value[projectId] || null;
        }

        function formatETA(etaSec) {
            if (etaSec == null || etaSec <= 0) return '';
            if (etaSec > 3600) {
                const h = Math.floor(etaSec / 3600);
                const m = Math.floor((etaSec % 3600) / 60);
                return `~${h}ч ${m}м`;
            }
            const m = Math.floor(etaSec / 60);
            if (m > 0) return `~${m} мин`;
            return `<1 мин`;
        }

        function heartbeatStatusText(projectId) {
            if (!isProjectRunning(projectId)) return '';
            const sec = secondsSinceHeartbeat(projectId);
            if (sec > 60) return `Claude думает... (нет вывода ${sec} сек)`;
            if (sec < 999) return `Процесс активен`;
            return '';
        }

        // ─── API helpers ───
        async function api(path) {
            const resp = await fetch(`/api${path}`);
            if (!resp.ok) throw new Error(`API error: ${resp.status}`);
            return resp.json();
        }

        // ─── Navigation ───
        function navigate(path) {
            window.location.hash = path;
        }

        function handleRoute() {
            const hash = window.location.hash.slice(1) || '/';
            selectedTile.value = null;

            if (hash === '/') {
                currentView.value = 'dashboard';
                connectGlobalWS();  // Вернуться на global WS
                refreshProjects();
            } else if (hash.match(/^\/project\/([^/]+)\/findings$/)) {
                const id = hash.match(/^\/project\/([^/]+)\/findings$/)[1];
                currentView.value = 'findings';
                connectGlobalWS();  // Не нужен project WS для findings
                loadFindings(id);
            } else if (hash.match(/^\/project\/([^/]+)\/tiles$/)) {
                const id = hash.match(/^\/project\/([^/]+)\/tiles$/)[1];
                currentView.value = 'tiles';
                connectGlobalWS();  // Не нужен project WS для tiles
                loadTiles(id);
            } else if (hash.match(/^\/project\/([^/]+)\/log$/)) {
                const id = hash.match(/^\/project\/([^/]+)\/log$/)[1];
                currentView.value = 'log';
                logProjectId.value = id;
                loadProject(id);
                connectProjectWS(id);  // Project WS только для лога
            } else if (hash.match(/^\/project\/([^/]+)$/)) {
                const id = hash.match(/^\/project\/([^/]+)$/)[1];
                currentView.value = 'project';
                connectGlobalWS();  // Не нужен project WS
                loadProject(id);
            }
        }

        // ─── Audit Actions ───
        const auditRunning = ref(false);

        async function apiPost(path) {
            const resp = await fetch(`/api${path}`, { method: 'POST' });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || `API error: ${resp.status}`);
            }
            return resp.json();
        }

        async function startPrepare(projectId) {
            try {
                auditRunning.value = true;
                await apiPost(`/audit/${projectId}/prepare`);
                navigate(`/project/${projectId}/log`);
            } catch (e) { alert(e.message); auditRunning.value = false; }
        }

        async function startTileAudit(projectId, startFrom = 1) {
            try {
                auditRunning.value = true;
                await apiPost(`/audit/${projectId}/tile-audit?start_from=${startFrom}`);
                navigate(`/project/${projectId}/log`);
            } catch (e) { alert(e.message); auditRunning.value = false; }
        }

        async function startMainAudit(projectId) {
            try {
                auditRunning.value = true;
                await apiPost(`/audit/${projectId}/main-audit`);
                navigate(`/project/${projectId}/log`);
            } catch (e) { alert(e.message); auditRunning.value = false; }
        }

        async function startFullAudit(projectId) {
            try {
                auditRunning.value = true;
                await apiPost(`/audit/${projectId}/full`);
                navigate(`/project/${projectId}/log`);
            } catch (e) { alert(e.message); auditRunning.value = false; }
        }

        async function startNormVerify(projectId) {
            try {
                auditRunning.value = true;
                await apiPost(`/audit/${projectId}/verify-norms`);
                navigate(`/project/${projectId}/log`);
            } catch (e) { alert(e.message); auditRunning.value = false; }
        }

        async function resumePipeline(projectId) {
            try {
                auditRunning.value = true;
                await apiPost(`/audit/${projectId}/resume`);
                navigate(`/project/${projectId}/log`);
            } catch (e) { alert(e.message); auditRunning.value = false; }
        }

        const resumeInfo = ref(null);

        async function loadResumeInfo(projectId) {
            try {
                const resp = await fetch(`/api/audit/${projectId}/resume-info`);
                if (resp.ok) {
                    resumeInfo.value = await resp.json();
                }
            } catch (e) { resumeInfo.value = null; }
        }

        async function cancelAudit(projectId) {
            try {
                await fetch(`/api/audit/${projectId}/cancel`, { method: 'DELETE' });
                auditRunning.value = false;
            } catch (e) { alert(e.message); }
        }

        // Запуск ВСЕХ проектов последовательно
        const allRunning = computed(() => {
            return liveStatus.value.running && '__ALL__' in liveStatus.value.running;
        });

        async function startAllProjects() {
            if (!confirm(`Запустить полный конвейер для всех ${projects.value.length} проектов?\n\nКаждый проект будет обработан последовательно: подготовка → тайлы → аудит → верификация норм → Excel.`)) {
                return;
            }
            try {
                auditRunning.value = true;
                await apiPost('/audit/all/full');
                // Сразу обновить список проектов, чтобы отобразить актуальные tile counts
                await refreshProjects();
            } catch (e) { alert(e.message); auditRunning.value = false; }
        }

        async function generateExcel() {
            try {
                const data = await apiPost('/export/excel');
                if (data.file) {
                    window.open(`/api/export/download/${data.file}`, '_blank');
                }
            } catch (e) { alert(e.message); }
        }

        // ─── Data Loading ───
        async function refreshProjects() {
            loading.value = true;
            try {
                const data = await api('/projects');
                projects.value = data.projects;
            } catch (e) {
                console.error('Failed to load projects:', e);
            }
            loading.value = false;
        }

        async function loadProject(id) {
            currentProjectId.value = id;
            try {
                currentProject.value = await api(`/projects/${id}`);
                loadResumeInfo(id);
            } catch (e) {
                console.error('Failed to load project:', e);
                currentProject.value = null;
            }
        }

        async function loadFindings(id) {
            findingsData.value = null;
            try {
                const params = new URLSearchParams();
                if (filterSeverity.value) params.set('severity', filterSeverity.value);
                if (filterSearch.value) params.set('search', filterSearch.value);
                const qs = params.toString();
                findingsData.value = await api(`/findings/${id}${qs ? '?' + qs : ''}`);
            } catch (e) {
                console.error('Failed to load findings:', e);
            }
        }

        async function loadTiles(id) {
            tilesProjectId.value = id;
            try {
                const data = await api(`/tiles/${id}/pages`);
                tilePages.value = data.pages;
                if (data.pages.length > 0 && !selectedPage.value) {
                    selectedPage.value = data.pages[0].page_num;
                }
            } catch (e) {
                console.error('Failed to load tiles:', e);
            }
        }

        // ─── Computed ───
        const filteredFindings = computed(() => {
            if (!findingsData.value) return [];
            return findingsData.value.findings;
        });

        const currentPageTiles = computed(() => {
            if (!selectedPage.value || !tilePages.value.length) return null;
            return tilePages.value.find(p => p.page_num === selectedPage.value);
        });

        // Live-статус текущего проекта (для Project Detail)
        const currentProjectLive = computed(() => {
            if (!currentProject.value) return null;
            return getProjectLiveInfo(currentProject.value.project_id);
        });

        // ─── Helpers ───
        function stepClass(status) {
            if (status === 'done') return 'step-done';
            if (status === 'partial') return 'step-partial';
            if (status === 'running') return 'step-running';
            return '';
        }

        function sevClass(severity) {
            const s = (severity || '').toUpperCase();
            if (s.includes('КРИТИЧ')) return 'critical';
            if (s.includes('ЭКОНОМ')) return 'economic';
            if (s.includes('ЭКСПЛУАТ')) return 'operational';
            if (s.includes('РЕКОМЕНД')) return 'recommendation';
            if (s.includes('ПРОВЕР')) return 'check';
            return 'check';
        }

        function sevIcon(severity) {
            const s = (severity || '').toUpperCase();
            if (s.includes('КРИТИЧ')) return '\uD83D\uDD34';
            if (s.includes('ЭКОНОМ')) return '\uD83D\uDFE0';
            if (s.includes('ЭКСПЛУАТ')) return '\uD83D\uDFE1';
            if (s.includes('РЕКОМЕНД')) return '\uD83D\uDD35';
            return '\u26AA';
        }

        function tileImageUrl(projectId, pageNum, tileName) {
            // tileName = "page_07_r1c2.png"
            const match = tileName.match(/r(\d+)c(\d+)/);
            if (!match) return '';
            return `/api/tiles/${projectId}/image/${pageNum}/${match[1]}_${match[2]}`;
        }

        function openTile(tileName) {
            selectedTile.value = tileName;
        }

        let searchTimeout = null;
        function debounceSearch() {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                const hash = window.location.hash.slice(1);
                const match = hash.match(/^\/project\/([^/]+)\/findings$/);
                if (match) loadFindings(match[1]);
            }, 400);
        }

        function clearLog() {
            logEntries.value = [];
        }

        // ─── WebSocket ───
        // Два отдельных WS-соединения: project (лог конкретного проекта) и global (дашборд)
        let wsProject = null;       // /ws/audit/{projectId}
        let wsGlobal = null;        // /ws/global
        let wsProjectReconnects = 0;
        let wsCurrentProjectId = null;
        let wsMode = 'global';      // 'global' | 'project'

        function closeProjectWS() {
            wsCurrentProjectId = null;
            wsProjectReconnects = 0;
            if (wsProject) {
                wsProject.onclose = null;  // убрать reconnect-handler
                wsProject.close();
                wsProject = null;
            }
        }

        function closeGlobalWS() {
            if (wsGlobal) {
                wsGlobal.onclose = null;   // убрать reconnect-handler
                wsGlobal.close();
                wsGlobal = null;
            }
        }

        function connectProjectWS(projectId) {
            // Переключаемся в project-режим: закрываем global, открываем project
            wsMode = 'project';
            closeGlobalWS();
            closeProjectWS();
            wsCurrentProjectId = projectId;
            wsProjectReconnects = 0;
            const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
            wsProject = new WebSocket(`${proto}//${location.host}/ws/audit/${projectId}`);
            wsProject.onopen = () => {
                wsConnected.value = true;
                wsProjectReconnects = 0;
            };
            wsProject.onclose = () => {
                wsConnected.value = false;
                // Переподключение только если мы всё ещё в project-режиме для этого проекта
                if (wsMode === 'project' && wsCurrentProjectId === projectId && wsProjectReconnects < 5) {
                    wsProjectReconnects++;
                    const delay = Math.min(2000 * wsProjectReconnects, 10000);
                    console.log(`[WS] Project WS reconnecting in ${delay}ms (attempt ${wsProjectReconnects})`);
                    setTimeout(() => {
                        if (wsMode === 'project' && wsCurrentProjectId === projectId) {
                            connectProjectWS(projectId);
                        }
                    }, delay);
                }
            };
            wsProject.onmessage = (event) => {
                try {
                    const msg = JSON.parse(event.data);
                    handleWSMessage(msg);
                } catch (e) {
                    console.error('[WS] Project parse error:', e.message);
                }
            };
        }

        function connectGlobalWS() {
            // Переключаемся в global-режим: закрываем project, открываем global
            wsMode = 'global';
            closeProjectWS();
            if (wsGlobal && wsGlobal.readyState === WebSocket.OPEN) return;  // уже подключен
            closeGlobalWS();
            const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
            wsGlobal = new WebSocket(`${proto}//${location.host}/ws/global`);
            wsGlobal.onopen = () => { wsConnected.value = true; };
            wsGlobal.onclose = () => {
                wsConnected.value = false;
                // Переподключение только если мы в global-режиме
                if (wsMode === 'global') {
                    setTimeout(() => {
                        if (wsMode === 'global') connectGlobalWS();
                    }, 3000);
                }
            };
            wsGlobal.onmessage = (event) => {
                try {
                    const msg = JSON.parse(event.data);
                    handleWSMessage(msg);
                } catch (e) {
                    console.error('[WS] Global parse error:', e.message);
                }
            };
        }

        function handleWSMessage(msg) {
            const time = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString() : '';

            if (msg.type === 'log') {
                logEntries.value.push({
                    time: time,
                    level: msg.data.level || 'info',
                    message: msg.data.message || '',
                });
                if (logAutoScroll.value) {
                    nextTick(() => {
                        const el = logContainer.value;
                        if (el) el.scrollTop = el.scrollHeight;
                    });
                }
            } else if (msg.type === 'progress') {
                // Update current project if viewing it
                if (currentProject.value && currentProject.value.project_id === msg.project) {
                    currentProject.value.completed_batches = msg.data.current;
                    currentProject.value.total_batches = msg.data.total;
                }
            } else if (msg.type === 'heartbeat') {
                const pid = msg.project;
                heartbeatData.value = {
                    ...heartbeatData.value,
                    [pid]: msg.data,
                };
                lastHeartbeatTime.value = {
                    ...lastHeartbeatTime.value,
                    [pid]: Date.now(),
                };
            } else if (msg.type === 'complete') {
                logEntries.value.push({
                    time: time,
                    level: 'success',
                    message: `Аудит завершён. Замечаний: ${msg.data.total_findings}. Время: ${msg.data.duration_minutes} мин.`,
                });
                auditRunning.value = false;
                // Обновляем данные при завершении
                pollLiveStatus();
                if (currentView.value === 'dashboard') refreshProjects();
            } else if (msg.type === 'error') {
                logEntries.value.push({
                    time: time,
                    level: 'error',
                    message: msg.data.message || 'Неизвестная ошибка',
                });
            }
        }

        // Watch severity filter
        watch(filterSeverity, () => {
            const hash = window.location.hash.slice(1);
            const match = hash.match(/^\/project\/([^/]+)\/findings$/);
            if (match) loadFindings(match[1]);
        });

        // ─── Init ───
        onMounted(() => {
            window.addEventListener('hashchange', handleRoute);
            handleRoute();
            connectGlobalWS();
            startPolling();
        });

        onUnmounted(() => {
            stopPolling();
        });

        return {
            // State
            currentView, currentProject, projects, loading,
            findingsData, filterSeverity, filterSearch, severityOptions,
            tilesProjectId, tilePages, selectedPage, selectedTile,
            logProjectId, logEntries, logAutoScroll, logContainer,
            wsConnected,
            // Live status
            liveStatus,
            isProjectRunning, getProjectLiveInfo,
            stageLabel, formatElapsed, batchPercent, batchProgressText,
            currentProjectLive,
            // Heartbeat
            heartbeatData, lastHeartbeatTime,
            secondsSinceHeartbeat, isHeartbeatStale, getHeartbeatInfo,
            formatETA, heartbeatStatusText,
            // Methods
            navigate, refreshProjects, stepClass, sevClass, sevIcon,
            tileImageUrl, openTile, debounceSearch, clearLog,
            // Audit actions
            auditRunning, allRunning,
            startPrepare, startTileAudit, startMainAudit, startFullAudit,
            startNormVerify, cancelAudit, generateExcel, startAllProjects,
            resumePipeline, resumeInfo,
            // Computed
            filteredFindings, currentPageTiles,
        };
    }
});

app.mount('#app');
