# Model Settings Menu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move model configuration and downloads into a dedicated `#/models` view accessible from the main menu.

**Architecture:** Reuse the current hash navigation and menu drawer. Move the existing model card into its own route container; preserve its controls, event handlers, model polling, and APIs.

**Tech Stack:** Existing HTML, CSS, vanilla JavaScript, and pytest static-content tests.

**Spec:** `docs/superpowers/specs/2026-11-21-model-settings-menu-design.md`

## Global Constraints

- Do not change model download behavior, Token handling, API endpoints, or recognition defaults.
- Do not add dependencies, modal behavior, or server-side routes.
- Keep home, queue, and model configuration navigation accessible and functional.

## Review Focus

- Unknown or empty hash fragments must continue to fall back to home; test route fallback in the static route logic.
- Direct `#/models` navigation and browser hash navigation must reveal only the model view; test route/view bindings.
- Menu focus on the active item must not throw when a route has no matching item; test active-item fallback in code review and route behavior.
- Model controls must remain outside the home view but inside the models view; test static HTML structure.
- Existing model setup endpoints and controls must remain present; retain and run current static control tests.

---

### Task 1: Add the Model Settings Route

**Files:**
- Modify: `src/douyin_live_sense/static/index.html`
- Modify: `src/douyin_live_sense/static/app.js`
- Test: `tests/test_static.py`

**Interfaces:**
- Consumes: Existing `route()`, `setMenuOpen(open)`, `#home-view`, `#queue-view`, `#site-menu`, and model card handlers.
- Produces: `#/models`, `#models-view`, and `#nav-models`, integrated with existing routing and active menu state.

- [ ] **Step 1: Write static structure tests**

Add tests asserting `#nav-models` exists, `#models-view` contains `#models-card`, and `#home-view` does not contain `#models-card`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --locked --extra analysis pytest tests/test_static.py -q`
Expected: FAIL because the model navigation item and view container do not yet exist and the model card remains in the home view.

- [ ] **Step 3: Move the model card and extend routing**

Move `#models-card` into `#models-view`. Add a `#/models` route while retaining home as fallback for unknown hashes. Update visibility and `aria-current` for all three menu links. Make `setMenuOpen(true)` focus the current menu item, falling back to `#nav-home` if none is active. Ensure all menu navigation links close the drawer.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --locked --extra analysis pytest tests/test_static.py -q`
Expected: PASS, including existing model control and endpoint checks.

- [ ] **Step 5: Verify JavaScript and full test suite**

Run: `node --check src/douyin_live_sense/static/app.js && uv run --locked --extra analysis pytest -q`
Expected: JavaScript syntax check passes and the full test suite passes.

- [ ] **Step 6: Commit**

```bash
git add src/douyin_live_sense/static/index.html src/douyin_live_sense/static/app.js tests/test_static.py
git commit -m "feat: move model setup into menu view"
```
