# Model Settings Menu Design

## Intent

Move model configuration and downloads out of the main home view into a dedicated view reachable from the existing menu. This keeps the monitoring and recording workspace focused while preserving explicit model setup controls.

## Goals

- Add a `模型配置` menu item and a dedicated hash-routed view.
- Move the existing speech-recognition/model card into that view without changing its controls or API behavior.
- Keep the home view's local analysis status summary and existing home/queue navigation.
- Preserve menu accessibility state, active-item indication, and close-on-navigation behavior.

## Non-goals

- No changes to model download behavior, token handling, API endpoints, or recognition defaults.
- No modal, new dependency, or server-side route.

## Approaches

1. Dedicated hash-routed view (selected): reuses the existing `#/home` and `#/queue` routing and menu patterns; gives model status, credentials, and download progress sufficient space.
2. Modal: fast to open but constrains the existing detailed setup workflow and adds modal focus/close behavior.

## Design

Add `#/models` and `#nav-models`. Move the existing `#models-card` under a `#models-view` route container. Extend `route()` and active navigation handling for home, queue, and models. Keep `loadModels()`, polling, and event handlers intact so downloads continue to update in the dedicated view and persist as before. Adjust menu focus handling to choose the active item with a fallback, including on routes without a menu item if necessary. Add or update static route tests to cover the new view and menu item.

## Acceptance

- The home page no longer renders the model setup card; it remains available from the menu's `模型配置` item.
- Direct navigation to `#/models`, browser back/forward, and menu active state work.
- Existing token, download progress, and recognition controls remain functional.
- Static tests and the full test suite pass.
