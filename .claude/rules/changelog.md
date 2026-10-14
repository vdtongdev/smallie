# Changelog and versions

`CHANGELOG.md` and the `version` in `pyproject.toml` follow these rules. Update both when a release is deployed or when asked to update the changelog.

## Version numbers: `major.minor.patch`

- **Major** stays `0` until the user says otherwise.
- **Minor** goes up by one for each release that adds agents. Agents released together share one minor version:
  - `0.1.0`: docs agent and SQL agent
  - `0.2.0`: research agent and writer agent
  - `0.3.0`: orchestrator and chart agent
  - The next agent(s) get `0.4.0`.
- **Patch** is for bug fixes only, and restarts at `.1` after each new minor (`0.4.0` → `0.4.1`).
  - Group 2–3 bug fixes into one patch. Fixes that shipped together, or that are about the same problem, go in the same patch. Don't make a patch per fix.
  - A single urgent fix may be its own patch if nothing else is waiting.
- Features, refactors and tooling that aren't a new agent and aren't a bug fix (e.g. the project restructure, the benchmark script) don't get their own version. List them under the minor version they shipped with, or under the next one.

## Finding what goes in

1. Find the last released version in `CHANGELOG.md`, then read every commit since it: `git log --reverse --format="%h %s%n%b"` (merge commits only matter for their branch's commits).
2. Classify each commit: **new agent** (new minor), **bug fix** (patch), or **other** (goes under its minor). Read the commit body; one commit can hold several fixes (e.g. "also includes earlier pending work").
3. A bug fix is something that behaved wrongly for the user or the operator: wrong output, wrong language, wasted calls, something slow enough to be a problem, a crash. Docs-only commits (README, CLAUDE.md) aren't listed.

## Format

- Oldest version first. The file starts with `# Changelog` and one line explaining the version scheme.
- Minor headings name the agents: `## 0.4.0 - <Name> agent and <name> agent` (one agent: `## 0.4.0 - <Name> agent`).
- Patch headings are `## 0.4.1 - Fixes`, and each fix is one bold bullet named after what the user saw, not the code change:
  ```
  - **Fix: <symptom, e.g. English research questions answered in Vietnamese>**
    - <cause, in plain words>
    - <what changed; settings, files and functions in backticks>
  ```
- Features are bold bullets too (`- **Chart agent** (`app/agents/chart/`): ...`), with details as indented sub-bullets.
- Write for someone running or using the app: what changed in behaviour, which settings are new or renamed, and honest limits (e.g. "this lowers the chance, it doesn't remove it"). Include measured numbers when there are some (e.g. "48% shorter, about a third faster in testing").

## Keep in sync

- Set `version` in `pyproject.toml` to the newest changelog version, then run `uv lock` so `uv.lock` matches (the Docker build uses `uv sync --frozen`).
- Commit the changelog, `pyproject.toml` and `uv.lock` together, e.g. `Release 0.4.0: ...`.
