# audio-ident-service

FastAPI backend for the audio-ident project.

## Stack

- **Python 3.12** with **uv** for package management
- **FastAPI** with Pydantic v2
- **SQLAlchemy 2.x** async ORM with asyncpg
- **Alembic** for database migrations
- **PyJWT** + **argon2-cffi** for auth
- **Ruff** for linting/formatting, **mypy** for type checking
- **pytest** + pytest-asyncio + httpx for testing

## Quick start

```bash
cd audio-ident-service
uv sync --all-extras          # install deps + dev extras
cp .env.example .env          # configure environment
uv run alembic upgrade head   # run migrations
uv run uvicorn app.main:app --port 17010 --reload
```

## Project layout

```
app/
  main.py          # FastAPI app factory, CORS, router mount
  settings.py      # pydantic-settings (reads .env)
  routers/         # endpoint modules (health, version)
  db/              # async engine + session dependency
  models/          # SQLAlchemy ORM models
  schemas/         # Pydantic request/response schemas
  auth/            # JWT, OAuth2, password hashing stubs
alembic/           # migration scripts
tests/             # pytest test suite
```

## Key commands

| Task | Command |
|------|---------|
| Run dev server | `uv run uvicorn app.main:app --port 17010 --reload` |
| Run tests | `uv run pytest` |
| Lint | `uv run ruff check app tests` |
| Format | `uv run ruff format app tests` |
| Type check | `uv run mypy app` |
| New migration | `uv run alembic revision --autogenerate -m "description"` |
| Apply migrations | `uv run alembic upgrade head` |

## Conventions

- All endpoints return JSON. Errors use `{"error": {"code": "...", "message": "...", "details": ...}}`.
- Router modules live in `app/routers/` and are registered in `app/main.py`.
- Config is loaded from environment via `app/settings.py` (pydantic-settings).
- Service port: **17010**. UI port (CORS): **17000**.
- Python version pinned to 3.12 in `.tool-versions` at repo root.
