---
paths:
  - "Dockerfile*"
  - "cloudbuild*.yaml"
  - ".gcloudignore"
  - ".dockerignore"
  - "DEPLOYING.md"
  - "pyproject.toml"
---

Deploy facts that `DEPLOYING.md` (the steps) does not state.

- **`Dockerfile`**: python:3.12-slim + uv, non-root `user`. The app dir is created and chowned before `USER`, otherwise `uv sync` can't create `.venv`. Listens on `$PORT`, no `--loop` flag (Linux).
- **Ignore files**: `.gcloudignore` and `.dockerignore` keep `.env`, `.venv`, `uploads`, tests, `cloudrun.env.yaml`, `cloudrun.agent*.yaml` and the local tooling folders (`.claude/`, `.codegraph/`, `.superpowers/`, `.antipatterns/`, `CLAUDE.local.md`) out of the image.
- **Background work**: `--no-cpu-throttling` is required because document ingestion, `save_turn` and the market scheduler run after the response is sent. The container disk is ephemeral, which is fine because uploads are only read during ingestion.
- **Research-only keys**: `TAVILY_API_KEY`, `MCP_SERVERS` and `EXTERNAL_DATABASE_URL` reach only the research server. In compose the other servers get them blanked.
- **Lock file**: after changing the version in `pyproject.toml`, run `uv lock` (the Docker build uses `uv sync --frozen`).
