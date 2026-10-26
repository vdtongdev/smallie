---
paths:
  - "app/core/auth.py"
  - "app/core/accounts.py"
  - "app/api/routes_admin.py"
  - "scripts/claim_anonymous_data.py"
  - "app/core/db.py"
  - "app/features/base.py"
---

Accounts, admin and per-user data isolation.

**Accounts and user isolation:** `app/core/auth.py::get_current_user` is the *only* place identity is decided: the signed session cookie holds the `user_id` of the logged-in account (401 if missing or the account was deleted). People create accounts (name, username, password) via `POST /api/signup`, capped at `MAX_ACCOUNTS` (default 20, admin not counted, enforced under a Postgres advisory lock). Account rows live on the `users` table and are handled in `app/core/accounts.py` (scrypt hashes; passwords never leave `accounts.py`/`auth.py`). The `admin` account keeps the old shared user id `"team"` (so it owns the pre-accounts data), logs in with `APP_PASSWORD` (no stored hash; `SESSION_SECRET` is then required) and can list, reset and delete accounts (`app/api/routes_admin.py`; deletion removes checkpoints and store items, then calls each feature's `delete_user_data`, e.g. chunks and `uploads/<user_id>/`; documents, conversations and the graph go with the `users` row via ON DELETE CASCADE). `scripts.claim_anonymous_data` moves data of old `anon-*` users to the admin. The account's name and username reach the agents as `AgentContext.user` (`USER_SECTION`). Everything else — DB rows, vector search filter `{"user_id": {"$eq": ...}}`, memory namespace, upload dir — is keyed by `user_id`. Preserve this scoping in any new query or tool.
