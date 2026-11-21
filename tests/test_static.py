import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path

STATIC = Path(__file__).parents[1] / "src" / "douyin_live_sense" / "static"


def test_login_page_has_required_controls():
    html = (STATIC / "login.html").read_text()
    for element in [
        'id="login-form"',
        'id="login-password"',
        'id="login-submit"',
        'id="login-error"',
    ]:
        assert element in html
    assert "/api/login" in (STATIC / "login.js").read_text()


def test_index_has_logout_control():
    assert 'id="logout"' in (STATIC / "index.html").read_text()


def test_index_has_model_setup_controls():
    html = (STATIC / "index.html").read_text()
    for element in [
        "models-card",
        "models-token",
        "models-progress",
        "models-transcription",
        "models-diarization",
    ]:
        assert f'id="{element}"' in html


def test_settings_view_hosts_recognition_modes_and_duration():
    html = (STATIC / "index.html").read_text()
    parser = _IdParentParser()
    parser.feed(html)
    for element in (
        "mode-record",
        "mode-models",
        "mode-feishu",
        "segment-seconds",
        "segment-unit",
        "segment-save",
        "segment-current",
    ):
        assert f'id="{element}"' in html
    assert parser.parents["models-card"] == "settings-view"
    assert parser.parents["segment-seconds"] == "duration-card"
    assert parser.parents["duration-card"] == "settings-view"
    assert "models-switch" not in html
    assert "网站配置" in html


def test_index_places_model_setup_in_menu_view():
    html = (STATIC / "index.html").read_text()
    parser = _IdParentParser()
    parser.feed(html)
    assert parser.parents["nav-settings"] == "site-menu"
    assert parser.parents["models-card"] == "settings-view"
    assert "models-card" not in parser.descendants["home-view"]


def test_password_view_and_menu_item_exist():
    html = (STATIC / "index.html").read_text()
    parser = _IdParentParser()
    parser.feed(html)
    for element in (
        "nav-password", "password-form", "password-current", "password-new",
        "password-confirm", "password-submit", "password-error",
    ):
        assert f'id="{element}"' in html
    assert parser.parents["nav-password"] == "site-menu"
    assert parser.parents["password-form"] == "password-view"
    js = (STATIC / "app.js").read_text()
    assert '"/api/password"' in js
    assert "#/password" in js


def test_home_merges_capture_modes_into_tabs():
    html = (STATIC / "index.html").read_text()
    parser = _IdParentParser()
    parser.feed(html)
    for element in ("tab-douyin", "tab-local", "panel-douyin", "panel-local"):
        assert f'id="{element}"' in html
    assert parser.parents["panel-douyin"] == parser.parents["panel-local"]
    assert "local-heading" not in parser.parents
    js = (STATIC / "app.js").read_text()
    assert "function setHomeTab" in js
    assert "aria-selected" in js


def test_app_routes_settings_and_legacy_models_hash():
    js = (STATIC / "app.js").read_text()
    assert "function routeViewState" in js
    start = js.index("function routeViewState")
    end = js.index("\nfunction route()", start)
    route_source = js[start:end]
    script = f"""
{route_source}
const hashes = ["#/home", "#/queue", "#/settings", "#/models", "#/password", "", "#/unknown"];
const result = hashes.map((hash) => routeViewState(hash));
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    states = json.loads(completed.stdout)
    expected = [
        ("home", "home"),
        ("queue", "queue"),
        ("settings", "settings"),
        ("settings", "settings"),
        ("password", "password"),
        ("home", "home"),
        ("home", "home"),
    ]
    for state, (route, current) in zip(states, expected, strict=True):
        assert state["route"] == route
        assert [name for name, hidden in state["views"].items() if not hidden] == [route]
        assert [name for name, value in state["nav"].items() if value == "page"] == [current]
        assert "password" in state["views"]


class _IdParentParser(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.parents = {}
        self.descendants = {}

    def handle_starttag(self, tag, attrs):
        element_id = dict(attrs).get("id")
        parent_id = next((item[1] for item in reversed(self.stack) if item[1]), None)
        if element_id:
            self.parents[element_id] = parent_id
            self.descendants[element_id] = set()
            for _, ancestor_id in self.stack:
                if ancestor_id:
                    self.descendants[ancestor_id].add(element_id)
        if tag not in self.VOID:
            self.stack.append((tag, element_id))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return


def test_audio_time_labels_say_they_are_a_moment():
    html = (STATIC / "index.html").read_text()
    assert html.count("最近音频到达时刻") == 2
    assert "最近收到音频" not in html


def test_audio_time_fields_render_beijing_suffix():
    js = (STATIC / "app.js").read_text()
    assert 'node("small", "北京时间")' in js
    for element in ["last-audio", "local-last"]:
        assert f'audioTime("{element}"' in js
        assert f'$("{element}").textContent' not in js


def test_audio_time_helper_only_suffixes_real_times():
    js = (STATIC / "app.js").read_text()
    start = js.index("function audioTime")
    end = js.index("\n}\n", start) + 3
    helper = js[start:end]
    script = f"""
const registry = {{}};
function $(id) {{
  return (registry[id] ||= {{
    children: [],
    replaceChildren(value) {{ this.children = [value]; }},
    append(value) {{ this.children.push(value); }},
  }});
}}
function node(tag, text) {{ return {{ tag, text }}; }}
const document = {{ createTextNode: (text) => text }};
function clock(value) {{ return value ? "11:51:54" : "—"; }}
function stamp(value) {{ return value ? "2026/11/21 11:51:54" : "—"; }}
{helper}
audioTime("with-time", "2026-11-21T03:51:54+00:00");
audioTime("without-time", null);
const shape = (id) => registry[id].children;
const result = {{ withTime: shape("with-time"), withoutTime: shape("without-time") }};
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    rendered = json.loads(completed.stdout)
    assert rendered["withTime"] == ["11:51:54", {"tag": "small", "text": "北京时间"}]
    assert rendered["withoutTime"] == ["—"]


BADGE_HARNESS_HEAD = """
const registry = {};
function $(id) {
  return (registry[id] ||= {
    textContent: "", className: "", hidden: false, disabled: false, checked: false,
    value: "", title: "", children: [],
    replaceChildren(...items) { this.children = items; },
    insertBefore() {}, remove() {},
    setAttribute(name, value) { this[name] = value; },
  });
}
function node(tag, text) { return { tag, text }; }
function duration() { return "00:00:00"; }
function audioTime() {}
function notice() {}
const document = { createTextNode: (text) => text };
const state = {
  monitor: null, monitorEnabled: false, lastMode: undefined, homeTab: "douyin",
  busy: false, analysisBusy: false, actionError: null,
};
"""

BADGE_HARNESS_TAIL = """
const base = {
  enabled: false, mode: null, state: "stopped", segment_seconds: 30, segment_count: 0,
  captured_seconds: 0, retry_in_seconds: null, data_dir: "/data", room_id: "123",
  anchor_name: "", title: "", last_error: null, last_audio_at: null,
};
const badge = () => $("state").textContent;
const seen = {};
renderMonitor({ ...base });
seen.idle = badge();
renderMonitor({ ...base, enabled: true, mode: "douyin", state: "waiting" });
seen.waiting = badge();
renderMonitor({ ...base, enabled: true, mode: "douyin", state: "recording" });
seen.recording = badge();
renderMonitor({ ...base, enabled: true, mode: "douyin", state: "reconnecting" });
seen.reconnecting = badge();
renderMonitor({ ...base });
seen.stoppedAgain = badge();
renderMonitor({ ...base, enabled: true, mode: "local", state: "recording" });
seen.local = badge();
process.stdout.write(JSON.stringify(seen));
"""


CONTROLS_HARNESS_HEAD = """
const registry = {};
function $(id) {
  return (registry[id] ||= {
    textContent: "", className: "", hidden: false, disabled: false, checked: false,
    value: "", title: "", children: [],
    replaceChildren(...items) { this.children = items; },
    insertBefore() {}, remove() {},
    setAttribute(name, value) { this[name] = value; },
  });
}
const state = {
  recognition: true, busy: false, analysisBusy: false, monitorEnabled: false,
  localBusy: false, localSocket: null, monitor: { session_id: null }, sessions: new Map(),
};
"""


def test_realtime_toggle_is_available_before_starting_the_guard(tmp_path):
    """识别方式已选“纯本地模型识别”且模型就绪时，未开守卫也应能先勾选实时生成。"""
    js = (STATIC / "app.js").read_text()
    sync_src = js[js.index("function syncAnalysisControls"):js.index("function updateModelPolling")]
    script = f"""
{CONTROLS_HARNESS_HEAD}
function updateClip() {{}}
{sync_src}
const seen = {{}};
syncAnalysisControls();
seen.readyIdle = !$("realtime").disabled;
state.recognition = false;
syncAnalysisControls();
seen.recordOnly = $("realtime").disabled;
state.recognition = true;
state.busy = true;
syncAnalysisControls();
seen.busy = $("realtime").disabled;
state.busy = false;
state.localSocket = {{ readyState: 1 }};
syncAnalysisControls();
seen.socketOpen = $("realtime").disabled;
process.stdout.write(JSON.stringify(seen));
"""
    script_path = tmp_path / "controls-harness.cjs"
    script_path.write_text(script)
    completed = subprocess.run(
        ["node", str(script_path)], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "readyIdle": True,
        "recordOnly": True,
        "busy": True,
        "socketOpen": True,
    }


def test_analysis_bar_hosts_switch_and_fill_button():
    html = (STATIC / "index.html").read_text()
    parser = _IdParentParser()
    parser.feed(html)
    for element in ("realtime", "realtime-hint", "analyse-current", "analysis-progress"):
        assert f'id="{element}"' in html
    # 两个采集面板共用同一组分析控件，不再各自放置。
    assert "local-realtime" not in html
    assert parser.parents["analyse-current"] == "analysis-bar"
    assert "analysis-bar" in parser.descendants
    assert "realtime" in parser.descendants["analysis-bar"]
    assert "analysis-summary" not in html


def test_header_shows_analysis_status_instead_of_local_label():
    html = (STATIC / "index.html").read_text()
    parser = _IdParentParser()
    parser.feed(html)
    assert parser.parents["analysis-pill"] == "analysis-status"
    assert parser.parents["analysis-global"] == "analysis-pill"
    assert parser.parents["model-hint"] == "analysis-status"
    assert "local-label" not in html
    assert "本机运行 · 音频留在本地" not in html


def test_realtime_label_explains_pre_start_selection():
    html = (STATIC / "index.html").read_text()
    hint = html[html.index('id="realtime-hint"'):]
    hint = hint[:hint.index("</small>")]
    assert "普通话记录" in hint
    assert "补分析" in hint


STATUS_HARNESS_HEAD = """
const registry = {};
function $(id) {
  return (registry[id] ||= {
    textContent: "", className: "", hidden: false, disabled: false,
    children: [], title: "",
    replaceChildren(...items) { this.children = items; },
    setAttribute(name, value) { this[name] = value; },
  });
}
const state = { recognition: true, monitor: null, visibleSessions: new Set(), sessions: new Map() };
"""


def test_header_status_reflects_analysis_state(tmp_path):
    js = (STATIC / "app.js").read_text()
    src = js[js.index('const ANALYSIS_DETAIL'):js.index("function setHomeTab")]
    script = f"""
{STATUS_HARNESS_HEAD}
function stamp() {{ return "—"; }}
{src}
const seen = {{}};
const status = (recognition, models, worker, lastError = "") => {{
  state.recognition = recognition;
  const detail = {{ ready: models, reason: models ? "" : "未准备模型" }};
  renderAnalysisStatus({{ models: detail, worker_state: worker, last_error: lastError }});
  return {{
    text: $("analysis-global").textContent,
    kind: $("analysis-pill").className,
    hint: $("model-hint").textContent,
  }};
}};
seen.ready = status(true, true, "idle");
seen.processing = status(true, true, "processing");
seen.notReady = status(true, false, "idle");
seen.recordOnly = status(false, false, "idle");
seen.workerError = status(true, true, "idle", "分析进程崩溃");
process.stdout.write(JSON.stringify(seen));
"""
    script_path = tmp_path / "status-harness.cjs"
    script_path.write_text(script)
    completed = subprocess.run(
        ["node", str(script_path)], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    seen = json.loads(completed.stdout)
    assert seen["ready"]["text"] == "模型已就绪"
    assert seen["ready"]["kind"] == "analysis-pill ready"
    assert "Community-1" in seen["ready"]["hint"]
    assert seen["processing"]["text"] == "模型正在分析音频"
    assert seen["processing"]["kind"] == "analysis-pill processing"
    assert seen["notReady"]["text"] == "模型未就绪"
    assert seen["notReady"]["kind"] == "analysis-pill error"
    assert seen["notReady"]["hint"] == "未准备模型"
    assert seen["recordOnly"]["text"] == "仅录音模式"
    assert seen["recordOnly"]["kind"] == "analysis-pill record"
    assert "Community-1" in seen["recordOnly"]["hint"]
    assert seen["workerError"]["text"] == "分析出错"
    assert seen["workerError"]["hint"] == "分析进程崩溃"


def test_fill_button_targets_latest_session_after_stopping(tmp_path):
    """停止守卫后 monitor.session_id 为空，补分析按钮仍应指向最近的场次。"""
    js = (STATIC / "app.js").read_text()
    src = js[js.index("// 采集进行中就用当前场次"):js.index("function updateModelPolling")]
    script = f"""
{STATUS_HARNESS_HEAD}
function stamp(value) {{ return value; }}
{src}
const old = {{
  id: "old",
  data: {{ id: "old", started_at: "2026-11-20T10:00:00", segment_count: 3 }},
}};
const latest = {{
  id: "new",
  data: {{ id: "new", started_at: "2026-11-21T10:00:00", segment_count: 9 }},
}};
state.sessions = new Map([["old", old], ["new", latest]]);
state.visibleSessions = new Set(["old", "new"]);
const seen = {{}};
seen.live = analysisTarget();
state.monitor = {{ session_id: "live-id" }};
seen.liveSession = analysisTarget();
state.monitor = {{ session_id: null }};
renderAnalysisBar();
seen.stoppedText = $("analysis-progress").textContent;
state.visibleSessions = new Set();
seen.emptyTarget = analysisTarget();
renderAnalysisBar();
seen.emptyText = $("analysis-progress").textContent;
process.stdout.write(JSON.stringify(seen));
"""
    script_path = tmp_path / "target-harness.cjs"
    script_path.write_text(script)
    completed = subprocess.run(
        ["node", str(script_path)], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    seen = json.loads(completed.stdout)
    assert seen["live"] == "new"
    assert seen["liveSession"] == "live-id"
    assert seen["stoppedText"] == "最近场次 2026-11-21T10:00:00 · 9 段"
    assert seen["emptyTarget"] is None
    assert seen["emptyText"] == "暂无场次"


def test_models_mode_is_called_local_model_recognition():
    sources = {
        "index.html": STATIC / "index.html",
        "app.js": STATIC / "app.js",
        "app.py": STATIC.parent / "app.py",
        "README.md": STATIC.parents[2] / "README.md",
    }
    for name, path in sources.items():
        text = path.read_text()
        assert "下载模型识别" not in text, name
    for name in ("index.html", "app.js", "README.md"):
        assert "纯本地模型识别" in sources[name].read_text(), name


def test_capture_badge_follows_monitor_state(tmp_path):
    js = (STATIC / "app.js").read_text()
    labels_src = js[js.index("const labels ="):js.index("const analysisLabels")]
    render_src = js[js.index("function secondsLabel"):js.index("// Stable nodes preserve")]
    script_path = tmp_path / "badge-harness.cjs"
    script_path.write_text(BADGE_HARNESS_HEAD + labels_src + "\n" + render_src + BADGE_HARNESS_TAIL)
    completed = subprocess.run(
        ["node", str(script_path)], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "idle": "已停止",
        "waiting": "等待开播",
        "recording": "正在采集",
        "reconnecting": "正在重新连接",
        "stoppedAgain": "已停止",
        "local": "正在采集",
    }


def test_capture_badge_breathes_only_while_recording_and_respects_reduced_motion():
    css = (STATIC / "style.css").read_text()
    recording_rule = css.split(".badge.recording {", 1)[1].split("}", 1)[0]
    assert "animation: recording-breathe 2.6s ease-in-out infinite;" in recording_rule
    assert "@keyframes recording-breathe" in css
    reduced_motion = css.split("@media (prefers-reduced-motion: reduce) {", 1)[1].split("}", 1)[0]
    assert ".badge.recording" in reduced_motion
    assert "animation: none;" in reduced_motion


def test_segment_seconds_helper_rounds_minutes_to_whole_seconds():
    js = (STATIC / "app.js").read_text()
    start = js.index("function segmentSecondsFrom")
    end = js.index("\n}\n", start) + 3
    helper = js[start:end]
    script = f"""
{helper}
const result = [4.1, 8.2, 8.3, 0.5, 240].map((value) => segmentSecondsFrom(value, "minutes"));
const raw = [5, 14400].map((value) => segmentSecondsFrom(value, "seconds"));
process.stdout.write(JSON.stringify({{ minutes: result, seconds: raw }}));
"""
    completed = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "minutes": [246, 492, 498, 30, 14400],
        "seconds": [5, 14400],
    }


def test_no_stale_recognize_card_copy():
    paths = {
        "app.py": STATIC.parent / "app.py",
        "app.js": STATIC / "app.js",
        "index.html": STATIC / "index.html",
    }
    for name, path in paths.items():
        text = path.read_text()
        assert "完整识别" not in text, name
        assert "语音识别卡片" not in text, name



def test_app_js_calls_model_endpoints():
    js = (STATIC / "app.js").read_text()
    for endpoint in [
        "/api/models",
        "/api/models/token",
        "/api/models/download",
        "/api/models/job",
    ]:
        assert endpoint in js


def test_delete_session_uses_inline_confirmation():
    js = (STATIC / "app.js").read_text()
    assert "window.confirm" not in js
    assert "function promptRemove" in js
    assert "function cancelRemove" in js
    assert "function discardSession" in js
    assert "removedSessions" in js
    assert "确认删除" in js
    assert "/api/sessions/${view.id}`, { method: \"DELETE\" }" in js
