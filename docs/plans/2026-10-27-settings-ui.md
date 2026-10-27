# Settings dialog UI fixes (0.5.2)

Goal: the Settings dialog is easier to use. Fields are spaced out, a **Save** button replaces **Close**, and the provider/model table from the Readme sits in the dialog.

Branch: `0.5.2/settings-ui`, created from `0.5.1/user-model-keys`. `main` has no Settings dialog yet, so this branch is stacked on that one.

## Task 1: Settings dialog layout, Save button, provider table
Agent: implementer

Files: `app/static/index.html` (`<dialog id="settingsDialog">`, Readme "Your model server" / "Máy chủ model của bạn" sections), `app/static/app.js` (model settings block, around `saveSettings`), `app/static/i18n.js` (en + vi), `app/static/style.css`.

1. Spacing: each label sits above its input. Each input is full width, using the `.login-card input` look (padding 8px 10px, radius 8px, border, `--bg` background). There are about 12px between field groups. The button row is flex, right-aligned, gap 8px, about 16px above it. Scope the rules to `#settingsDialog` (or a class on it) so other dialogs don't change.
2. Save button: `settingsClose` becomes `settingsSave`, labelled with a new i18n key `save` ("Save" / "Lưu"), class `primary`.
   - Clicking it runs the save (PUT `/model-settings`, as `saveSettings` does now).
   - It closes the dialog only when the save succeeds.
   - On a failed check it stays open and shows the error in `settingsStatus`.
   - If a field is missing (endpoint, model, or a key when none is saved), it stays open. The status then shows a new i18n message saying all three fields are needed (en + vi).
   - If nothing changed since the last load and settings are already saved, it just closes.
   - Remove the save-on-`change` listeners: Save is now the only way to save, so there is one connection check per save. Esc and the backdrop still close the dialog without saving. Clear is unchanged.
3. Provider table: under the inputs, above the buttons, add the same four-row table as the Readme (Provider, Key page, API endpoint, Model), in both languages through i18n.
   - Use small text and keep the table compact.
   - Wrap it in an `overflow-x: auto` container so the long DashScope URL doesn't widen the dialog. The dialog must stay usable at 360px width.
   - Add one line under it: "Any OpenAI-compatible service works." (en + vi).
4. Readme text: in both languages, replace "It saves when you leave a field, and "Saved" appears." / "Mọi thứ tự lưu khi bạn rời khỏi ô nhập, và chữ "Đã lưu" hiện ra." with a sentence saying to press **Save** / **Lưu**.
5. `ARCHITECTURE.md`: if it describes saving on blur, update that line.

Acceptance:
- `uv run pytest` passes, with no new failures compared with the branch base.
- `node --check app/static/app.js` and `node --check app/static/i18n.js` pass.
- Every new i18n key exists in both `en` and `vi`.
- `grep -n "settingsClose" app/static` finds nothing.
- `grep -n "addEventListener(\"change\", saveSettings)" app/static/app.js` finds nothing.
