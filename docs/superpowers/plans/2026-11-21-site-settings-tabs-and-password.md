# 网站配置、首页 tab 与修改密码 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把“模型配置”升级为“网站配置”（识别方式三选一 + 录音切片时长可调），首页守卫与本机录音合并为 tab 卡片，菜单新增修改登录密码。

**Architecture:** 服务端 `Preferences` 升到 v2 作为唯一权威（`mode` + `segment_seconds`），API 暴露并校验；采集端每段切片开始时读取时长。前端为无构建链的原生 HTML/CSS/JS，新增 hash 路由 `#/settings`、`#/password`。

**Tech Stack:** Python 3.11 / FastAPI / pytest / 原生 JS。

**Spec:** `docs/superpowers/specs/2026-11-21-site-settings-tabs-and-password-design.md`

## Global Constraints

- 切片时长：整数，1 ≤ 秒 ≤ 14400，默认 30；生产默认来自 `Settings.segment_seconds`。
- 识别方式：`record` / `models` / `feishu`，互斥；`feishu` 返回 409“飞书秒记尚未支持。”
- 任务进行中（`monitor.enabled`）修改 `mode` 返回 409；修改 `segment_seconds` 允许。
- `GET /api/preferences` 保留 `recognition` 字段，值等于 `mode == "models"`。
- 测试命令：`.venv/bin/python -m pytest -q`；静态检查：`.venv/bin/ruff check .`
- 中文文案，不加新依赖，不引入前端构建。

## Review Focus

- 时长输入为 0、负数、小数、14401、空值时：应被拦截并显示提示，不得写入偏好文件。
- 守卫运行中切换识别方式：必须 409，且页面选项保持原选中值，不出现界面与服务器不一致。
- 切换首页 tab 时守卫/录音仍在跑：不得触发停止、不得丢失正在显示的分析记录。
- `#/models` 旧书签：应落到网站配置页而不是空白页。
- 旧 `preferences.json`（v1）存在时：升级后识别方式必须与原先 `recognition` 一致。

---

### Task 1: Preferences v2（mode + segment_seconds）

**Files:**
- Modify: `src/douyin_live_sense/preferences.py`
- Test: `tests/test_preferences.py`

**Interfaces:**
- Produces: `Preferences(path: Path, default_segment_seconds: float = 30)`, `.mode() -> str`, `.segment_seconds() -> float`, `.enabled() -> bool`, `.snapshot() -> dict`, `.update(*, mode: str | None = None, segment_seconds: int | None = None) -> dict`；常量 `MODES = ("record", "models", "feishu")`、`MIN_SEGMENT_SECONDS = 1`、`MAX_SEGMENT_SECONDS = 14_400`。

- [ ] **Step 1: 写失败测试**（`tests/test_preferences.py` 顶部三例替换为下面四个）

```python
def test_defaults_to_record_and_default_seconds(tmp_path):
    prefs = Preferences(tmp_path / "preferences.json", default_segment_seconds=30)
    assert prefs.mode() == "record"
    assert prefs.segment_seconds() == 30
    assert prefs.enabled() is False


def test_legacy_v1_recognition_true_migrates_to_models(tmp_path):
    path = tmp_path / "preferences.json"
    path.write_text(json.dumps({"version": 1, "recognition": True}))
    assert Preferences(path).mode() == "models"
    assert Preferences(path).enabled() is True


def test_update_persists_mode_and_seconds(tmp_path):
    prefs = Preferences(tmp_path / "preferences.json")
    prefs.update(mode="models", segment_seconds=600)
    reloaded = Preferences(tmp_path / "preferences.json")
    assert reloaded.mode() == "models"
    assert reloaded.segment_seconds() == 600
    assert reloaded.snapshot() == {"mode": "models", "segment_seconds": 600}


@pytest.mark.parametrize("seconds", [0, -1, 14_401])
def test_update_rejects_seconds_out_of_range(tmp_path, seconds):
    with pytest.raises(ValueError):
        Preferences(tmp_path / "preferences.json").update(segment_seconds=seconds)


def test_update_rejects_unknown_mode(tmp_path):
    with pytest.raises(ValueError):
        Preferences(tmp_path / "preferences.json").update(mode="auto")
```

- [ ] **Step 2: 运行测试确认失败**
  Run: `.venv/bin/python -m pytest tests/test_preferences.py -q`
  Expected: FAIL（`AttributeError: ... 'mode'` / 旧 API `set_enabled` 不存在）

- [ ] **Step 3: 实现 Preferences v2**
  `_read()` 读 JSON，非 dict → `{}`；`version` 缺失或 < 2 时返回 `{"mode": "models" if recognition is True else "record"}`。`mode()` 只认 `MODES`。`segment_seconds()`：值必须是 `int`（排除 `bool`）且在范围内，否则返回 `self.default_segment_seconds`。`update()` 先校验再写 `{"version": 2, "mode": ..., "segment_seconds": ...}`（tmp + `os.replace`）。

- [ ] **Step 4: 运行测试确认通过**
  Run: `.venv/bin/python -m pytest tests/test_preferences.py -q`
  Expected: PASS

- [ ] **Step 5: 提交**
```bash
git add src/douyin_live_sense/preferences.py tests/test_preferences.py
git commit -m "feat: 偏好升级为识别方式与切片时长"
```

---

### Task 2: 偏好 API（读取、切换识别方式、修改时长）

**Files:**
- Modify: `src/douyin_live_sense/app.py`（`PreferencesRequest`、`GET/PUT /api/preferences`）
- Test: `tests/test_preferences.py`、`tests/test_model_api.py`

**Interfaces:**
- Consumes: Task 1 的 `Preferences.snapshot()/update()/mode()/segment_seconds()/enabled()`。
- Produces: `GET /api/preferences` →
  `{"mode", "recognition", "segment_seconds", "locked", "segment_min_seconds", "segment_max_seconds", "supported", "reason"}`；`PUT` 接受 `{"mode"?, "recognition"?, "segment_seconds"?}`，响应同上。

- [ ] **Step 1: 写失败测试**

```python
def test_preferences_api_reports_mode_and_duration(build_app):
    client = TestClient(build_app(), base_url="http://127.0.0.1")
    with client:
        login(client)
        body = client.get("/api/preferences").json()
        assert body["mode"] == "record"
        assert body["recognition"] is False
        assert body["segment_seconds"] == 0.002  # 测试 settings 的默认值
        assert body["locked"] is False
        assert body["segment_max_seconds"] == 14_400


def test_preferences_api_sets_duration(build_app):
    client = TestClient(build_app(), base_url="http://127.0.0.1")
    with client:
        login(client)
        response = client.put("/api/preferences", json={"segment_seconds": 5})
        assert response.status_code == 200
        assert response.json()["segment_seconds"] == 5


@pytest.mark.parametrize("seconds", [0, 14_401])
def test_preferences_api_rejects_bad_duration(build_app, seconds):
    client = TestClient(build_app(), base_url="http://127.0.0.1")
    with client:
        login(client)
        assert client.put("/api/preferences", json={"segment_seconds": seconds}).status_code == 422


def test_feishu_mode_is_not_available_yet(build_app):
    client = TestClient(build_app(), base_url="http://127.0.0.1")
    with client:
        login(client)
        response = client.put("/api/preferences", json={"mode": "feishu"})
        assert response.status_code == 409
        assert "飞书" in response.text


def test_mode_cannot_change_while_task_running(build_app, monkeypatch):
    monkeypatch.setattr(
        "douyin_live_sense.app.model_status",
        lambda settings: {"ready": True, "reason": "", "components": {}},
    )
    client = TestClient(build_app(), base_url="http://127.0.0.1")
    with client:
        login(client)
        client.app.state.monitor.enabled = True
        response = client.put("/api/preferences", json={"mode": "models"})
        assert response.status_code == 409
        assert client.put("/api/preferences", json={"segment_seconds": 8}).status_code == 200
```

并把 `tests/test_model_api.py::test_preferences_enable_after_models_ready` 的最后两行改成：

```python
        assert client.put("/api/preferences", json={"mode": "models"}).status_code == 200
        assert client.get("/api/preferences").json()["mode"] == "models"
```

- [ ] **Step 2: 运行测试确认失败**
  Run: `.venv/bin/python -m pytest tests/test_preferences.py tests/test_model_api.py -q`
  Expected: FAIL（缺少 `mode` / `segment_seconds` 字段，`feishu` 未被拒绝）

- [ ] **Step 3: 实现 API**
  `PreferencesRequest` 三个可选字段（默认 `None`）。抽出 `def preferences_payload(app) -> dict` 供 GET/PUT 复用；`locked=bool(app.state.monitor.enabled)`。PUT 里：`recognition` 先映射成 mode；`mode` 非法值 → 422，`feishu` → 409；mode 实际变化且 `monitor.enabled` → 409“任务进行中，无法更改识别方式。请先停止直播间守卫或本机录音。”；切到 `models` 时沿用原有 `analysis_supported()` 与 `model_status()` 校验；`segment_seconds` 用 `MIN/MAX_SEGMENT_SECONDS` 校验 → 422。全部校验通过后一次 `update()`，返回 payload。
  `create_app` 里把偏好构造改成 `Preferences(settings.data_dir / "preferences.json", settings.segment_seconds)`。（`Monitor` 接线放在 Task 3。）

- [ ] **Step 4: 运行测试确认通过**
  Run: `.venv/bin/python -m pytest tests/test_preferences.py tests/test_model_api.py -q`
  Expected: PASS

- [ ] **Step 5: 提交**
```bash
git add src/douyin_live_sense/app.py tests/test_preferences.py tests/test_model_api.py
git commit -m "feat: 偏好接口支持识别方式与切片时长"
```

---

### Task 3: 切片时长接入采集

**Files:**
- Modify: `src/douyin_live_sense/audio.py`（`SegmentWriter`）、`src/douyin_live_sense/monitor.py`（`Monitor`）
- Test: `tests/test_audio_storage.py`、`tests/test_monitor.py`

**Interfaces:**
- Consumes: `Preferences.segment_seconds()`。
- Produces: `SegmentWriter(store, settings, session_id, segment_seconds: float | None = None)`；`Monitor(..., preferences=None)`、`Monitor.segment_seconds() -> float`；`monitor.status()["segment_seconds"]`。

- [ ] **Step 1: 写失败测试**

```python
def test_writer_honours_explicit_segment_seconds(settings):
    store = Storage(settings.data_dir)
    session = store.create_session("123", "live-1", "主播", "标题")
    writer = SegmentWriter(store, settings, session["id"], segment_seconds=0.004)
    writer.feed(b"\x01\x02" * 160, utc_now())
    assert store.totals(session["id"])[0] == 2
    writer.close()
    assert [s["sample_count"] for s in store.segments(session["id"])] == [64, 64, 32]
    store.close()


def test_monitor_uses_segment_seconds_from_preferences(settings):
    prefs = Preferences(settings.data_dir / "preferences.json")
    prefs.update(segment_seconds=7)
    store = Storage(settings.data_dir)
    monitor = Monitor(store, settings, FakeSource(), FakeCapture(), preferences=prefs)
    assert monitor.segment_seconds() == 7
    assert monitor.status()["segment_seconds"] == 7
    store.close()
```

- [ ] **Step 2: 运行测试确认失败**
  Run: `.venv/bin/python -m pytest tests/test_audio_storage.py tests/test_monitor.py -q`
  Expected: FAIL（`unexpected keyword argument 'segment_seconds'` / `preferences`）

- [ ] **Step 3: 实现**
  `SegmentWriter.__init__` 增加 `segment_seconds`，`self.max_samples = max(1, round(seconds * settings.sample_rate))`。`Monitor.__init__` 增加 `preferences=None`，`segment_seconds()` 返回 `float(self.preferences.segment_seconds())` 或 `settings.segment_seconds`；`start_local()` 与 `_run()` 建 writer 时传 `segment_seconds=self.segment_seconds()`；`status()` 加字段。`create_app` 的 `Monitor(store, settings)` 改为 `Monitor(store, settings, preferences=preferences)`（已存在同名形参，注意不要和 `create_app` 的参数重名冲突）。

- [ ] **Step 4: 运行测试确认通过**
  Run: `.venv/bin/python -m pytest tests/test_audio_storage.py tests/test_monitor.py -q`
  Expected: PASS

- [ ] **Step 5: 提交**
```bash
git add src/douyin_live_sense/audio.py src/douyin_live_sense/monitor.py src/douyin_live_sense/app.py tests/test_audio_storage.py tests/test_monitor.py
git commit -m "feat: 每段切片按当前配置决定时长"
```

---

### Task 4: 首页守卫与本机录音合并为 tab 卡片

**Files:**
- Modify: `src/douyin_live_sense/static/index.html`、`static/app.js`、`static/style.css`
- Test: `tests/test_static.py`

**Interfaces:**
- Produces: `#tab-douyin` / `#tab-local`（`role="tab"`）、`#panel-douyin` / `#panel-local`（`role="tabpanel"`）；`app.js` 的 `setHomeTab(tab: "douyin" | "local")`；`state.homeTab`、`state.lastMode`。

- [ ] **Step 1: 写失败测试**

```python
def test_home_merges_capture_modes_into_tabs():
    html = (STATIC / "index.html").read_text()
    parser = _IdParentParser()
    parser.feed(html)
    for element in ("tab-douyin", "tab-local", "panel-douyin", "panel-local"):
        assert f'id="{element}"' in html
    assert parser.parents["panel-douyin"] == parser.parents["panel-local"]
    assert "local-heading" not in parser.parents  # 不再有独立卡片标题
    js = (STATIC / "app.js").read_text()
    assert "function setHomeTab" in js
    assert 'aria-selected' in js
```

- [ ] **Step 2: 运行测试确认失败**
  Run: `.venv/bin/python -m pytest tests/test_static.py -q`
  Expected: FAIL（找不到 `tab-douyin`）

- [ ] **Step 3: 改 HTML/CSS/JS**
  两张 `section.monitor-card` 合成一张：`card-heading` 内放 `.mode-tabs`（两个 tab 按钮）与共享的 `#state` 徽标；两个面板承载原内容，本机面板默认 `hidden`。`app.js`：`state.homeTab = "douyin"`、`state.lastMode = undefined`；`setHomeTab()` 切换 `hidden`、`aria-selected`、`tabindex`；`renderMonitor()` 在 `data.mode` 发生变化时自动切 tab（本机 → local，守卫启用 → douyin）；徽标按当前 tab 与 `monitor.mode` 计算（本机未跑时显示“未录音”）；`updateLocal()` 不再写 `local-state`。CSS 加 `.mode-tabs`、`.mode-tab[aria-selected="true"]`、`.card-heading` 内 tab 的移动端折行。

- [ ] **Step 4: 运行测试确认通过**
  Run: `.venv/bin/python -m pytest tests/test_static.py -q`
  Expected: PASS

- [ ] **Step 5: 提交**
```bash
git add src/douyin_live_sense/static/index.html src/douyin_live_sense/static/app.js src/douyin_live_sense/static/style.css tests/test_static.py
git commit -m "feat: 首页合并为守卫与本机录音 tab 卡片"
```

---

### Task 5: 网站配置页（改名、识别方式三选一、录音时长）

**Files:**
- Modify: `src/douyin_live_sense/static/index.html`、`static/app.js`、`static/style.css`
- Test: `tests/test_static.py`

**Interfaces:**
- Consumes: Task 2 的偏好 API 字段。
- Produces: `#nav-settings`（文案“网站配置”）、`#settings-view`、`#mode-record` / `#mode-models` / `#mode-feishu`、`#models-state`、`#segment-seconds`、`#segment-unit`、`#segment-save`、`#segment-current`、`#segment-error`；`app.js` 的 `renderSettings()`、`requestMode(mode)`、`saveSegmentSeconds()`；`routeViewState` 返回 `route: "settings"`（`#/models` 别名）。

- [ ] **Step 1: 写失败测试**

```python
def test_settings_view_hosts_recognition_modes_and_duration():
    html = (STATIC / "index.html").read_text()
    parser = _IdParentParser()
    parser.feed(html)
    for element in (
        "mode-record", "mode-models", "mode-feishu",
        "segment-seconds", "segment-unit", "segment-save", "segment-current",
    ):
        assert f'id="{element}"' in html
    assert parser.parents["models-card"] == "settings-view"
    assert parser.parents["segment-seconds"] == "settings-view"
    assert "models-switch" not in html
    assert "网站配置" in html


def test_route_recognises_settings_and_legacy_models_hash():
    js = (STATIC / "app.js").read_text()
    start = js.index("function routeViewState")
    end = js.index("\nfunction route()", start)
    script = f"""
{js[start:end]}
const hashes = ["#/home", "#/queue", "#/settings", "#/models", "#/password", ""];
process.stdout.write(JSON.stringify(hashes.map((hash) => routeViewState(hash).route)));
"""
    completed = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == [
        "home", "queue", "settings", "settings", "password", "home",
    ]
```

- [ ] **Step 2: 运行测试确认失败**
  Run: `.venv/bin/python -m pytest tests/test_static.py -q`
  Expected: FAIL（`mode-record` 不存在，菜单文案仍是“模型配置”）

- [ ] **Step 3: 改 HTML/CSS/JS**
  菜单项文案与 id 改为 `nav-settings`；`#models-view` 换为 `#settings-view`，卡片标题“识别方式配置”，把原“完整识别”复选框换成 `role="radiogroup"` 的三个 `.mode-option`；新增时长卡。`app.js`：`state.mode`、`state.segmentSeconds`、`state.settingsLocked`；`loadModels()` 一并读偏好；`renderSettings()` 渲染单选、禁用状态（`settingsLocked`、`unsupported`、`feishu` 恒禁用）、时长输入与“当前 X 秒”；`mode-models` 选中时复用原“缺模型则下载后启用”流程；`saveSegmentSeconds()` 做整数与范围校验后 PUT；`routeViewState`/`route()` 支持 settings；切换 `segment-unit` 时换算输入值；首页脚注按 `monitor.segment_seconds` 显示“按当前配置保存 N 秒 WAV”。

- [ ] **Step 4: 运行测试确认通过**
  Run: `.venv/bin/python -m pytest tests/test_static.py -q`
  Expected: PASS

- [ ] **Step 5: 提交**
```bash
git add src/douyin_live_sense/static tests/test_static.py
git commit -m "feat: 网站配置页支持识别方式与切片时长"
```

---

### Task 6: 菜单修改登录密码页

**Files:**
- Modify: `src/douyin_live_sense/static/index.html`、`static/app.js`、`static/style.css`
- Test: `tests/test_static.py`

**Interfaces:**
- Consumes: `POST /api/password {current, new}`（已存在，新密码 ≥ 12 位）。
- Produces: `#nav-password`、`#password-view`、`#password-form`、`#password-current`、`#password-new`、`#password-confirm`、`#password-submit`、`#password-error`、`#password-notice`；`route: "password"`。

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 运行测试确认失败**
  Run: `.venv/bin/python -m pytest tests/test_static.py -q`
  Expected: FAIL（`nav-password` 不存在）

- [ ] **Step 3: 改 HTML/CSS/JS**
  菜单加 `修改登录密码`；新增 `#password-view` 表单（三个 password 输入 + 提交）。`app.js`：`#password-form` 的 `onsubmit` 先校验两次新密码一致且 ≥ 12 位，再 `POST /api/password`；成功清空表单并显示“密码已更新，本设备保持登录”，失败显示在 `#password-error`；菜单 Tab 焦点循环加入 `nav-settings`、`nav-password`。

- [ ] **Step 4: 运行测试确认通过**
  Run: `.venv/bin/python -m pytest tests/test_static.py -q`
  Expected: PASS

- [ ] **Step 5: 提交**
```bash
git add src/douyin_live_sense/static tests/test_static.py
git commit -m "feat: 菜单新增修改登录密码"
```

---

### Task 7: 文档与全量验证

**Files:**
- Modify: `README.md`、`.env.example`

- [ ] **Step 1: 更新文档**
  README 的“完整识别”段落改为“网站配置 → 识别方式配置”，补充三选一说明与“录音时间配置”（1–14400 秒，下一段生效，默认 30 秒）；`.env.example` 不新增变量，仅把“语音识别卡片”改称“网站配置”。

- [ ] **Step 2: 全量验证**
  Run: `.venv/bin/python -m pytest -q; .venv/bin/ruff check .`
  Expected: 全部 PASS，ruff 无输出

- [ ] **Step 3: 提交**
```bash
git add README.md .env.example
git commit -m "docs: 说明网站配置与切片时长"
```
