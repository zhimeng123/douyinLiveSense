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
