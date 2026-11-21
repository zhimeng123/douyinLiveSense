const $ = (id) => document.getElementById(id);
const labels = {
  stopped: "已停止", checking: "检查直播中", waiting: "等待开播",
  recording: "正在采集", reconnecting: "正在重新连接", needs_attention: "需要处理",
};
const analysisLabels = {
  not_requested: "未请求", pending: "等待分析", running: "正在分析", processing: "正在分析",
  waiting_context: "等待相邻音频上下文", waiting_audio: "已追上，等待新音频",
  complete: "已完成", failed: "分析失败", needs_setup: "需要准备模型", needs_attention: "需要处理",
};
const state = {
  sessionsOffset: 0, queueOffset: 0, busy: false, connected: false,
  refreshRunning: false, tick: 0, actionError: null, monitor: null,
  monitorEnabled: false, analysisBusy: false, sessions: new Map(),
  visibleSessions: new Set(), pinnedSession: null, route: "home",
  homeTab: "douyin", lastMode: undefined,
  queueRequests: new Map(), queueLoading: false,
  localBusy: false, localSessionId: null, localReportId: null,
  localSocket: null, localStream: null, localContext: null, localReport: null,
  models: null, modelJob: null, modelPoll: null, recognition: false, recognitionPending: false,
  mode: "record", segmentSeconds: 30, settingsLocked: false,
};
const timeFormat = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});
const dateFormat = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
});
function clock(value) { return value ? timeFormat.format(new Date(value)) : "—"; }
function stamp(value) { return value ? `${dateFormat.format(new Date(value))} ${clock(value)}` : "—"; }
// 时刻与时长都显示成 HH:MM:SS，加时区后缀避免被读成时长。
function audioTime(id, value) {
  const target = $(id);
  target.replaceChildren(document.createTextNode(clock(value)));
  if (value) target.append(node("small", "北京时间"));
  target.title = stamp(value);
}
function duration(value) {
  const s = Math.floor(value || 0);
  return [Math.floor(s / 3600), Math.floor(s / 60) % 60, s % 60].map((n) => String(n).padStart(2, "0")).join(":");
}
function notice(message) {
  for (const id of ["notice", "page-error"]) {
    $(id).textContent = message || "";
    $(id).hidden = !message;
  }
}
async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (response.status === 401) {
    location.assign("/login");
    throw new Error("请先登录。");
  }
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : body.detail?.detail || "请求失败，请检查输入。");
  return body;
}
function showModelError(message) {
  const error = $("models-error");
  error.textContent = message || "";
  error.hidden = !message;
}
function componentLabel(component, downloading) {
  if (downloading) return "下载中";
  if (component?.ready) return "就绪";
  if (component?.failed || state.modelJob?.state === "failed") return "失败";
  return "未下载";
}
function formatBytes(value) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / (1024 ** index)).toFixed(index >= 3 ? 1 : 0)} ${units[index]}`;
}
let segmentUnit = "seconds";
function setSegmentNotice(message) {
  const element = $("segment-error");
  element.textContent = message || "";
  element.hidden = !message;
}
function updateSegmentLimits() {
  const input = $("segment-seconds");
  if (segmentUnit === "minutes") {
    input.min = "1";
    input.max = "240";
    input.step = "1";
  } else {
    input.min = "1";
    input.max = "14400";
    input.step = "1";
  }
}
function renderSettings() {
  const models = state.models;
  if (!models) return;
  const job = state.modelJob;
  const running = job?.state === "running";
  const unsupported = !models.supported;
  $("models-unsupported").hidden = !unsupported;
  $("mode-lock").hidden = !state.settingsLocked;
  for (const value of ["record", "models", "feishu"]) {
    const radio = $(`mode-${value}`);
    radio.checked = state.mode === value;
    radio.disabled = state.settingsLocked || value === "feishu"
      || (value !== "record" && unsupported);
  }
  $("models-state").textContent = state.mode === "models"
    ? "下载模型识别已开启"
    : state.mode === "feishu"
      ? "飞书秒记尚未支持"
      : state.recognitionPending ? "正在准备模型，完成后启用识别" : "仅录音模式";
  $("models-token").disabled = unsupported || running;
  $("models-token-save").disabled = unsupported || running;
  $("models-token-clear").disabled = unsupported || running || !models.token?.configured;
  $("models-token-masked").textContent = models.token?.configured ? `已保存：${models.token.masked}` : "未配置";
  const downloading = running ? job.current : "";
  const transcriptionBusy = downloading === "asr" || downloading === "punctuation" || (running && job.parts.includes("transcription"));
  const diarizationBusy = downloading === "diarization" || (running && job.parts.includes("diarization"));
  $("models-transcription").querySelector("span").textContent = componentLabel(models.components?.transcription, transcriptionBusy);
  $("models-diarization").querySelector("span").textContent = componentLabel(models.components?.diarization, diarizationBusy);
  const missing = [];
  if (!models.components?.transcription?.ready) missing.push("transcription");
  if (!models.components?.diarization?.ready) missing.push("diarization");
  $("models-download").hidden = unsupported || missing.length === 0;
  $("models-download").disabled = running || !missing.length || state.settingsLocked;
  $("models-download").textContent = running ? "模型下载中…" : "下载缺失模型";
  $("models-progress").hidden = !job || !["running", "completed", "failed"].includes(job.state);
  if (job) {
    const percent = Math.max(0, Math.min(100, Number(job.percent) || 0));
    $("models-progress-bar").value = percent;
    $("models-progress-text").textContent = `${job.state === "running" ? `已下载 ${percent.toFixed(0)}%` : job.state === "completed" ? "下载完成" : "下载失败"} · ${formatBytes(job.downloaded_bytes)} / ${formatBytes(job.total_bytes)}`;
  }
  if (document.activeElement !== $("segment-seconds")) {
    $("segment-seconds").value = segmentUnit === "minutes"
      ? String(Math.round(state.segmentSeconds / 6) / 10)
      : String(Math.round(state.segmentSeconds));
  }
  $("segment-current").textContent = `当前 ${secondsLabel(state.segmentSeconds)} 秒`;
  updateSegmentLimits();
}
async function loadModels() {
  const [models, preferences] = await Promise.all([api("/api/models"), api("/api/preferences")]);
  state.models = models;
  state.modelJob = models.job;
  state.mode = preferences.mode;
  state.recognition = preferences.recognition;
  state.segmentSeconds = preferences.segment_seconds;
  state.settingsLocked = preferences.locked;
  renderSettings();
  syncAnalysisControls();
  updateModelPolling();
}
function syncAnalysisControls() {
  $("realtime").disabled = !state.recognition || state.busy || state.analysisBusy || !state.monitorEnabled;
  $("local-realtime").disabled = !state.recognition || state.localBusy || !!state.localSocket;
  $("analyse-current").disabled = !state.recognition || state.analysisBusy || !state.monitor?.session_id;
  for (const view of state.sessions.values()) {
    if (view.generate) view.generate.disabled = !state.recognition || !!view.busy || !view.hasReadyAudio;
    if (view.retry) view.retry.disabled = !state.recognition || !!view.busy;
    for (const clip of view.clipViews.values()) if (clip.data) updateClip(clip, clip.data);
  }
}
function updateModelPolling() {
  if (state.modelJob?.state === "running" && !document.hidden && !state.modelPoll) {
    state.modelPoll = setInterval(() => pollModelJob().catch((error) => showModelError(error.message)), 1000);
  } else if ((state.modelJob?.state !== "running" || document.hidden) && state.modelPoll) {
    clearInterval(state.modelPoll);
    state.modelPoll = null;
  }
}
async function pollModelJob() {
  if (document.hidden || state.modelJob?.state !== "running") return;
  state.modelJob = await api("/api/models/job");
  if (state.modelJob?.state !== "running") {
    const completed = state.modelJob?.state === "completed";
    const error = state.modelJob?.error;
    const pending = state.recognitionPending;
    state.recognitionPending = false;
    await loadModels();
    if (!completed) showModelError(error || "模型下载失败。");
    else if (pending) await applyMode("models");
  } else renderSettings();
  updateModelPolling();
}
async function beginModelDownload(parts, pendingEnable = false) {
  if (!parts.length) {
    if (pendingEnable) await applyMode("models");
    return;
  }
  state.recognitionPending = pendingEnable;
  showModelError("");
  try {
    await api("/api/models/download", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parts }),
    });
    await loadModels();
  } catch (error) {
    state.recognitionPending = false;
    showModelError(error.message);
  }
}
async function applyMode(mode) {
  try {
    const result = await api("/api/preferences", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    state.mode = result.mode;
    state.recognition = result.recognition;
    state.settingsLocked = result.locked;
    showModelError("");
    await refresh(true);
  } catch (error) {
    showModelError(error.message);
    await loadModels().catch(() => {});
  }
  renderSettings();
  syncAnalysisControls();
}
async function requestMode(mode) {
  if (mode === "feishu") {
    showModelError("飞书秒记尚未支持。");
    renderSettings();
    return;
  }
  if (mode === state.mode) {
    renderSettings();
    return;
  }
  if (mode === "record") {
    state.recognitionPending = false;
    await applyMode("record");
    return;
  }
  const missing = [];
  if (!state.models?.components?.transcription?.ready) missing.push("transcription");
  if (!state.models?.components?.diarization?.ready) missing.push("diarization");
  if (missing.length && !state.models?.token?.configured) {
    showModelError("人物判断需要 Hugging Face Token，请先保存 Token 并接受 Community-1 条款。");
    renderSettings();
    return;
  }
  if (missing.length) {
    await beginModelDownload(missing, true);
    return;
  }
  await applyMode("models");
}
async function saveSegmentSeconds() {
  const value = Number($("segment-seconds").value);
  if (!Number.isFinite(value) || value <= 0) {
    setSegmentNotice("请输入大于 0 的切片时长。");
    return;
  }
  const seconds = segmentUnit === "minutes" ? value * 60 : value;
  if (!Number.isInteger(seconds) || seconds < 1 || seconds > 14400) {
    setSegmentNotice("切片时长必须在 1 秒到 14400 秒之间，且为整数秒。");
    return;
  }
  try {
    const result = await api("/api/preferences", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ segment_seconds: seconds }),
    });
    state.segmentSeconds = result.segment_seconds;
    setSegmentNotice("");
    renderSettings();
    await refresh(true);
  } catch (error) { setSegmentNotice(error.message); }
}
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function button(text, action, className = "text-button") {
  const element = node("button", text, className);
  element.type = "button";
  element.onclick = () => Promise.resolve(action()).catch((error) => {
    state.actionError = error.message;
    notice(error.message);
  });
  return element;
}
function disclosure(title, className) {
  const root = node("details", undefined, className);
  const summary = node("summary", title);
  const content = node("div", undefined, "disclosure-content");
  root.append(summary, content);
  return { root, summary, content };
}
function downloads(path) {
  const root = node("div", undefined, "transcript-downloads");
  for (const format of ["txt", "md", "jsonl"]) {
    const link = node("a", `下载 ${format.toUpperCase()}`);
    link.href = `${path}?format=${format}`;
    root.append(link);
  }
  return root;
}
function play(path, label) {
  $("player-section").hidden = false;
  $("player-label").textContent = label;
  $("player").src = path;
  $("player").play().catch((error) => notice(error.message));
}
function secondsLabel(value) {
  const number = Number(value) || 0;
  return Number.isInteger(number) ? String(number) : number.toFixed(1);
}
function setHomeTab(tab) {
  state.homeTab = tab;
  for (const name of ["douyin", "local"]) {
    const active = name === tab;
    const button = $(`tab-${name}`);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
    $(`panel-${name}`).hidden = !active;
  }
  updateBadge();
}
function updateBadge() {
  const data = state.monitor || {};
  let text;
  let kind;
  if (data.mode === "local") {
    text = labels[data.state] || "正在录音";
    kind = data.state;
  } else if (state.homeTab === "local") {
    text = "未录音";
    kind = "stopped";
  } else {
    text = labels[data.state] || data.state || "正在连接服务";
    kind = data.state || "stopped";
  }
  $("state").textContent = text;
  $("state").className = `badge ${kind}`;
}
function renderMonitor(data) {
  const wasEnabled = state.monitorEnabled;
  state.monitor = data;
  state.monitorEnabled = data.enabled && data.mode !== "local";
  if (state.lastMode !== data.mode) {
    state.lastMode = data.mode;
    if (data.mode === "local") setHomeTab("local");
    else if (data.enabled) setHomeTab("douyin");
  }
  $("segment-rule").textContent = `按采样保存 ${secondsLabel(data.segment_seconds)} 秒 WAV · 停止或断线保留尾段`;
  if (data.mode === "local") {
    $("connection-dot").className = "connection-dot waiting";
    $("anchor").textContent = "本机录音进行中";
    $("live-title").textContent = "请在本机录音卡片停止后，再开启直播间守卫。";
    $("start").disabled = true;
    $("stop").disabled = true;
    $("room").disabled = true;
    $("realtime").disabled = true;
    $("analyse-current").disabled = state.analysisBusy || !data.session_id;
    updateBadge();
    notice(data.last_error || state.actionError);
    return;
  }
  if (data.enabled) $("realtime").checked = data.realtime_analysis;
  else if (wasEnabled) $("realtime").checked = false;
  $("realtime").disabled = state.busy || state.analysisBusy;
  $("connection-dot").className = `connection-dot ${data.state}`;

  $("anchor").textContent = data.anchor_name || (data.enabled ? "正在获取直播间信息" : "准备开始监听");
  $("live-title").textContent = data.title || (data.state === "waiting" ? "直播间尚未开播，守卫会持续等待。" : "输入直播间，开启后可关闭此网页；本机服务需要保持运行。");
  $("duration").textContent = duration(data.captured_seconds);
  $("segment-count").replaceChildren(document.createTextNode(`${data.segment_count} `), node("small", "段"));
  audioTime("last-audio", data.last_audio_at);
  $("retry").textContent = data.retry_in_seconds !== null ? `${Math.ceil(data.retry_in_seconds)} 秒后检查` : "";
  $("save-path").textContent = data.data_dir;
  $("save-path").title = data.data_dir;
  $("start").disabled = state.busy || data.enabled;
  $("stop").disabled = state.busy || (!data.enabled && !data.session_id);
  $("room").disabled = data.enabled || state.busy;
  if (data.enabled) $("room").value = data.room_id;
  $("analyse-current").disabled = state.analysisBusy || !data.session_id;
  notice(data.last_error || state.actionError);
}
// Stable nodes preserve disclosure, scroll and media state during polling.
function reconcile(parent, children) {
  children.forEach((child, index) => {
    if (parent.children[index] !== child) parent.insertBefore(child, parent.children[index] || null);
  });
  while (parent.children.length > children.length) parent.lastElementChild.remove();
}
function createReport(path, note) {
  const root = node("div", undefined, "report");
  const list = node("div", undefined, "utterances");
  const empty = node("p", "尚无已完成的发言记录；静音不会生成文字。", "empty");
  const more = button("加载更多发言", () => loadReport(report));
  more.hidden = true;
  root.append(node("p", note, "subtle transcript-note"), list, empty, more);
  const report = { root, list, empty, more, path, run: null, cursor: 0, rows: new Map(), loading: false, events: [] };
  return report;
}
function compare(left, right) {
  for (const key of ["start_at", "end_at", "id"]) {
    if (left[key] < right[key]) return -1;
    if (left[key] > right[key]) return 1;
  }
  return 0;
}
function insertRecord(report, item, element) {
  const existing = Array.from(report.list.children).find((child) => compare(item, child.record) < 0);
  element.record = item;
  report.list.insertBefore(element, existing || null);
}
function syncGaps(report) {
  for (const event of report.events.filter((item) => item.kind === "gap")) {
    const id = `gap-${event.id}`;
    const text = `${stamp(event.started_at)} — ${event.ended_at ? stamp(event.ended_at) : "等待恢复"} · 采集缺口 · ${event.detail}`;
    if (report.rows.has(id)) { report.rows.get(id).textContent = text; continue; }
    const item = { id, start_at: event.started_at, end_at: event.ended_at || event.started_at };
    const row = node("p", text, "gap-record");
    report.rows.set(id, row);
    insertRecord(report, item, row);
  }
}
async function loadReport(report) {
  if (report.loading) return;
  report.loading = true;
  try {
    let page = await api(`${report.path}&after=${report.cursor}&limit=100`);
    if (page.run_id !== report.run) {
      report.run = page.run_id;
      report.cursor = 0;
      report.rows.clear();
      report.list.replaceChildren();
      page = await api(`${report.path}&after=0&limit=100`);
    }
    for (const item of page.items) {
      if (report.rows.has(item.id)) continue;
      const row = node("article", undefined, "utterance");
      const meta = node("div", undefined, "utterance-meta");
      meta.append(node("strong", item.speaker_label), node("span", `${stamp(item.start_at)} — ${clock(item.end_at)}`));
      if (item.cross_segment) meta.append(node("span", "跨段发言", "badge"));
      meta.append(button("回听发言", () => play(`/api/utterances/${item.id}/audio`, `${item.speaker_label} · ${stamp(item.start_at)}`)));
      row.append(meta, node("p", item.text || "检测到讲话，但未识别出文字。"));
      if (item.flags.includes("overlap")) row.classList.add("overlap");
      report.rows.set(item.id, row);
      insertRecord(report, item, row);
    }
    report.cursor = page.next_cursor;
    report.more.hidden = page.items.length < 100;
    syncGaps(report);
    report.empty.hidden = report.rows.size > 0;
  } finally { report.loading = false; }
}
function createSession(session) {
  const view = disclosure("", "session-archive");
  view.id = session.id;
  view.root.id = `session-${session.id}`;
  view.heading = node("strong");
  view.description = node("small");
  view.summary.replaceChildren(view.heading, view.description);
  view.info = node("p", "", "subtle");
  view.progress = node("p", "", "subtle");
  view.error = node("p", "", "archive-error");
  view.generate = button("分析本场全部切片", () => requestSession(view.id), "secondary");
  view.retry = button("重试未完成分析", () => requestSession(view.id, true), "secondary");
  view.retry.hidden = true;
  const actions = node("div", undefined, "archive-actions");
  actions.append(view.generate, view.retry);
  view.feedback = node("p", "", "analysis-feedback");
  view.feedback.setAttribute("role", "status");
  view.feedback.setAttribute("aria-live", "polite");
  view.feedback.hidden = true;
  view.whole = disclosure("整场综合文本记录", "whole-record");
  view.report = createReport(`/api/sessions/${view.id}/utterances?`, "音频接收时间（北京时间）；人物编号在本场次、此分析版本内有效。重叠讲话保留混合文本。");
  view.downloads = downloads(`/api/sessions/${view.id}/transcript`);
  view.downloads.hidden = true;
  view.whole.content.append(view.downloads, view.report.root);
  view.whole.root.ontoggle = () => { if (view.whole.root.open) loadReport(view.report).catch((error) => notice(error.message)); };
  view.clips = node("div", undefined, "clip-list");
  view.clipViews = new Map();
  view.offset = 0;
  const pages = node("div", undefined, "pagination");
  view.prev = button("上一页切片", async () => { view.offset = Math.max(0, view.offset - 100); await loadSession(view); });
  view.next = button("下一页切片", async () => { view.offset += 100; await loadSession(view); });
  view.page = node("span", "1");
  pages.append(view.prev, view.page, view.next);
  view.content.append(view.info, actions, view.feedback, view.progress, view.error, view.whole.root, node("h3", "时间切片"), view.clips, pages);
  view.root.ontoggle = () => {
    if (view.root.open) loadSession(view).catch((error) => notice(error.message));
    else for (const child of view.content.querySelectorAll("details[open]")) child.open = false;
  };
  updateSession(view, session);
  state.sessions.set(view.id, view);
  return view;
}
function updateSession(view, session) {
  view.data = session;
  view.heading.textContent = `${session.anchor_name || `直播间 ${session.room_id}`} · ${stamp(session.started_at)}`;
  view.description.textContent = `${session.ended_at ? "已结束" : "场次未结束"} · ${duration(session.duration_seconds)} · ${session.segment_count} 段`;
  view.info.textContent = `${session.title || "直播记录"} · 房间 ${session.room_id} · 场次 ${session.id}`;
  if (!state.recognition) {
    view.generate.disabled = true;
    view.retry.disabled = true;
  }
}
function renderSessionList() {
  const views = Array.from(state.sessions.values()).filter((v) => state.visibleSessions.has(v.id) || v.id === state.pinnedSession);
  views.sort((a, b) => b.data.started_at.localeCompare(a.data.started_at));
  if (!views.length) {
    if (!$('sessions').querySelector('.empty')) $("sessions").replaceChildren(node("p", "暂无采集记录", "empty"));
  } else reconcile($("sessions"), views.map((v) => v.root));
}
async function loadSessions() {
  const sessions = await api(`/api/sessions?limit=50&offset=${state.sessionsOffset}`);
  state.visibleSessions = new Set(sessions.map((s) => s.id));
  for (const session of sessions) updateSession(state.sessions.get(session.id) || createSession(session), session);
  renderSessionList();
  $("sessions-prev").disabled = state.sessionsOffset === 0;
  $("sessions-next").disabled = sessions.length < 50;
  $("sessions-page").textContent = String(state.sessionsOffset / 50 + 1);
}
function createClip(view, segment) {
  const clip = disclosure("", "clip");
  clip.id = segment.id;
  clip.root.id = `segment-${segment.id}`;
  clip.title = node("strong");
  clip.meta = node("small");
  clip.summary.replaceChildren(clip.title, clip.meta);
  clip.scope = "session";
  clip.chosen = false;
  clip.status = node("p", "", "subtle");
  clip.error = node("p", "", "archive-error");
  const actions = node("div", undefined, "archive-actions");
  clip.analyse = button("分析", () => requestClip(clip), "secondary");
  clip.reanalyse = button("重新分析", () => requestClip(clip, true), "secondary");
  clip.retry = button("重试未完成分析", () => requestClip(clip, false, true));
  const download = node("a", "下载 WAV", "text-button");
  download.href = `/api/segments/${segment.id}/audio?download=true`;
  actions.append(clip.analyse, clip.reanalyse, clip.retry, button("回听切片", () => play(`/api/segments/${segment.id}/audio`, `第 ${segment.sequence} 段 · ${stamp(segment.start_at)}`)), download);
  const tabs = node("div", undefined, "result-tabs");
  clip.wholeTab = button("整场记录", async () => { clip.chosen = true; clip.scope = "session"; await loadClip(clip); });
  clip.reviewTab = button("单片复核", async () => { clip.chosen = true; clip.scope = "segment"; await loadClip(clip); });
  tabs.append(clip.wholeTab, clip.reviewTab);
  clip.reports = {
    session: createReport(`/api/segments/${segment.id}/utterances?scope=session`, "来自整场记录；人物编号在本场次内有效。跨段发言也可在相邻切片查看。"),
    segment: createReport(`/api/segments/${segment.id}/utterances?scope=segment`, "独立单片复核；人物编号仅在此切片的此版本内有效。复核不会更新整场记录。"),
  };
  clip.downloads = downloads(`/api/segments/${segment.id}/transcript`);
  clip.content.append(actions, clip.status, clip.error, tabs, clip.downloads, clip.reports.session.root, clip.reports.segment.root);
  clip.root.ontoggle = () => { if (clip.root.open) loadClip(clip).catch((error) => notice(error.message)); };
  view.clipViews.set(segment.id, clip);
  view.clips.append(clip.root);
  clip.view = view;
  updateClip(clip, segment);
  return clip;
}
function updateClip(clip, segment) {
  clip.data = segment;
  const status = segment.analysis;
  clip.title.textContent = `第 ${segment.sequence} 段 · ${stamp(segment.start_at)} — ${clock(segment.end_at)}`;
  clip.meta.textContent = `${(segment.sample_count / segment.sample_rate).toFixed(2)} 秒 · 整场：${analysisLabels[status.session_state]} · 复核：${analysisLabels[status.review_state]}`;
  if (!clip.chosen) clip.scope = status.session_state === "complete" || !status.available_run_id ? "session" : "segment";
  const busy = clip.busy || ["pending", "processing", "needs_setup", "waiting_context"].includes(status.review_state);
  clip.analyse.disabled = !state.recognition || busy || status.review_state !== "not_requested";
  clip.reanalyse.disabled = !state.recognition || busy || status.review_state === "not_requested";
  clip.retry.hidden = status.review_state !== "needs_attention";
  clip.retry.disabled = !state.recognition || !!clip.busy;
}
async function loadClip(clip) {
  if (clip.loading) return;
  clip.loading = true;
  try {
    const result = await api(`/api/segments/${clip.id}/analysis`);
    clip.status.textContent = `单片复核：${analysisLabels[result.state]}${result.revision ? ` · 请求 v${result.revision}` : ""}${result.available_revision ? ` · 当前显示成功版本 v${result.available_revision}` : ""}`;
    clip.error.textContent = result.last_error || result.export_error || "";
    for (const scope of ["session", "segment"]) clip.reports[scope].root.hidden = clip.scope !== scope;
    clip.wholeTab.setAttribute("aria-pressed", String(clip.scope === "session"));
    clip.reviewTab.setAttribute("aria-pressed", String(clip.scope === "segment"));
    clip.downloads.hidden = clip.scope !== "segment" || !result.available_run_id;
    await loadReport(clip.reports[clip.scope]);
  } finally { clip.loading = false; }
}
async function loadSession(view, around = null) {
  if (view.loading) return;
  view.loading = true;
  try {
    const [segments, result, events] = await Promise.all([
      api(`/api/sessions/${view.id}/segments?limit=100&offset=${view.offset}${around ? `&around=${around}` : ""}`),
      api(`/api/sessions/${view.id}/analysis`), api(`/api/sessions/${view.id}/events`),
    ]);
    if (around && segments.length) {
      // Offset is exposed by counting earlier ready clips, including sequence gaps.
      view.offset = segments[0].page_offset;
    }
    view.progress.textContent = `${analysisLabels[result.state]} · 已处理 ${duration(result.processed_seconds)} / 请求 ${duration(result.target_seconds)} · 待处理 ${duration(result.backlog_seconds)} · 尚未请求 ${duration(result.unrequested_seconds)}${result.pending_speech ? " · 等待跨段发言结束" : ""}`;
    view.error.textContent = result.last_error || result.export_error || "";
    view.hasReadyAudio = segments.length > 0 || view.data.segment_count > 0;
    view.generate.disabled = !!view.busy || !view.hasReadyAudio;
    view.generate.textContent = view.busy ? "正在提交…" : "分析本场全部切片";
    view.retry.hidden = result.state !== "needs_attention";
    view.retry.disabled = !!view.busy;
    view.downloads.hidden = !result.id;
    view.report.events = events;
    const ids = new Set(segments.map((s) => s.id));
    for (const segment of segments) updateClip(view.clipViews.get(segment.id) || createClip(view, segment), segment);
    const ordered = Array.from(view.clipViews.values()).sort((a, b) => a.data.sequence - b.data.sequence);
    reconcile(view.clips, ordered.map((clip) => clip.root));
    for (const clip of ordered) clip.root.hidden = !ids.has(clip.id);
    if (!ordered.length) view.clips.replaceChildren(node("p", "尚无已保存完成的切片；停止时会保留尾段。", "empty"));
    view.prev.disabled = view.offset === 0;
    view.next.disabled = segments.length < 100;
    view.page.textContent = String(view.offset / 100 + 1);
    if (view.whole.root.open) await loadReport(view.report);
    for (const clip of ordered) if (!clip.root.hidden && clip.root.open) await loadClip(clip);
  } finally { view.loading = false; }
}
async function post(path, body) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
}
async function requestSession(id, retry = false) {
  if (!state.recognition) {
    showModelError("当前为仅录音模式，请先开启完整识别。");
    return;
  }
  let view = state.sessions.get(id);
  if (view?.busy) return;
  state.actionError = null;
  if (view) {
    view.busy = true;
    view.generate.disabled = true;
    view.generate.textContent = "正在提交…";
    view.retry.disabled = true;
    view.feedback.hidden = false;
    view.feedback.dataset.kind = "success";
    view.feedback.textContent = "正在提交本场分析请求…";
  }
  let result;
  try {
    result = await post(`/api/sessions/${id}/analysis`, { retry });
    if (!view) view = createSession(await api(`/api/sessions/${id}`));
    if (!state.visibleSessions.has(id)) state.pinnedSession = id;
    renderSessionList();
    view.feedback.hidden = false;
    view.feedback.dataset.kind = result.state === "needs_attention" ? "error" : "success";
    view.feedback.textContent = result.state === "needs_attention"
      ? "本场分析需要处理，请查看下方错误并点击“重试未完成分析”。"
      : !result.target_seconds
        ? "本场尚无保存完成的音频切片，下一段保存后可再次分析。"
        : result.backlog_seconds || result.pending_speech
          ? `本场分析请求已提交，待处理 ${duration(result.backlog_seconds)}${result.pending_speech ? "，正在等待当前发言完成" : ""}。可在分析队列查看进度。`
          : "本场已保存的音频已全部分析完成，已展开整场综合文本记录。";
    view.root.open = true;
    view.whole.root.open = true;
    await loadSession(view);
    await loadReport(view.report);
    await loadQueue();
  } catch (error) {
    if (view) {
      view.feedback.hidden = false;
      view.feedback.dataset.kind = "error";
      view.feedback.textContent = `${result ? "请求已提交，但状态刷新失败" : "分析请求失败"}：${error.message}`;
    }
    throw error;
  } finally {
    if (view) {
      view.busy = false;
      view.generate.disabled = !(view.hasReadyAudio || view.data.segment_count);
      view.generate.textContent = "分析本场全部切片";
      view.retry.disabled = false;
    }
  }
}
async function requestClip(clip, reanalyse = false, retry = false) {
  if (!state.recognition) {
    showModelError("当前为仅录音模式，请先开启完整识别。");
    return;
  }
  clip.busy = true;
  updateClip(clip, clip.data);
  try {
    await post(`/api/segments/${clip.id}/analysis`, { reanalyse, retry });
    clip.scope = "segment";
    clip.chosen = true;
  } finally { clip.busy = false; await loadSession(clip.view); }
  await loadQueue();
}
function taskType(task) {
  return (task.scope === "segment" ? `单片复核 v${task.revision}` : "整场分析") + (task.kind === "flush" ? " · 发言收尾" : "");
}
function createQueueRow(task) {
  const row = node("article", undefined, "queue-row");
  const jump = button(`第 ${task.sequence} 段 · ${analysisLabels[task.state] || task.state}`, () => jumpTo(task));
  row.append(jump, node("p", `分段 ID：${task.segment_id}`, "subtle"), node("p", `${stamp(task.start_at)} — ${clock(task.end_at)}`, "subtle"));
  if (task.last_error) row.append(node("p", task.last_error, "archive-error"));
  return row;
}
function createQueueRequest(record) {
  const view = disclosure("", "queue-request");
  view.id = record.run_id;
  view.offset = 0;
  view.title = node("strong");
  view.status = node("span", undefined, "badge");
  const heading = node("div", undefined, "queue-request-heading");
  heading.append(view.title, view.status);
  view.submitted = node("p", undefined, "subtle");
  view.progress = node("progress");
  view.progress.max = 100;
  view.percent = node("span");
  const bar = node("div", undefined, "queue-progress");
  bar.append(view.progress, view.percent);
  view.counts = node("p", undefined, "subtle");
  view.current = node("p", undefined, "queue-current");
  view.summary.append(heading, view.submitted, bar, view.counts, view.current);
  const actions = node("div", undefined, "archive-actions");
  actions.append(button("查看音频档案", () => jumpTo({ ...view.data, sequence: view.data.scope === "segment" ? view.data.target_sequence : undefined })));
  view.retry = button("重试未完成分析", async () => {
    const task = view.data;
    if (task.scope === "segment") await post(`/api/segments/${task.segment_id}/analysis`, { retry: true });
    else await post(`/api/sessions/${task.session_id}/analysis`, { retry: true });
    await loadQueue();
  });
  actions.append(view.retry);
  view.error = node("p", undefined, "archive-error");
  view.detailsError = node("p", undefined, "archive-error");
  view.items = node("div");
  view.prev = button("上一页切片", async () => { view.offset = Math.max(0, view.offset - 50); await loadRequestSegments(view); });
  view.next = button("下一页切片", async () => { view.offset += 50; await loadRequestSegments(view); });
  view.page = node("span", "1");
  const pagination = node("div", undefined, "pagination");
  pagination.append(view.prev, view.page, view.next);
  view.content.append(actions, node("p", `请求 ID：${record.run_id} · 场次：${record.session_id}`, "subtle queue-identities"), view.error, view.detailsError, view.items, pagination);
  view.root.ontoggle = () => {
    if (view.root.open) loadRequestSegments(view).catch((error) => {
      view.detailsError.textContent = `切片状态读取失败：${error.message}`;
    });
  };
  state.queueRequests.set(view.id, view);
  return view;
}
function updateQueueRequest(view, record, active) {
  view.data = record;
  view.title.textContent = `${record.anchor_name} · ${stamp(record.session_started_at)}`;
  view.status.textContent = analysisLabels[record.state] || record.state;
  view.root.dataset.state = record.state;
  view.submitted.textContent = `${taskType(record)} · 提交于 ${stamp(record.created_at)}`;
  view.progress.value = record.progress_percent;
  view.progress.setAttribute("aria-label", `${record.anchor_name} · ${taskType(record)}进度`);
  view.progress.setAttribute("aria-valuetext", `${record.progress_percent}%：已完成 ${record.completed_segments} / ${record.total_segments} 个切片`);
  view.percent.textContent = `${record.progress_percent}%`;
  view.counts.textContent = `已完成 ${record.completed_segments} / ${record.total_segments} 个切片 · 音频 ${duration(record.processed_seconds)} / ${duration(record.target_seconds)}`;
  const finishing = record.pending_speech && record.completed_segments === record.total_segments;
  view.current.hidden = !active && !finishing;
  view.current.textContent = active ? `正在${active.kind === "flush" ? "整理末尾发言" : `分析第 ${active.sequence} 段`} · 已用 ${duration(active.elapsed_seconds)}` : "切片已处理，末尾发言待整理；完成后进度达到 100%";
  view.error.textContent = record.last_error || record.export_error || "";
  view.retry.hidden = record.state !== "needs_attention";
  view.retry.disabled = !state.recognition || record.state !== "needs_attention";
}
async function loadRequestSegments(view) {
  if (view.loading) return;
  view.loading = true;
  try {
    let page = await api(`/api/analysis/requests/${view.id}/segments?offset=${view.offset}&limit=50`);
    if (view.offset && view.offset >= page.total_count) {
      view.offset = Math.max(0, Math.floor((page.total_count - 1) / 50) * 50);
      page = await api(`/api/analysis/requests/${view.id}/segments?offset=${view.offset}&limit=50`);
    }
    const signature = JSON.stringify(page.items);
    if (view.items.dataset.signature !== signature) {
      view.items.dataset.signature = signature;
      view.items.replaceChildren(...(page.items.length ? page.items.map(createQueueRow) : [node("p", "本次请求尚无保存完成的音频切片", "empty")]));
    }
    view.prev.disabled = view.offset === 0;
    view.next.disabled = !page.has_more;
    view.page.textContent = String(view.offset / 50 + 1);
    view.detailsError.textContent = "";
  } finally { view.loading = false; }
}
async function loadQueue() {
  if (state.queueLoading) return;
  state.queueLoading = true;
  try {
    const queue = await api("/api/analysis/queue?limit=1");
    $("queue-badge").textContent = String(queue.waiting_count);
    if (state.route !== "queue") return;
    let records = await api(`/api/analysis/requests?offset=${state.queueOffset}&limit=50`);
    if (state.queueOffset && state.queueOffset >= records.total_count) {
      state.queueOffset = Math.max(0, Math.floor((records.total_count - 1) / 50) * 50);
      records = await api(`/api/analysis/requests?offset=${state.queueOffset}&limit=50`);
    }
    $("queue-summary").textContent = `共 ${records.total_count} 条分析记录 · 排队中 ${queue.waiting_count} 个切片任务 · ${queue.active ? "正在分析" : "当前空闲"}`;
    const roots = records.items.map((record) => {
      const view = state.queueRequests.get(record.run_id) || createQueueRequest(record);
      updateQueueRequest(view, record, queue.active?.run_id === record.run_id ? queue.active : null);
      return view.root;
    });
    const order = records.items.map((r) => r.run_id).join(",");
    if ($("queue-records").dataset.order !== order || !$("queue-records").childElementCount) {
      $("queue-records").dataset.order = order;
      $("queue-records").replaceChildren(...(roots.length ? roots : [node("p", "暂无分析记录。请在音频档案中点击“分析本场全部切片”。", "empty")]));
    }
    for (const record of records.items) {
      const view = state.queueRequests.get(record.run_id);
      if (view.root.open) {
        try { await loadRequestSegments(view); }
        catch (error) { view.detailsError.textContent = `切片状态读取失败：${error.message}`; }
      }
    }
    $("queue-prev").disabled = state.queueOffset === 0;
    $("queue-next").disabled = !records.has_more;
    $("queue-page").textContent = String(state.queueOffset / 50 + 1);
  } finally { state.queueLoading = false; }
}
async function jumpTo(task) {
  const session = await api(`/api/sessions/${task.session_id}`);
  const view = state.sessions.get(session.id) || createSession(session);
  updateSession(view, session);
  state.pinnedSession = session.id;
  renderSessionList();
  location.hash = "/home";
  view.root.open = true;
  // A queued clip can belong to a different archive page.
  while (view.loading) await new Promise((resolve) => setTimeout(resolve, 20));
  await loadSession(view, task.sequence);
  const clip = view.clipViews.get(task.segment_id);
  if (clip) { clip.root.open = true; await loadClip(clip); clip.root.scrollIntoView({ block: "center" }); }
  else view.root.scrollIntoView({ block: "start" });
}
function routeViewState(hash) {
  const route = hash === "#/queue"
    ? "queue"
    : hash === "#/settings" || hash === "#/models"
      ? "settings"
      : hash === "#/password"
        ? "password"
        : "home";
  return {
    route,
    views: {
      home: route !== "home", queue: route !== "queue", settings: route !== "settings",
      password: route !== "password",
    },
    nav: {
      home: route === "home" ? "page" : "false",
      queue: route === "queue" ? "page" : "false",
      settings: route === "settings" ? "page" : "false",
      password: route === "password" ? "page" : "false",
    },
  };
}
function route() {
  setMenuOpen(false);
  const routed = routeViewState(location.hash);
  state.route = routed.route;
  $("home-view").hidden = routed.views.home;
  $("queue-view").hidden = routed.views.queue;
  $("settings-view").hidden = routed.views.settings;
  $("password-view").hidden = routed.views.password;
  $("nav-home").setAttribute("aria-current", routed.nav.home);
  $("nav-queue").setAttribute("aria-current", routed.nav.queue);
  $("nav-settings").setAttribute("aria-current", routed.nav.settings);
  $("nav-password").setAttribute("aria-current", routed.nav.password);
  if (routed.route === "settings") loadModels().catch((error) => showModelError(error.message));
  loadQueue().catch((error) => notice(error.message));
}
function workletUrl() {
  const code = `
    function concat(a, b) {
      const out = new Float32Array(a.length + b.length);
      out.set(a); out.set(b, a.length); return out;
    }
    class PCMEncoder extends AudioWorkletProcessor {
      constructor(options) {
        super();
        this.ratio = sampleRate / options.processorOptions.targetRate;
        this.pos = 0;
        this.tail = new Float32Array(0);
      }
      process(inputs) {
        const input = inputs[0][0];
        if (!input) return true;
        const buffer = this.tail.length ? concat(this.tail, input) : input;
        const out = [];
        let pos = this.pos;
        while (Math.floor(pos) + 1 < buffer.length) {
          const i = Math.floor(pos), frac = pos - i;
          out.push(buffer[i] * (1 - frac) + buffer[i + 1] * frac);
          pos += this.ratio;
        }
        const consumed = Math.floor(pos);
        this.tail = buffer.slice(consumed);
        this.pos = pos - consumed;
        if (out.length) {
          const pcm = new Int16Array(out.length);
          for (let i = 0; i < out.length; i++) {
            const v = Math.max(-1, Math.min(1, out[i]));
            pcm[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
          }
          this.port.postMessage(pcm.buffer, [pcm.buffer]);
        }
        return true;
      }
    }
    registerProcessor("pcm-encoder", PCMEncoder);
  `;
  return URL.createObjectURL(new Blob([code], { type: "application/javascript" }));
}
async function acquireStream(source) {
  if (source === "system") {
    const media = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
    if (!media.getAudioTracks().length) {
      for (const track of media.getTracks()) track.stop();
      throw new Error("未共享音频。请在弹窗中选择“分享标签页音频/系统音频”。");
    }
    for (const track of media.getVideoTracks()) track.stop();
    return media;
  }
  return navigator.mediaDevices.getUserMedia({ audio: true });
}
async function startAudioStream(socket, stream) {
  const context = new AudioContext({ sampleRate: 16000 });
  await context.audioWorklet.addModule(workletUrl());
  const source = context.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(context, "pcm-encoder", {
    processorOptions: { targetRate: 16000 },
  });
  node.port.onmessage = (event) => {
    if (socket.readyState === WebSocket.OPEN) socket.send(event.data);
  };
  const sink = context.createGain();
  sink.gain.value = 0;
  source.connect(node).connect(sink).connect(context.destination);
  state.localContext = context;
  return node;
}
function resetLocalReport() {
  const report = state.localReport;
  if (!report) return;
  report.run = null;
  report.cursor = 0;
  report.rows.clear();
  report.list.replaceChildren();
  report.empty.hidden = false;
  report.more.hidden = true;
}
function localNotice(message) {
  const element = $("local-notice");
  element.textContent = message || "";
  element.hidden = !message;
}
async function startLocal() {
  if (state.localBusy) return;
  state.localBusy = true;
  updateLocal();
  try {
    if (!navigator.mediaDevices) throw new Error("此浏览器不支持音频采集。");
    const source = $("local-source").value;
    const stream = await acquireStream(source);
    const realtime = state.recognition && $("local-realtime").checked;
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(
      `${scheme}://${location.host}/api/local/audio?source=${source}&realtime=${realtime}`,
    );
    socket.binaryType = "arraybuffer";
    state.localSocket = socket;
    state.localStream = stream;
    await new Promise((resolve, reject) => {
      socket.onerror = () => reject(new Error("录音连接失败，请检查浏览器权限。"));
      socket.onclose = () => reject(new Error("录音连接已关闭。"));
      socket.onopen = () => resolve();
    });
    const hello = await new Promise((resolve, reject) => {
      socket.onerror = () => reject(new Error("录音连接失败。"));
      socket.onclose = () => reject(new Error("录音连接已关闭。"));
      socket.onmessage = (event) => resolve(JSON.parse(event.data));
    });
    if (hello.error) throw new Error(hello.error);
    socket.onclose = () => { if (!state.localBusy) stopLocal(); };
    socket.onerror = null;
    socket.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      if (payload.error) localNotice(payload.error);
    };
    state.localSessionId = hello.session_id;
    resetLocalReport();
    const links = downloads(`/api/sessions/${hello.session_id}/transcript`);
    $("local-downloads").replaceChildren(...links.children);
    $("local-downloads").hidden = false;
    await startAudioStream(socket, stream);
    const track = stream.getAudioTracks()[0];
    if (track) track.onended = () => stopLocal();
  } catch (error) {
    localNotice(error.message);
    await cleanupLocal();
  } finally {
    state.localBusy = false;
    updateLocal();
    await refresh(true);
  }
}
async function stopLocal() {
  if (state.localBusy) return;
  state.localBusy = true;
  updateLocal();
  try { await cleanupLocal(); }
  finally {
    state.localBusy = false;
    state.localSessionId = null;
    updateLocal();
    await refresh(true);
  }
}
async function cleanupLocal() {
  if (state.localSocket) {
    try { state.localSocket.close(); } catch (error) { void error; }
    state.localSocket = null;
  }
  if (state.localStream) {
    for (const track of state.localStream.getTracks()) track.stop();
    state.localStream = null;
  }
  if (state.localContext) {
    try { await state.localContext.close(); } catch (error) { void error; }
    state.localContext = null;
  }
}
function updateLocal() {
  const data = state.monitor || {};
  const local = data.mode === "local";
  const busyElsewhere = !!data.enabled && !local;
  $("local-toggle").disabled = state.localBusy;
  $("local-toggle").textContent = state.localBusy
    ? "处理中…"
    : local ? "停止录音" : "开启录音";
  $("local-source").disabled = state.localBusy || local || busyElsewhere;
  $("local-realtime").disabled = !state.recognition || state.localBusy || local || busyElsewhere;
  if (local) $("local-realtime").checked = data.realtime_analysis;
  $("local-title").textContent = local ? data.title || "本机录音" : "未开始";
  $("local-status-text").textContent = local
    ? (data.last_audio_at ? `正在采集 · 最近到达 ${clock(data.last_audio_at)}` : "正在等待音频…")
    : busyElsewhere
      ? "请先停止直播间守卫。"
      : "点击“开启录音”开始采集本机音频。";
  $("local-dot").className = `connection-dot ${local ? data.state : "stopped"}`;
  $("local-duration").textContent = local ? duration(data.captured_seconds) : "00:00:00";
  $("local-count").replaceChildren(
    document.createTextNode(`${local ? data.segment_count : 0} `), node("small", "段"),
  );
  audioTime("local-last", local ? data.last_audio_at : null);
}
$("local-toggle").onclick = () => {
  const local = state.monitor && state.monitor.mode === "local";
  return (local ? stopLocal() : startLocal()).catch((error) => localNotice(error.message));
};
for (const name of ["douyin", "local"]) {
  $(`tab-${name}`).onclick = () => setHomeTab(name);
  $(`tab-${name}`).onkeydown = (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    setHomeTab(name === "douyin" ? "local" : "douyin");
    $(`tab-${state.homeTab}`).focus();
  };
}
$("models-token-save").onclick = async () => {
  const token = $("models-token").value.trim();
  if (!token) {
    showModelError("请输入 Hugging Face Token。");
    return;
  }
  try {
    const result = await api("/api/models/token", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    $("models-token").value = "";
    state.models = { ...state.models, token: result.token };
    renderSettings();
    showModelError(result.warning || "Token 已保存。");
  } catch (error) { showModelError(error.message); }
};
$("models-token-clear").onclick = async () => {
  try {
    await api("/api/models/token", { method: "DELETE" });
    if (state.mode !== "record") {
      const result = await api("/api/preferences", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "record" }),
      });
      state.mode = result.mode;
      state.recognition = result.recognition;
    }
    state.models = { ...state.models, token: { configured: false, masked: "", source: "none" } };
    renderSettings();
    syncAnalysisControls();
    showModelError("Token 已清除。");
  } catch (error) { showModelError(error.message); }
};
$("models-download").onclick = async () => {
  const parts = [];
  if (!state.models?.components?.transcription?.ready) parts.push("transcription");
  if (!state.models?.components?.diarization?.ready) parts.push("diarization");
  await beginModelDownload(parts);
};
for (const mode of ["record", "models"]) {
  $(`mode-${mode}`).onchange = () => {
    if (!$(`mode-${mode}`).checked) return;
    requestMode(mode).catch((error) => showModelError(error.message));
  };
}
$("segment-save").onclick = () => {
  saveSegmentSeconds().catch((error) => setSegmentNotice(error.message));
};
$("segment-unit").onchange = () => {
  const next = $("segment-unit").value;
  const value = Number($("segment-seconds").value);
  if (Number.isFinite(value)) {
    const seconds = segmentUnit === "minutes" ? value * 60 : value;
    $("segment-seconds").value = next === "minutes"
      ? String(Math.round(seconds / 6) / 10)
      : String(Math.round(seconds));
  }
  segmentUnit = next;
  updateSegmentLimits();
};
async function refresh(force = false) {
  if (state.refreshRunning) return;
  state.refreshRunning = true;
  try {
    const [monitor, global] = await Promise.all([api("/api/monitor"), api("/api/analysis/status")]);
    state.connected = true;
    renderMonitor(monitor);
    updateLocal();
    if (state.settingsLocked !== !!monitor.enabled) {
      state.settingsLocked = !!monitor.enabled;
      renderSettings();
    }
    $("analysis-global").textContent = state.recognition && global.models.ready ? (global.worker_state === "processing" ? "模型正在分析音频" : "模型已就绪") : "仅录音模式";
    $("model-hint").textContent = global.last_error || global.models.reason || "本地运行：Community-1 人物判断 + Paraformer 普通话转写。分段分析会有处理延迟。";
    if (force || state.tick++ % 5 === 0) await loadSessions();
    if (state.route === "home") {
      for (const view of state.sessions.values()) if (view.root.isConnected && view.root.open) await loadSession(view);
      if (monitor.mode === "local" && monitor.session_id && state.localReport) {
        if (state.localReportId !== monitor.session_id) {
          state.localReportId = monitor.session_id;
          state.localReport.path = `/api/sessions/${monitor.session_id}/utterances?`;
          resetLocalReport();
        }
        await loadReport(state.localReport);
      }
    }
    await loadQueue();
  } catch (error) {
    state.connected = false;
    $("state").textContent = "服务或数据读取异常";
    $("state").className = "badge needs_attention";
    $("start").disabled = true;
    $("stop").disabled = true;
    notice(error.message);
  } finally { state.refreshRunning = false; syncAnalysisControls(); }
}
async function act(path, body) {
  state.actionError = null;
  state.busy = true;
  $("start").disabled = true;
  $("stop").disabled = true;
  try { renderMonitor(await post(path, body)); }
  catch (error) { state.actionError = error.message; notice(error.message); }
  finally { state.busy = false; await refresh(true); }
}
$("monitor-form").onsubmit = (event) => {
  event.preventDefault();
  act("/api/monitor/start", { room: $("room").value, realtime_analysis: $("realtime").checked });
};
$("stop").onclick = () => act("/api/monitor/stop");
$("analyse-current").onclick = async () => {
  if (!state.monitor?.session_id) return;
  state.analysisBusy = true;
  $("analyse-current").disabled = true;
  try { await requestSession(state.monitor.session_id); }
  catch (error) { notice(error.message); }
  finally { state.analysisBusy = false; await refresh(); }
};
$("realtime").onchange = async () => {
  if (!state.monitorEnabled) return;
  state.analysisBusy = true;
  $("realtime").disabled = true;
  try {
    await api("/api/analysis/realtime", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: $("realtime").checked }) });
  } catch (error) { state.actionError = error.message; notice(error.message); }
  finally { state.analysisBusy = false; await refresh(true); }
};
for (const [id, key, step, loader] of [
  ["sessions-prev", "sessionsOffset", -50, loadSessions], ["sessions-next", "sessionsOffset", 50, loadSessions],
  ["queue-prev", "queueOffset", -50, loadQueue], ["queue-next", "queueOffset", 50, loadQueue],
]) $(id).onclick = async () => {
  state[key] = Math.max(0, state[key] + step);
  try { await loader(); } catch (error) { notice(error.message); }
};
function setMenuOpen(open) {
  const toggle = $("menu-toggle");
  const menu = $("site-menu");
  const wasOpen = toggle.getAttribute("aria-expanded") === "true";
  if (!open && wasOpen) toggle.focus();
  document.body.classList.toggle("menu-open", open);
  toggle.setAttribute("aria-expanded", String(open));
  toggle.setAttribute("aria-label", open ? "收起菜单" : "打开菜单");
  menu.inert = !open;
  menu.setAttribute("aria-hidden", String(!open));
  document.querySelector(".topbar").inert = open;
  document.querySelector("main").inert = open;
  if (open) (menu.querySelector('[aria-current="page"]') || $("nav-home")).focus();
}
$("menu-toggle").onclick = () => setMenuOpen($("menu-toggle").getAttribute("aria-expanded") !== "true");
$("menu-backdrop").onclick = () => setMenuOpen(false);
for (const id of ["nav-home", "nav-queue", "nav-settings", "nav-password"]) $(id).onclick = () => setMenuOpen(false);
$("logout").onclick = async () => {
  try { await api("/api/logout", { method: "POST" }); }
  finally { location.assign("/login"); }
};
$("password-form").onsubmit = async (event) => {
  event.preventDefault();
  const error = $("password-error");
  const noticeBox = $("password-notice");
  const current = $("password-current").value;
  const next = $("password-new").value;
  error.hidden = true;
  noticeBox.hidden = true;
  if (next.length < 12) {
    error.textContent = "新密码至少 12 位。";
    error.hidden = false;
    return;
  }
  if (next !== $("password-confirm").value) {
    error.textContent = "两次输入的新密码不一致。";
    error.hidden = false;
    return;
  }
  $("password-submit").disabled = true;
  try {
    await api("/api/password", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current, new: next }),
    });
    $("password-form").reset();
    noticeBox.textContent = "密码已更新，本设备保持登录。";
    noticeBox.hidden = false;
  } catch (failure) {
    error.textContent = failure.message;
    error.hidden = false;
  } finally { $("password-submit").disabled = false; }
};
document.addEventListener("keydown", (event) => {
  if ($("menu-toggle").getAttribute("aria-expanded") !== "true") return;
  if (event.key === "Escape") {
    event.preventDefault();
    setMenuOpen(false);
  } else if (event.key === "Tab") {
    const controls = [$("menu-toggle"), $("nav-home"), $("nav-queue"), $("nav-settings"), $("nav-password"), $("logout")];
    const next = (controls.indexOf(document.activeElement) + (event.shiftKey ? -1 : 1) + controls.length) % controls.length;
    event.preventDefault();
    controls[next].focus();
  }
});
window.addEventListener("hashchange", route);
document.addEventListener("visibilitychange", () => {
  updateModelPolling();
  if (!document.hidden && state.modelJob?.state === "running") pollModelJob().catch((error) => showModelError(error.message));
});
state.localReport = createReport("", "音频接收时间（北京时间）；人物编号在本场次内有效。");
$("local-report").append(state.localReport.root);
route();
loadModels().catch((error) => showModelError(error.message));
refresh(true);
setInterval(() => refresh(), 2000);
