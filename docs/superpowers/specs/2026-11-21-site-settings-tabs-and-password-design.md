# 网站配置、首页 tab 与修改密码设计

## 意图

把“模型配置”从一个只放模型下载的页面，升级成真正管理站点行为的“网站配置”：识别方式三选一、录音切片时长可调；同时把首页两张互斥的采集卡片收进一个带左右 tab 的卡片，并在菜单里补上修改登录密码。

## 目标

- 菜单项 `模型配置` 改名 `网站配置`，路由 `#/settings`，旧 `#/models` 仍可访问。
- `网站配置` 页内：
  - `识别方式配置`：`仅录音` / `下载模型识别` / `飞书秒记` 三选一互斥；飞书秒记标注“即将支持”并禁用；模型 Token 与下载区保留在同一个卡片内。
  - `录音时间配置`：切片时长手动输入，单位可选秒或分钟，范围 1 秒 – 14400 秒（4 小时），默认 30 秒；从下一段切片开始生效。
- 识别方式与切片时长存服务端 `data/preferences.json`（v2），旧 v1 的 `recognition` 自动迁移。
- 任务进行中（守卫或本机录音在跑）时，识别方式不可更改：页面禁用整组选项，`PUT /api/preferences` 返回 409。切片时长不锁定。
- 首页把 `直播间守卫` 与 `本机录音` 合并成一张卡片，卡片内左右 tab 切换，一个 div 不再整体消失。
- 菜单新增 `修改登录密码`，独立页面 `#/password`。

## 非目标

- 不实现飞书秒记的真实功能；选择它返回“尚未支持”。
- 不做识别方式/时长修改后立刻断流重连；只在下一段切片开始时读取新时长。
- 不改变会话签名机制：改密码后其他设备不会被踢下线。
- 不改动分析管道、模型下载行为、队列与档案接口。

## 约束

- `preferences.json` 必须是唯一权威来源，跨浏览器与设备一致。
- 时长校验在前后端都要做：整数、1 ≤ 秒 ≤ 14400。
- 现有 `GET /api/preferences` 的 `recognition` 字段保留一版，值为 `mode == "models"`。
- 生产默认 30 秒；测试用 `Settings.segment_seconds` 的小数值做默认回退。
- 静态资源无构建链，`index.html` / `app.js` / `style.css` 直接改。

## 方案

1. **服务端偏好 v2（选定）**：`Preferences` 存 `{"version": 2, "mode": ..., "segment_seconds": ...}`，`mode` 三值 `record` / `models` / `feishu`。读数带兜底，写数原子替换。切片写入时通过 `Monitor.segment_seconds()` 取当前值。
2. 浏览器 `localStorage`：不用改后端，但换设备丢失且后端无法拒绝任务中修改，不选。
3. `DLS_SEGMENT_SECONDS` 环境变量：改完必须重启服务，和“页面里能调”冲突，仅作为默认值来源保留 `Settings.segment_seconds`。

## 设计

- `preferences.py`：`Preferences(path, default_segment_seconds)` 提供 `mode()`、`segment_seconds() -> float`、`enabled()`（= `mode == "models"`）、`snapshot()`、`update(mode=..., segment_seconds=...)`；`update` 校验取值，非法抛 `ValueError`。
- `app.py`：`GET /api/preferences` 返回 `mode`、`recognition`、`segment_seconds`、`locked`、`segment_min_seconds`、`segment_max_seconds`、`supported`、`reason`。`PUT` 接受 `mode` / `recognition`（兼容）/ `segment_seconds`；`feishu` → 409，切到 `models` 时沿用现有模型就绪校验，运行中改 mode → 409，时长越界 → 422。
- `audio.py`：`SegmentWriter` 增加可选 `segment_seconds`，`max_samples = max(1, round(seconds * sample_rate))`。
- `monitor.py`：`Monitor` 接受 `preferences`，`segment_seconds()` 返回偏好值或 `settings.segment_seconds`；建 `SegmentWriter` 时传入；`status()` 增加 `segment_seconds`。
- `index.html`：首页单卡片，`role="tablist"` 两个 tab 与两个 `role="tabpanel"` 面板；`#settings-view` 含识别方式卡与时长卡；`#password-view`。
- `app.js`：`routeViewState` 支持 `settings`（含 `#/models` 别名）与 `password`；`setHomeTab()` 只在模式切换时自动跳 tab；`renderSettings()` 管识别方式、模型区与时长；密码表单先本地校验再提交。
- `style.css`：`.mode-tabs`、`.mode-tab`、`.mode-options`、`.mode-option`、`.password-form` 等新样式，移动端沿用现有断点。

## 验收

- 守卫运行时切到本机录音 tab 可见但控件禁用并提示“请先停止直播间守卫”，反之亦然。
- 网站配置页三种识别方式互斥；运行中整组禁用；飞书秒记不可选。
- 时长改 5 秒后，下一段 WAV 约 5 秒，录音不中断；重启后仍为 5 秒。
- 修改密码页能改密码，改完当前会话仍有效。
- 全量 `pytest` 通过。
