const $ = (id) => document.getElementById(id);
const labels = {
  stopped: "已停止",
  checking: "检查直播中",
  waiting: "等待开播",
  recording: "正在采集",
  reconnecting: "正在重新连接",
  needs_attention: "需要处理",
};
const state = {
  selected: null,
  sessionsOffset: 0,
  segmentsOffset: 0,
  busy: false,
  connected: false,
  refreshRunning: false,
  tick: 0,
  actionError: null,
};
const timeFormat = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});
const dateFormat = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});
function clock(value) {
  return value ? timeFormat.format(new Date(value)) : "—";
}
function stamp(value) {
  return value ? `${dateFormat.format(new Date(value))} ${clock(value)}` : "—";
}
function duration(value) {
  const s = Math.floor(value || 0);
  return `${Math.floor(s / 3600)
    .toString()
    .padStart(2, "0")}:${Math.floor((s / 60) % 60)
    .toString()
    .padStart(2, "0")}:${(s % 60).toString().padStart(2, "0")}`;
}
function notice(message) {
  $("notice").textContent = message || "";
  $("notice").hidden = !message;
}
async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) {
    throw new Error(
      typeof body.detail === "string" ? body.detail : "请求失败，请检查输入。",
    );
  }
  return body;
}
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function renderMonitor(data) {
  $("state").textContent = labels[data.state] || data.state;
  $("state").className = `badge ${data.state}`;
  $("connection-dot").className = `connection-dot ${data.state}`;
  $("anchor").textContent =
    data.anchor_name || (data.enabled ? "正在获取直播间信息" : "准备开始监听");
  $("live-title").textContent =
    data.title ||
    (data.state === "waiting"
      ? "直播间尚未开播，守卫会持续等待。"
      : "输入直播间，开启后可关闭此网页；本机服务需要保持运行。");
  $("duration").textContent = duration(data.captured_seconds);
  $("segment-count").replaceChildren(
    document.createTextNode(`${data.segment_count} `),
    node("small", "段"),
  );
  $("last-audio").textContent = clock(data.last_audio_at);
  $("last-audio").title = stamp(data.last_audio_at);
  $("retry").textContent =
    data.retry_in_seconds !== null
      ? `${Math.ceil(data.retry_in_seconds)} 秒后检查`
      : "";
  $("save-path").textContent = data.data_dir;
  $("save-path").title = data.data_dir;
  $("start").disabled = state.busy || data.enabled;
  $("stop").disabled = state.busy || (!data.enabled && !data.session_id);
  $("room").disabled = data.enabled || state.busy;
  if (data.enabled) $("room").value = data.room_id;
  notice(data.last_error || state.actionError);
}
async function loadSessions() {
  const sessions = await api(
    `/api/sessions?limit=50&offset=${state.sessionsOffset}`,
  );
  const container = $("sessions");
  container.replaceChildren();
  if (!sessions.length) container.append(node("p", "暂无采集记录", "empty"));
  if (!state.selected && sessions.length) state.selected = sessions[0].id;
  for (const session of sessions) {
    const button = node(
      "button",
      undefined,
      `session${session.id === state.selected ? " selected" : ""}`,
    );
    button.type = "button";
    button.append(
      node("strong", session.anchor_name || `直播间 ${session.room_id}`),
      node("small", stamp(session.started_at)),
      node(
        "small",
        `${session.ended_at ? "已结束" : "场次未结束"} · ${duration(session.duration_seconds)} · ${session.segment_count} 段`,
      ),
    );
    button.onclick = async () => {
      state.selected = session.id;
      state.segmentsOffset = 0;
      try {
        await loadSessions();
        await loadSegments();
      } catch (error) {
        notice(error.message);
      }
    };
    container.append(button);
  }
  $("sessions-prev").disabled = state.sessionsOffset === 0;
  $("sessions-next").disabled = sessions.length < 50;
  $("sessions-page").textContent = String(state.sessionsOffset / 50 + 1);
  const selected = sessions.find((item) => item.id === state.selected);
  if (selected) {
    $("selected-heading").textContent =
      selected.title || `直播间 ${selected.room_id}`;
    $("selected-description").textContent =
      `${stamp(selected.started_at)} 开始`;
  }
}
async function loadSegments() {
  if (!state.selected) return;
  const session = state.selected;
  const [segments, events] = await Promise.all([
    api(
      `/api/sessions/${session}/segments?limit=100&offset=${state.segmentsOffset}`,
    ),
    api(`/api/sessions/${session}/events`),
  ]);
  if (session !== state.selected) return;
  const container = $("segments");
  container.replaceChildren();
  if (!segments.length) {
    const empty = node("div", undefined, "archive-empty");
    empty.append(
      node("span", "♫"),
      node("p", "还没有保存完成的音频"),
      node("small", "每 30 秒保存一段，停止时会保存剩余音频。"),
    );
    container.append(empty);
  }
  for (const segment of segments) {
    const row = node("div", undefined, "audio-row");
    const info = node("div");
    info.append(
      node("strong", `${clock(segment.start_at)} — ${clock(segment.end_at)}`),
      node(
        "small",
        `第 ${segment.sequence} 段 · ${(segment.sample_count / segment.sample_rate).toFixed(1)} 秒 · ${dateFormat.format(new Date(segment.start_at))}`,
      ),
    );
    const actions = node("div", undefined, "audio-actions");
    const play = node("button", "回听", "text-button");
    play.type = "button";
    play.onclick = () => {
      $("player-section").hidden = false;
      $("player-label").textContent =
        `第 ${segment.sequence} 段 · ${stamp(segment.start_at)}`;
      $("player").src = `/api/segments/${segment.id}/audio`;
      $("player")
        .play()
        .catch((error) => notice(`无法播放：${error.message}`));
    };
    const download = node("a", "下载");
    download.href = `/api/segments/${segment.id}/audio?download=true`;
    download.download = "";
    actions.append(play, download);
    row.append(info, actions);
    container.append(row);
  }
  $("gaps").replaceChildren();
  for (const event of events.filter((item) =>
    ["gap", "recovery_failed"].includes(item.kind),
  )) {
    $("gaps").append(
      node(
        "p",
        event.kind === "gap"
          ? `采集缺口：${stamp(event.started_at)} — ${event.ended_at ? stamp(event.ended_at) : "等待恢复"}`
          : event.detail,
      ),
    );
  }
  $("segments-prev").disabled = state.segmentsOffset === 0;
  $("segments-next").disabled = segments.length < 100;
  $("segments-page").textContent = String(state.segmentsOffset / 100 + 1);
}
async function refresh(force = false) {
  if (state.refreshRunning) return;
  state.refreshRunning = true;
  try {
    const data = await api("/api/monitor");
    state.connected = true;
    renderMonitor(data);
    if (force || state.tick++ % 5 === 0) await loadSessions();
    await loadSegments();
  } catch (error) {
    state.connected = false;
    $("state").textContent = "服务连接异常";
    $("state").className = "badge needs_attention";
    $("start").disabled = true;
    $("stop").disabled = true;
    notice(`无法连接本机服务：${error.message}。请确认服务仍在运行。`);
  } finally {
    state.refreshRunning = false;
  }
}
async function act(path, body) {
  state.actionError = null;
  state.busy = true;
  $("start").disabled = true;
  $("stop").disabled = true;
  try {
    const data = await api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    renderMonitor(data);
    if (data.session_id && !state.selected) state.selected = data.session_id;
    await loadSessions();
    await loadSegments();
  } catch (error) {
    state.actionError = error.message;
    notice(error.message);
  } finally {
    state.busy = false;
    const data = await api("/api/monitor").catch(() => null);
    if (data) {
      $("start").disabled = data.enabled;
      $("stop").disabled = !data.enabled && !data.session_id;
      $("room").disabled = data.enabled;
    }
  }
}
$("monitor-form").onsubmit = (event) => {
  event.preventDefault();
  act("/api/monitor/start", { room: $("room").value });
};
$("stop").onclick = () => act("/api/monitor/stop");
for (const [id, key, step] of [
  ["sessions-prev", "sessionsOffset", -50],
  ["sessions-next", "sessionsOffset", 50],
  ["segments-prev", "segmentsOffset", -100],
  ["segments-next", "segmentsOffset", 100],
]) {
  $(id).onclick = async () => {
    state[key] = Math.max(0, state[key] + step);
    try {
      if (key === "sessionsOffset") await loadSessions();
      else await loadSegments();
    } catch (error) {
      notice(error.message);
    }
  };
}
refresh(true);
setInterval(() => refresh(), 2000);
