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
        "models-switch",
        "models-token",
        "models-progress",
        "models-transcription",
        "models-diarization",
    ]:
        assert f'id="{element}"' in html


def test_index_places_model_setup_in_menu_view():
    html = (STATIC / "index.html").read_text()
    parser = _IdParentParser()
    parser.feed(html)
    assert parser.parents["nav-models"] == "site-menu"
    assert parser.parents["models-card"] == "models-view"
    assert "models-card" not in parser.descendants["home-view"]


def test_app_routes_model_settings_view():
    js = (STATIC / "app.js").read_text()
    assert "function routeViewState" in js
    start = js.index("function routeViewState")
    end = js.index("\nfunction route()", start)
    route_source = js[start:end]
    script = f"""
{route_source}
const hashes = ["#/home", "#/queue", "#/models", "", "#/unknown"];
const result = hashes.map((hash) => routeViewState(hash));
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    states = json.loads(completed.stdout)
    expected = [
        ("home", "home"),
        ("queue", "queue"),
        ("models", "models"),
        ("home", "home"),
        ("home", "home"),
    ]
    for state, (route, current) in zip(states, expected, strict=True):
        assert state["route"] == route
        assert [name for name, hidden in state["views"].items() if not hidden] == [route]
        assert [name for name, value in state["nav"].items() if value == "page"] == [current]


class _IdParentParser(HTMLParser):
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


def test_app_js_calls_model_endpoints():
    js = (STATIC / "app.js").read_text()
    for endpoint in [
        "/api/models",
        "/api/models/token",
        "/api/models/download",
        "/api/models/job",
    ]:
        assert endpoint in js
