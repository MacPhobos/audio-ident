# Phase 2 Infrastructure Scaffolding Audit

> **Date**: 2026-02-14
> **Purpose**: Comprehensive audit of existing scaffolding to inform Phase 2 implementation delegation
> **Status**: Complete

---

## Executive Summary

The audio-ident codebase has a **well-scaffolded foundation** for Phase 2 infrastructure work. The critical infrastructure pieces -- database engine, async sessions, Alembic migrations, Settings class with `database_url`, and Docker Compose with PostgreSQL -- are **all already in place**. There is **no lifespan handler** yet in `main.py`, the `app/models/__init__.py` has only a bare `Base` class with no models, and the `alembic/versions/` directory is **empty** (no migration files yet). The `audio/`, `search/`, and `ingest/` directories referenced in CLAUDE.md do **not yet exist** as code -- they are planned layout only.

---

## 1. docker-compose.yml

**File**: `/docker-compose.yml` (root)

**Services**: PostgreSQL only (1 service)
- Image: `postgres:16`
- Port: `${POSTGRES_PORT:-5432}:5432`
- Credentials: `audio_ident` / `audio_ident` / `audio_ident`
- Healthcheck: `pg_isready -U audio_ident` (5s interval, 3s timeout, 5 retries)

**Volumes**: `pgdata` (named volume for PostgreSQL data persistence)

**What is MISSING**:
- No Qdrant service (mentioned in CLAUDE.md as needed)
- No profiles defined (CLAUDE.md mentions profiles but they are not implemented)
- No `docker-compose` version field (uses implicit latest format)
- No service container for the FastAPI app itself

**Key observation**: The Makefile target `db-up` calls `docker compose up -d postgres` which works because the service name `postgres` exists. But the CLAUDE.md mentions profiles (`--profile postgres --profile qdrant`) which do **not exist** in the current compose file.

---

## 2. Makefile

**File**: `/Makefile` (root)

**Defined targets** (11 total):
| Target | Description | Status |
|--------|-------------|--------|
| `help` | Show help text | Working |
| `install` | `uv sync --all-extras` + `pnpm install` | Working |
| `dev` | Start postgres + run alembic + start uvicorn + start UI | Working |
| `test` | `uv run pytest` + `pnpm test` | Working |
| `lint` | `ruff check` + `pnpm lint` | Working |
| `fmt` | `ruff format` + `pnpm format` | Working |
| `typecheck` | `pyright` + `svelte-check` | Working |
| `gen-client` | `pnpm gen:api` | Working |
| `gen-client-from-file` | Generate from static OpenAPI spec | Working |
| `db-up` | `docker compose up -d postgres` | Working |
| `db-down` | `docker compose down` | Working |
| `db-reset` | Drop + recreate DB + run migrations | Working |

**Variables**:
- `SERVICE_DIR := audio-ident-service`
- `UI_DIR := audio-ident-ui`
- `SERVICE_PORT ?= 17010`
- `UI_PORT ?= 17000`

**What is MISSING**:
- No `docker-up` target (CLAUDE.md references `make docker-up` but Makefile has `db-up`)
- No `docker-down` target separate from `db-down`
- No `ingest` target
- No `rebuild-index` target
- No Qdrant service management

**Key observation**: The `dev` target already runs `uv run alembic upgrade head` before starting uvicorn. This means migration execution is wired in.

---

## 3. audio-ident-service/app/settings.py

**File**: `/audio-ident-service/app/settings.py`

**Settings class**: `Settings(BaseSettings)` with `pydantic_settings`

**Defined settings**:
| Setting | Type | Default |
|---------|------|---------|
| `service_port` | `int` | `17010` |
| `service_host` | `str` | `"0.0.0.0"` |
| `cors_origins` | `str` | `"http://localhost:17000"` |
| `database_url` | `str` | `"postgresql+asyncpg://audio_ident:audio_ident@localhost:5432/audio_ident"` |
| `jwt_secret_key` | `str` | `"change-me-in-production"` |
| `jwt_algorithm` | `str` | `"HS256"` |
| `jwt_access_token_expire_minutes` | `int` | `30` |
| `app_name` | `str` | `"audio-ident-service"` |
| `app_version` | `str` | `"0.1.0"` |

**Properties**:
- `cors_origin_list` -> splits comma-separated `cors_origins` into `list[str]`

**Configuration**:
- Reads from `.env` file
- `extra="ignore"` (ignores unknown env vars)

**Singleton**: `settings = Settings()` at module level

**What is MISSING**:
- No `qdrant_url` setting (mentioned in CLAUDE.md env vars table)
- No `qdrant_collection_name` setting
- No `olaf_lmdb_path` setting
- No `audio_storage_root` setting
- No `embedding_model` / `embedding_dim` settings

---

## 4. audio-ident-service/app/main.py

**File**: `/audio-ident-service/app/main.py`

**Structure**: `create_app()` factory pattern returning `FastAPI` instance

**Current setup**:
- FastAPI with `title`, `version`, `docs_url`, `openapi_url`
- CORSMiddleware configured from `settings.cors_origin_list`
- Two routers mounted: `health.router` (no prefix), `version.router` (prefix `/api/v1`)
- Global exception handler returning standardized error JSON

**What is MISSING**:
- **No lifespan handler** -- no `@asynccontextmanager` lifespan function
- No startup health checks (PostgreSQL, Qdrant connectivity)
- No CLAP model loading at startup
- No `app.state` usage for shared resources
- Imports only `health` and `version` routers

**Key observation**: The `create_app()` factory is clean and ready for a lifespan handler to be added. The pattern is compatible with the standard `FastAPI(lifespan=lifespan)` approach.

---

## 5. audio-ident-service/app/models/__init__.py

**File**: `/audio-ident-service/app/models/__init__.py`

**Content** (complete):
```python
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass
```

**What exists**: Bare `Base` class with no columns, no mixins, no models

**What is MISSING**:
- No `Track` model
- No timestamp mixin
- No UUID primary key pattern
- No model imports

**Key observation**: The `Base` is already imported in `alembic/env.py` via `from app.models import Base` and `target_metadata = Base.metadata`. Any model that imports from and extends this `Base` will be automatically discovered by Alembic's `--autogenerate`.

---

## 6. audio-ident-service/app/schemas/

**Files present**:
| File | Content |
|------|---------|
| `__init__.py` | Empty |
| `health.py` | `HealthResponse(BaseModel)` with `status: str`, `version: str` |
| `version.py` | `VersionResponse(BaseModel)` with `name`, `version`, `git_sha`, `build_time` (all `str`) |
| `errors.py` | `ErrorDetail(BaseModel)` with `code`, `message`, `details` + `ErrorResponse(BaseModel)` wrapping it |

**What is MISSING**:
- No pagination schemas (`PaginatedResponse`)
- No track schemas
- No search/ingest schemas
- No shared base schemas

**Key observation**: The `ErrorResponse` schema matches the API contract's error shape exactly. The pattern is consistent and ready for extension.

---

## 7. audio-ident-service/app/db/

**Files present**:
| File | Content |
|------|---------|
| `__init__.py` | Empty |
| `engine.py` | Creates `AsyncEngine` via `create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)` |
| `session.py` | Creates `async_sessionmaker` and provides `get_db()` async generator dependency |

**What exists**:
- Async engine with connection pooling and pre-ping
- Session factory with `expire_on_commit=False`
- FastAPI dependency `get_db()` yielding `AsyncSession`

**What is MISSING**:
- Engine is created at module import time (not inside lifespan) -- this means the engine is created when any module imports `app.db.engine`
- No engine disposal on shutdown
- No connection pool size configuration

**Key observation**: The `get_db()` dependency is already the standard pattern for injecting database sessions into route handlers. Routes can immediately use `Depends(get_db)`.

---

## 8. audio-ident-service/pyproject.toml

**Core dependencies** (already installed):
| Package | Version Range | Purpose |
|---------|--------------|---------|
| `fastapi` | `>=0.115,<1` | Web framework |
| `uvicorn[standard]` | `>=0.34,<1` | ASGI server |
| `pydantic` | `>=2,<3` | Data validation |
| `pydantic-settings` | `>=2,<3` | Settings management |
| `sqlalchemy[asyncio]` | `>=2,<3` | ORM (async) |
| `asyncpg` | `>=0.30,<1` | PostgreSQL async driver |
| `alembic` | `>=1.14,<2` | Database migrations |
| `PyJWT` | `>=2,<3` | JWT tokens |
| `argon2-cffi` | `>=23,<25` | Password hashing |
| `python-multipart` | `>=0.0.18,<1` | Form data parsing |

**Dev dependencies** (`[project.optional-dependencies] dev`):
| Package | Version Range | Purpose |
|---------|--------------|---------|
| `pytest` | `>=8,<9` | Testing |
| `pytest-asyncio` | `>=0.25,<1` | Async test support |
| `httpx` | `>=0.28,<1` | HTTP client for testing |
| `ruff` | `>=0.9,<1` | Linting/formatting |
| `mypy` | `>=1.14,<2` | Type checking |
| `aiosqlite` | `>=0.20,<1` | SQLite async for testing |

**What is MISSING**:
- No `qdrant-client` dependency
- No `laion-clap` dependency
- No `numpy` / `librosa` / audio processing dependencies
- No `python-magic` dependency
- No `pyacoustid` / `chromaprint` dependency
- No `pyright` in dev dependencies (Makefile uses it via `uv run pyright`)

**Key observation**: All database infrastructure dependencies (SQLAlchemy async, asyncpg, Alembic) are fully installed. `aiosqlite` in dev deps suggests the test strategy includes SQLite-based testing.

---

## 9. API Contract

**File**: `/audio-ident-service/docs/api-contract.md`

**Version**: 1.0.0 (FROZEN)

**Defined endpoints**:
1. `GET /health` -- Health check (no prefix)
2. `GET /api/v1/version` -- Version metadata

**Defined types**:
- `PaginatedResponse<T>` (pagination wrapper)
- `ErrorResponse` (error shape)

**Authentication**: Stubbed only (OAuth2 + JWT structure mentioned, not enforced)

**Copies exist in all 3 locations**:
- `/audio-ident-service/docs/api-contract.md` -- YES (source of truth)
- `/docs/api-contract.md` -- YES (identical)
- `/audio-ident-ui/docs/api-contract.md` -- YES (identical, minor whitespace differences)

---

## 10. .env.example

**Root level**: `/Users/mac/workspace/audio-ident/.env.example` -- **DOES NOT EXIST**

**Service level**: `/Users/mac/workspace/audio-ident/audio-ident-service/.env.example` -- **EXISTS**

**Content**:
```
SERVICE_PORT=17010
SERVICE_HOST=0.0.0.0
CORS_ORIGINS=http://localhost:17000
DATABASE_URL=postgresql+asyncpg://audio_ident:audio_ident@localhost:5432/audio_ident
JWT_SECRET_KEY=change-me-in-production
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
APP_NAME=audio-ident-service
APP_VERSION=0.1.0
```

**What is MISSING**:
- No Qdrant settings
- No audio storage settings
- No Olaf LMDB settings
- No CLAP/embedding settings

---

## 11. audio-ident-service/app/routers/

**Files present**:
| File | Content |
|------|---------|
| `__init__.py` | Empty |
| `health.py` | `GET /health` returning `HealthResponse` |
| `version.py` | `GET /api/v1/version` returning `VersionResponse` with git SHA |

**What is MISSING**:
- No search router
- No ingest router
- No tracks router
- No auth router

---

## 12. Alembic Setup

**alembic.ini**: Fully configured
- `script_location = alembic`
- `sqlalchemy.url` set (overridden at runtime by `env.py`)
- Standard logging configuration

**alembic/env.py**: Fully configured for async
- Imports `Base` from `app.models`
- Overrides URL from `settings.database_url`
- Sets `target_metadata = Base.metadata`
- Uses `async_engine_from_config` with `NullPool`
- Supports both offline and online modes

**alembic/versions/**: **EMPTY** -- No migration files yet

**Key observation**: Alembic is fully wired up and ready. As soon as a model is added to `app/models/` and imported, running `uv run alembic revision --autogenerate -m "description"` will detect the new table and generate a migration.

---

## 13. Auth Stubs

**Files present** (all marked as stubs):
| File | Content |
|------|---------|
| `__init__.py` | Empty |
| `jwt.py` | `create_access_token()` and `decode_access_token()` using PyJWT + settings |
| `oauth2.py` | `oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)` |
| `password.py` | `hash_password()` and `verify_password()` using argon2 |

**Status**: All are functional implementations but not wired into any routes. They are ready to use when auth endpoints are added.

---

## 14. Test Infrastructure

**Files present**:
| File | Content |
|------|---------|
| `__init__.py` | Empty |
| `conftest.py` | `client` fixture using `httpx.ASGITransport` with `AsyncClient` |
| `test_health.py` | 3 tests: health endpoint, version endpoint, openapi schema |

**Key observation**: The test client fixture does NOT use a test database -- it uses the real `app` directly. For database-dependent tests, this will need to be updated to either use an in-memory SQLite database (aiosqlite is in dev deps) or a separate test PostgreSQL database.

---

## 15. Directories That Do NOT Exist Yet

The following directories are referenced in CLAUDE.md project layout but **have no code files**:
- `app/audio/` -- Does not exist
- `app/search/` -- Does not exist
- `app/ingest/` -- Does not exist

These are Phase 2+ implementation targets.

---

## Summary: What Exists vs. What Needs Building

### READY (No Changes Needed)
- SQLAlchemy Base class in `app/models/__init__.py`
- Async database engine in `app/db/engine.py`
- Async session factory + `get_db()` dependency in `app/db/session.py`
- Alembic fully configured with async support in `alembic/env.py`
- Settings class with `database_url` in `app/settings.py`
- `create_app()` factory pattern in `app/main.py`
- Error schemas matching API contract
- Auth stubs (JWT, OAuth2, password hashing)
- PostgreSQL in Docker Compose with healthcheck
- API contract v1.0.0 synced across all 3 locations
- Test infrastructure with httpx AsyncClient
- All database dependencies installed (sqlalchemy, asyncpg, alembic, aiosqlite)

### NEEDS BUILDING (Phase 2 Targets)
1. **docker-compose.yml**: Add Qdrant service + profiles
2. **Makefile**: Add `docker-up`, `docker-down`, `ingest`, `rebuild-index` targets
3. **settings.py**: Add Qdrant, Olaf, audio storage, CLAP settings
4. **main.py**: Add lifespan handler with startup health checks
5. **models/**: Add Track model (and any other domain models)
6. **schemas/**: Add pagination, track, search, ingest schemas
7. **routers/**: Add search, ingest, tracks routers
8. **alembic/versions/**: Generate first migration after models are defined
9. **.env.example**: Add new settings (Qdrant, audio, etc.)
10. **API contract**: Bump to v1.1.0 when new endpoints are defined
11. **app/audio/**: Create audio processing modules
12. **app/search/**: Create search service modules
13. **app/ingest/**: Create ingestion pipeline modules

### IMPORTANT CONSTRAINTS FOR DELEGATION
- The `dev` target already runs `alembic upgrade head` -- any new migration will auto-apply on `make dev`
- The engine is created at module import time, not in a lifespan -- if a lifespan handler is added, consider whether to move engine creation there
- `get_db()` is already the standard dependency injection pattern -- new routes should use `Depends(get_db)`
- The API contract must be updated BEFORE implementing new endpoints (per CLAUDE.md conventions)
- Contract must be synced to all 3 locations after update
- Alembic autogenerate requires models to be imported in `alembic/env.py` (currently only imports `Base` from `app.models`)
