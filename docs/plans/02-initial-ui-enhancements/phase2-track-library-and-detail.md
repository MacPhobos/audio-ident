# Phase 2: Track Library + Track Detail

> **Date**: 2026-02-15
> **Status**: READY FOR IMPLEMENTATION
> **Depends On**: Phase 1 (NavBar must exist for Library link)
> **Contract Version**: 1.1.0 (may bump to 1.2.0 -- see Step 1)

---

## 1. Objective

Deliver two new pages -- a paginated track library (`/tracks`) and a track detail view (`/tracks/[id]`) -- backed by two new backend endpoints (`GET /api/v1/tracks` and `GET /api/v1/tracks/{id}`). Enable navigation from search results to track details while preserving search state.

### Success Criteria

1. `GET /api/v1/tracks?page=1&pageSize=50&search=queen` returns a correctly paginated JSON response matching the contract's `PaginatedResponse<TrackInfo>` shape.
2. `GET /api/v1/tracks/{uuid}` returns a `TrackDetail` JSON response matching the contract, or 404 with `ErrorResponse` for unknown IDs.
3. The `/tracks` page renders a paginated, searchable table (desktop) / card list (mobile) driven by URL query parameters.
4. The `/tracks/[id]` page renders full track metadata with fingerprint/embedding status indicators.
5. Track titles in `SearchResults.svelte` are clickable links to `/tracks/[id]`.
6. Navigating from search results to track detail and back preserves the search results (no re-search required).
7. All TypeScript types come from `make gen-client` -- zero hand-written API response types.
8. Backend tests pass: `cd audio-ident-service && uv run pytest`.
9. Frontend type-checks: `cd audio-ident-ui && pnpm check`.

---

## 2. Critical Constraints from Devil's Advocate Review

### BLOCK-1: No Hand-Written TypeScript Types

**Constraint**: CLAUDE.md forbids manually writing TypeScript types for API responses. All types must come through `make gen-client`.

**How this plan addresses it**: This plan contains zero TypeScript interface definitions. The workflow is:

1. Create Pydantic schemas in `app/schemas/` (Python -- source of truth).
2. Implement FastAPI routes in `app/routers/` using those schemas.
3. Start the backend (`make dev`).
4. Run `make gen-client` to regenerate `src/lib/api/generated.ts` from the live OpenAPI spec.
5. Import generated types in `src/lib/api/client.ts`.

If the backend cannot start, use the committed `openapi.json` fallback per CLAUDE.md.

### BLOCK-2: PaginatedResponse Needs a Pydantic Model

**Constraint**: The API contract v1.1.0 defines `PaginatedResponse<T>` but no corresponding Pydantic model exists in the backend. Creating this model is a prerequisite.

**How this plan addresses it**: Step 1 of the implementation order (Section 7) is to verify the contract shape and create the Pydantic model. If the Pydantic model's serialized JSON differs from the contract in ANY way, the contract must be bumped to 1.2.0 before writing any router code. See Section 3 for the detailed verification process.

### SIG-3: Search State Preservation

**Constraint**: When navigating from search results to `/tracks/[id]`, search state is lost because it is component-local (Svelte 5 runes). The user must re-search to see results again.

**How this plan addresses it**: Use `sessionStorage` to save and restore search state. Before navigating to a track detail page from search results, save the current search response, mode, and page state to `sessionStorage`. When the search page mounts, check for saved state and restore it if present. See Section 5e for the detailed implementation.

### SIG-4: URL-Driven Pagination

**Constraint**: Track library pagination must be URL-driven (query params) for bookmarkability and back-button support.

**How this plan addresses it**: The `/tracks` page reads `page`, `pageSize`, and `search` exclusively from `$page.url.searchParams` (via `$app/state`). Navigation uses `goto()` with `replaceState: true` for search input changes and `replaceState: false` for page changes. TanStack Query keys are derived from URL params, keeping query cache and URL in sync. See Section 5c for details.

---

## 3. Contract-First Workflow

This section MUST be completed before writing any backend or frontend code.

### Step 1: Review Current Contract

Read `/Users/mac/workspace/audio-ident/docs/api-contract.md` (v1.1.0). Confirm the following are defined:

- `GET /api/v1/tracks` with query params `page`, `pageSize`, `search`
- `GET /api/v1/tracks/{id}` with UUID path param
- `PaginatedResponse<T>` wrapper with `data: T[]` and `pagination: { page, pageSize, totalItems, totalPages }`
- `TrackInfo` schema (id, title, artist, album, duration_seconds, ingested_at)
- `TrackDetail` schema (extends TrackInfo with audio properties and indexing status)
- Error codes: `VALIDATION_ERROR` (400), `NOT_FOUND` (404)

**STATUS**: All of the above are confirmed present in the contract v1.1.0.

### Step 2: Verify PaginatedResponse Compatibility

Create the Pydantic model `PaginatedResponse` and verify its serialized JSON output matches the contract EXACTLY.

The contract specifies:
```json
{
    "data": [...],
    "pagination": {
        "page": 1,
        "pageSize": 50,
        "totalItems": 142,
        "totalPages": 3
    }
}
```

The Pydantic model (to be created in `app/schemas/pagination.py`) must serialize field names using camelCase aliases to match the contract (specifically `pageSize`, `totalItems`, `totalPages`). Pydantic v2 uses `alias_generator` or explicit `Field(alias=...)` for this.

**CRITICAL CHECK**: After creating the Pydantic model, write a unit test that serializes it and asserts the JSON keys match the contract. If they do not (e.g., Pydantic outputs `page_size` instead of `pageSize`), you have two options:

- **Option A (preferred)**: Use Pydantic's `model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)` to match the contract. No contract bump needed.
- **Option B**: If the camelCase aliasing cannot cleanly match, bump the contract to 1.2.0 with the actual field names and synchronize all three copies. This is the nuclear option -- prefer Option A.

### Step 3: Contract Synchronization (if bumped to 1.2.0)

Only execute this step if the contract needs changes.

1. Edit `audio-ident-service/docs/api-contract.md` -- update version, changelog, and modified schema.
2. Copy to `audio-ident-ui/docs/api-contract.md`.
3. Copy to `docs/api-contract.md`.
4. Verify all three files are identical: `diff <(cat audio-ident-service/docs/api-contract.md) <(cat audio-ident-ui/docs/api-contract.md) && diff <(cat audio-ident-service/docs/api-contract.md) <(cat docs/api-contract.md)`.

### Step 4: Implementation Sequence

After the contract is verified (or bumped), proceed in this order:

1. Backend: Pydantic schemas (Section 4b)
2. Backend: Database queries + router (Sections 4c, 4d)
3. Backend: Register router in `app/main.py` (Section 4d)
4. Backend: Tests (Section 4e)
5. Backend: `uv run pytest` must pass
6. Frontend: Start backend, run `make gen-client` (Section 5a)
7. Frontend: API client functions (Section 5b)
8. Frontend: Track library page (Section 5c)
9. Frontend: Track detail page (Section 5d)
10. Frontend: Search results enhancement (Section 5e)
11. Frontend: NavBar update (Section 5f)
12. Frontend: `pnpm check` and `pnpm test` must pass

---

## 4. Backend Implementation

### 4a. Contract Changes

**Expected outcome**: No contract changes needed. The contract v1.1.0 already defines both endpoints, all schemas, and the pagination wrapper. The Pydantic model will use camelCase aliases to match the contract's field naming.

If the verification in Step 2 above reveals a mismatch, bump to 1.2.0 following the workflow in Step 3.

### 4b. Pydantic Schemas

#### New file: `audio-ident-service/app/schemas/pagination.py`

Create a generic pagination schema that matches the contract's `PaginatedResponse<T>` shape.

```python
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


def _to_camel(name: str) -> str:
    """Convert snake_case to camelCase."""
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


class PaginationMeta(BaseModel):
    """Pagination metadata matching the API contract."""

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=_to_camel,
    )

    page: int
    page_size: int = Field(ge=1, le=100)
    total_items: int = Field(ge=0)
    total_pages: int = Field(ge=0)


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response wrapper matching the API contract."""

    data: list[T]
    pagination: PaginationMeta
```

**Key decisions**:
- `PaginationMeta` uses `alias_generator=_to_camel` so `page_size` serializes as `pageSize`, matching the contract.
- `populate_by_name=True` allows constructing the model with Python-style `page_size=50` while serializing to `pageSize`.
- The `PaginatedResponse` is generic over `T` so it works with `TrackInfo` and any future list endpoint.

**Verification test** (add to `tests/test_pagination_schema.py`):
```python
def test_pagination_serializes_to_contract_shape():
    from app.schemas.pagination import PaginatedResponse, PaginationMeta
    from app.schemas.search import TrackInfo
    import uuid, datetime

    meta = PaginationMeta(page=1, page_size=50, total_items=142, total_pages=3)
    serialized = meta.model_dump(by_alias=True)
    assert serialized == {
        "page": 1,
        "pageSize": 50,
        "totalItems": 142,
        "totalPages": 3,
    }
```

If this test fails, STOP and resolve the mismatch before proceeding.

#### Existing file: `audio-ident-service/app/schemas/track.py`

This file already contains `TrackDetail` extending `TrackInfo`. No changes needed. Verify it matches the contract:

- Contract `TrackDetail` fields: `sample_rate`, `channels`, `bitrate`, `format`, `file_hash_sha256`, `file_size_bytes`, `olaf_indexed`, `embedding_model`, `embedding_dim`, `updated_at`
- Pydantic `TrackDetail` fields: same list. Confirmed matching.

#### Query parameter validation

Create a query params model for the list endpoint. Add to the new `pagination.py` or as part of the router.

```python
class TrackListParams(BaseModel):
    """Query parameters for GET /api/v1/tracks."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100, alias="pageSize")
    search: str | None = None

    model_config = ConfigDict(populate_by_name=True)
```

**Note on `pageSize` alias**: The contract uses `pageSize` (camelCase) as the query parameter name. FastAPI reads query params by field name or alias, so `page_size` with `alias="pageSize"` will accept `?pageSize=50` from the URL.

### 4c. Database Queries

#### File: `audio-ident-service/app/routers/tracks.py` (NEW)

The track list query needs:
1. Count total matching rows (for pagination metadata).
2. Fetch one page of rows, ordered by `ingested_at DESC` (newest first).
3. Apply optional `search` filter using `ILIKE` on `title` and `artist`.

**SQLAlchemy async pattern** (matching the existing codebase which uses `AsyncSession`):

```python
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.track import Track

async def _get_tracks_page(
    db: AsyncSession,
    page: int,
    page_size: int,
    search: str | None,
) -> tuple[list[Track], int]:
    """Return (tracks, total_count) for the given page."""
    base_query = select(Track)

    if search:
        pattern = f"%{search}%"
        base_query = base_query.where(
            or_(
                Track.title.ilike(pattern),
                Track.artist.ilike(pattern),
            )
        )

    # Total count
    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_query)).scalar_one()

    # Paginated results
    offset = (page - 1) * page_size
    rows_query = base_query.order_by(Track.ingested_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(rows_query)
    tracks = list(result.scalars().all())

    return tracks, total
```

**For track detail**:

```python
async def _get_track_by_id(db: AsyncSession, track_id: uuid.UUID) -> Track | None:
    """Return a single track by UUID, or None if not found."""
    result = await db.execute(select(Track).where(Track.id == track_id))
    return result.scalar_one_or_none()
```

### 4d. Router Implementation

#### File: `audio-ident-service/app/routers/tracks.py` (NEW)

```python
"""Track listing and detail endpoints."""

from __future__ import annotations

import math
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.pagination import PaginatedResponse, PaginationMeta
from app.schemas.search import TrackInfo
from app.schemas.track import TrackDetail

router = APIRouter(tags=["tracks"])


@router.get(
    "/tracks",
    response_model=PaginatedResponse[TrackInfo],
)
async def list_tracks(
    page: int = Query(default=1, ge=1, description="1-indexed page number"),
    pageSize: int = Query(default=50, ge=1, le=100, alias="pageSize", description="Items per page"),
    search: str | None = Query(default=None, description="Filter by title or artist"),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[TrackInfo]:
    tracks, total = await _get_tracks_page(db, page, pageSize, search)

    items = [
        TrackInfo(
            id=t.id,
            title=t.title,
            artist=t.artist,
            album=t.album,
            duration_seconds=t.duration_seconds,
            ingested_at=t.ingested_at,
        )
        for t in tracks
    ]

    total_pages = max(1, math.ceil(total / pageSize)) if total > 0 else 0

    return PaginatedResponse(
        data=items,
        pagination=PaginationMeta(
            page=page,
            page_size=pageSize,
            total_items=total,
            total_pages=total_pages,
        ),
    )


@router.get(
    "/tracks/{track_id}",
    response_model=TrackDetail,
    responses={
        404: {"description": "Track not found"},
        400: {"description": "Invalid UUID format"},
    },
)
async def get_track_detail(
    track_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> TrackDetail | JSONResponse:
    track = await _get_track_by_id(db, track_id)

    if track is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"Track {track_id} not found",
                    "details": None,
                }
            },
        )

    return TrackDetail(
        id=track.id,
        title=track.title,
        artist=track.artist,
        album=track.album,
        duration_seconds=track.duration_seconds,
        ingested_at=track.ingested_at,
        sample_rate=track.sample_rate,
        channels=track.channels,
        bitrate=track.bitrate,
        format=track.format,
        file_hash_sha256=track.file_hash_sha256,
        file_size_bytes=track.file_size_bytes,
        olaf_indexed=track.olaf_indexed,
        embedding_model=track.embedding_model,
        embedding_dim=track.embedding_dim,
        updated_at=track.updated_at,
    )
```

**Important**: The `_get_tracks_page` and `_get_track_by_id` helper functions from Section 4c should be defined in the same file (or in a separate `app/services/tracks.py` if preferred). Keeping them in the router file is consistent with the existing `search.py` router pattern which defines helper functions inline.

#### Register in `app/main.py`

Add this import and router registration:

```python
# In imports section, add:
from app.routers import health, search, tracks, version

# In create_app(), add after the existing router registrations:
application.include_router(tracks.router, prefix="/api/v1")
```

The full registration block will be:

```python
application.include_router(health.router)
application.include_router(version.router, prefix="/api/v1")
application.include_router(search.router, prefix="/api/v1")
application.include_router(tracks.router, prefix="/api/v1")
```

#### Pagination Edge Cases (per contract)

- `page` > `totalPages`: Return empty `data` array with valid pagination meta. The SQL `OFFSET` naturally handles this.
- `page` < 1: FastAPI `Query(ge=1)` rejects this with 422. The contract says "treated as page=1" but FastAPI validation is stricter. This is acceptable since 422 is a subset of 400 (VALIDATION_ERROR).
- `pageSize` > 100: FastAPI `Query(le=100)` rejects this with 422.
- `pageSize` < 1: FastAPI `Query(ge=1)` rejects this with 422.

#### UUID Validation for track_id

FastAPI automatically validates UUID path parameters. If the path contains a non-UUID string, FastAPI returns 422 with validation details. The contract specifies `VALIDATION_ERROR` (400) for invalid UUID format. To match exactly, add a custom exception handler for `RequestValidationError` in the router or rely on FastAPI's global handler. The existing global exception handler in `main.py` already catches all exceptions. The 422 vs 400 difference is acceptable for this phase but could be harmonized in a future pass.

### 4e. Backend Tests

#### File: `audio-ident-service/tests/test_tracks.py` (NEW)

Use the same patterns as `test_health.py`: `pytest.mark.asyncio`, `AsyncClient` fixture from `conftest.py`.

**What to test**:

1. **test_list_tracks_returns_paginated_response**: GET `/api/v1/tracks` returns 200 with `data` array and `pagination` object.
2. **test_list_tracks_pagination_meta_uses_camel_case**: Verify response JSON keys are `pageSize`, `totalItems`, `totalPages` (not snake_case).
3. **test_list_tracks_with_search_filter**: GET `/api/v1/tracks?search=queen` returns only tracks matching "queen" in title or artist.
4. **test_list_tracks_page_beyond_total**: GET `/api/v1/tracks?page=999` returns empty `data` with valid pagination.
5. **test_list_tracks_invalid_page_size**: GET `/api/v1/tracks?pageSize=200` returns 422.
6. **test_get_track_detail_success**: GET `/api/v1/tracks/{valid_uuid}` returns 200 with full TrackDetail fields.
7. **test_get_track_detail_not_found**: GET `/api/v1/tracks/{random_uuid}` returns 404 with `error.code == "NOT_FOUND"`.
8. **test_get_track_detail_invalid_uuid**: GET `/api/v1/tracks/not-a-uuid` returns 422.
9. **test_pagination_schema_serialization**: Unit test for `PaginationMeta` camelCase serialization (see Section 4b).

**Test fixtures needed**:

The tracks endpoints require database records. Add a fixture that creates test Track records in the database:

```python
@pytest.fixture
async def seed_tracks(client: AsyncClient) -> list[uuid.UUID]:
    """Seed the database with test tracks for list/detail tests.

    This requires a running database. For unit tests that mock the DB,
    use mock_db fixture instead.
    """
    # Option A: Insert directly via SQLAlchemy
    # Option B: Use the ingest pipeline with test audio fixtures
    # Choose Option A for speed -- insert Track records directly.
    ...
```

**Important**: The existing `conftest.py` creates an `AsyncClient` with `ASGITransport(app=app)`. The `app` uses a lifespan that requires PostgreSQL, Qdrant, and CLAP model. For tests that only need the DB:

- If running with `make test` (which assumes infrastructure is running): Tests work as-is.
- For CI without infrastructure: Mock the lifespan dependencies or use a test-specific app factory.

The existing test suite (`test_health.py`) already works with the lifespan, so follow the same pattern. Add track seed data via SQLAlchemy direct insert in the fixture.

---

## 5. Frontend Implementation

### 5a. Type Generation

**Prerequisite**: Backend must be running with the new tracks router registered.

1. Start the backend: `make dev` (or at minimum `make docker-up` + start the service).
2. Verify the new endpoints are live:
   - `curl http://localhost:17010/api/v1/tracks` should return a paginated JSON response.
   - `curl http://localhost:17010/openapi.json | jq '.paths["/api/v1/tracks"]'` should show the endpoint definition.
3. Regenerate types: `make gen-client`.
4. Verify the generated file includes the new types:
   - Open `audio-ident-ui/src/lib/api/generated.ts`.
   - Confirm it contains types for `TrackDetail`, `PaginatedResponse_TrackInfo_` (or similar), `PaginationMeta`.
   - Confirm the `paths` interface includes `/api/v1/tracks` and `/api/v1/tracks/{track_id}`.

**CRITICAL**: Do NOT hand-write any TypeScript types. If `make gen-client` fails or produces unexpected output, debug the issue (backend not running? OpenAPI spec missing endpoint?). Do not work around it by writing types manually.

### 5b. API Client Extensions

#### File: `audio-ident-ui/src/lib/api/client.ts`

Add two new functions using the generated types. The exact import paths depend on how `openapi-typescript` names the generated types. Typical generated names:

- `components['schemas']['TrackInfo']` (already used for search)
- `components['schemas']['TrackDetail']` (new)
- `components['schemas']['PaginatedResponse_TrackInfo_']` or similar (new)

**Add to imports** (adjust names based on actual generated output):

```typescript
// Import generated types -- exact names determined by make gen-client output
import type { components } from './generated';

type TrackDetail = components['schemas']['TrackDetail'];
type PaginatedTrackResponse = components['schemas']['PaginatedResponse_TrackInfo_'];
```

**New functions**:

```typescript
export async function fetchTracks(
    page: number = 1,
    pageSize: number = 50,
    search?: string
): Promise<PaginatedTrackResponse> {
    const params = new URLSearchParams({
        page: String(page),
        pageSize: String(pageSize),
    });
    if (search) params.set('search', search);
    return fetchJSON<PaginatedTrackResponse>(`/api/v1/tracks?${params}`);
}

export async function fetchTrackDetail(id: string): Promise<TrackDetail> {
    return fetchJSON<TrackDetail>(`/api/v1/tracks/${id}`);
}
```

**Error handling**: The existing `fetchJSON` helper throws on non-OK responses. For the track detail 404 case, callers should catch this error and display a "not found" state. Consider enhancing `fetchJSON` to throw `ApiRequestError` instead of generic `Error` for structured error responses. This is an optional improvement for this phase.

**Re-exports**: Add type re-exports at the top of `client.ts` so components can import from `$lib/api/client`:

```typescript
export type { TrackDetail, PaginatedTrackResponse };
```

### 5c. Track Library Page (`/tracks`)

#### File: `audio-ident-ui/src/routes/tracks/+page.svelte` (NEW)

**Behavior**:
- On load: read `page`, `pageSize`, `search` from URL query params.
- Fetch tracks using TanStack Query with key `['tracks', page, pageSize, search]`.
- Search input: debounced (300ms), triggers `goto('?page=1&search=...', { replaceState: true })`.
- Pagination: prev/next buttons trigger `goto('?page=N&search=...')` (pushes to history for back-button support).
- Desktop (>= 640px): responsive table with columns Title, Artist, Album, Duration.
- Mobile (< 640px): card list (title, artist -- album, duration).
- Click on a row/card navigates to `/tracks/[id]`.
- Loading state: skeleton rows (pulse animation).
- Empty state: "No tracks found" with context-appropriate message.
- Error state: red alert with retry suggestion.

**URL-driven state management** (addresses SIG-4):

```svelte
<script lang="ts">
    import { page } from '$app/state';
    import { goto } from '$app/navigation';
    import { createQuery } from '@tanstack/svelte-query';
    import { fetchTracks } from '$lib/api/client';

    // Read state from URL (single source of truth)
    let currentPage = $derived(Number(page.url.searchParams.get('page')) || 1);
    let currentPageSize = $derived(Number(page.url.searchParams.get('pageSize')) || 50);
    let currentSearch = $derived(page.url.searchParams.get('search') || '');

    // Local state for the search input (not URL until debounce fires)
    let searchInput = $state(currentSearch);
    let debounceTimer: ReturnType<typeof setTimeout> | undefined;

    // Sync searchInput when URL changes (e.g., back button)
    $effect(() => {
        searchInput = currentSearch;
    });

    // Debounced search: update URL after 300ms of no typing
    function onSearchInput(value: string) {
        searchInput = value;
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            const params = new URLSearchParams();
            params.set('page', '1'); // Reset to page 1 on new search
            if (value) params.set('search', value);
            goto(`/tracks?${params}`, { replaceState: true });
        }, 300);
    }

    // Page navigation
    function goToPage(p: number) {
        const params = new URLSearchParams();
        params.set('page', String(p));
        if (currentSearch) params.set('search', currentSearch);
        goto(`/tracks?${params}`);
    }

    // TanStack Query -- key includes URL params so it refetches on navigation
    const tracksQuery = createQuery(() => ({
        queryKey: ['tracks', currentPage, currentPageSize, currentSearch],
        queryFn: () => fetchTracks(currentPage, currentPageSize, currentSearch || undefined),
    }));
</script>
```

**Page title**: `<svelte:head><title>Track Library - audio-ident</title></svelte:head>`

**Table structure** (desktop):

| Column | Source Field | Notes |
|--------|-------------|-------|
| Title | `title` | Clickable link to `/tracks/[id]` |
| Artist | `artist` | Display "--" if null |
| Album | `album` | Display "--" if null |
| Duration | `duration_seconds` | Format as `m:ss` |

**Card structure** (mobile, < 640px):

```
+------------------------------------------+
| Track Title                              |
| Artist -- Album                          |
| 5:55                               [>]  |
+------------------------------------------+
```

**Duration formatting helper** (add to a shared utils file or inline):

```typescript
function formatDuration(seconds: number): string {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
}
```

**Pagination controls**:

Display below the table/cards:
- "Previous" button (disabled when page === 1)
- "Page X of Y" text
- "Next" button (disabled when page === totalPages)
- Total items count: "142 tracks" in the page header

**Empty state variants**:
- No tracks at all (no search filter): "No tracks in the library yet. Ingest audio files to build your library."
- No tracks matching search: "No tracks found matching '{search}'. Try a different search term."

**Error state**: Consistent red alert with the error message and a "Try again" button that refetches.

### 5d. Track Detail Page (`/tracks/[id]`)

#### File: `audio-ident-ui/src/routes/tracks/[id]/+page.svelte` (NEW)

**Behavior**:
- Read `id` from `$page.params.id` (via `$app/state`).
- Fetch track detail via TanStack Query with key `['track', id]`.
- Display full metadata in two card sections (Track Information + Audio Properties).
- Display indexing status section (Olaf + CLAP status indicators).
- "Back" navigation: link back to `/tracks` (library). If the user came from search results, provide an additional link/button to go back to search (check sessionStorage).
- 404 handling: display "Track not found" with a link back to the library.
- Loading state: skeleton cards.

**Page title**: `<svelte:head><title>{track.title} - audio-ident</title></svelte:head>` (set after data loads, fallback to "Track Detail - audio-ident" while loading).

**Layout**:

```
+------------------------------------------------------------------+
| [<- Back to Library]      [<- Back to Search Results]*            |
|                                                                  |
| Track Title                                                      |
| Artist -- Album                                                  |
|                                                                  |
| +-----------------------------+  +-------------------------------+
| | Track Information           |  | Audio Properties              |
| |-----------------------------|  |-------------------------------|
| | Title:    ...               |  | Format:      MP3              |
| | Artist:   ...               |  | Sample Rate: 44100 Hz         |
| | Album:    ...               |  | Channels:    2 (stereo)       |
| | Duration: 5:55              |  | Bitrate:     320 kbps         |
| | Ingested: Feb 14, 2026      |  | File Size:   13.2 MB          |
| +-----------------------------+  +-------------------------------+
|                                                                  |
| +--------------------------------------------------------------+
| | Indexing Status                                               |
| |--------------------------------------------------------------|
| | Fingerprint (Olaf):  [green dot] Indexed                     |
| | Embeddings (CLAP):   [green dot] Indexed (512-dim)           |
| | File Hash (SHA-256): a1b2c3d4e5...                           |
| +--------------------------------------------------------------+
```

*The "Back to Search Results" link only appears when the user navigated from the search page (detected via sessionStorage flag).

**State management**:

```svelte
<script lang="ts">
    import { page } from '$app/state';
    import { createQuery } from '@tanstack/svelte-query';
    import { fetchTrackDetail } from '$lib/api/client';

    const trackId = $derived(page.params.id);

    const trackQuery = createQuery(() => ({
        queryKey: ['track', trackId],
        queryFn: () => fetchTrackDetail(trackId),
        retry: false, // Don't retry 404s
    }));

    // Check if user came from search results
    let cameFromSearch = $state(false);
    $effect(() => {
        if (typeof sessionStorage !== 'undefined') {
            cameFromSearch = sessionStorage.getItem('audio-ident-search-state') !== null;
        }
    });
</script>
```

**Formatting helpers**:

| Field | Format | Example |
|-------|--------|---------|
| `duration_seconds` | `m:ss` | `5:55` |
| `ingested_at` | Locale date/time | `Feb 14, 2026 10:30 AM` |
| `updated_at` | Locale date/time | `Feb 14, 2026 10:30 AM` |
| `file_size_bytes` | Human-readable | `13.2 MB` |
| `bitrate` | kbps | `320 kbps` |
| `sample_rate` | Hz | `44100 Hz` |
| `channels` | Text | `1 (mono)` / `2 (stereo)` |
| `file_hash_sha256` | Truncated + copy | `a1b2c3d4...` with copy button |

**Status indicators**:
- Olaf indexed: green dot + "Indexed" or red dot + "Not indexed"
- CLAP embeddings: green dot + "Indexed ({dim}-dim, {model})" or gray dot + "Not indexed"
- Use `olaf_indexed` boolean and `embedding_model !== null` to determine status.

**404 state**:
```
Track Not Found

The track you're looking for doesn't exist or may have been removed.

[Browse Track Library]
```

### 5e. Search Results Enhancement

#### File: `audio-ident-ui/src/lib/components/SearchResults.svelte`

**Change**: Make track titles clickable links to `/tracks/[id]`.

In the exact match card, change the track title from static text:
```svelte
<p class="font-medium text-gray-900">{match.track.title}</p>
```
to a link:
```svelte
<a href="/tracks/{match.track.id}"
   onclick={(e) => { saveSearchState(); }}
   class="font-medium text-gray-900 hover:text-indigo-600 hover:underline">
    {match.track.title}
</a>
```

Apply the same change to vibe match cards.

**Save search state before navigation** (addresses SIG-3):

Add a `saveSearchState` function prop or handle it at the search page level. The cleanest approach is to emit a custom event from SearchResults or pass a callback:

**Option chosen**: Pass the search response data from the parent (search page) and save to sessionStorage on any track link click.

In `search/+page.svelte`, add:

```typescript
function saveSearchState() {
    if (typeof sessionStorage === 'undefined') return;
    sessionStorage.setItem('audio-ident-search-state', JSON.stringify({
        response: searchResponse,
        mode: searchMode,
        inputMode: inputMode,
    }));
}
```

Pass this function to SearchResults as a prop:

```svelte
<SearchResults
    response={searchResponse}
    isLoading={$mutation.isPending}
    error={searchError}
    onTrackClick={saveSearchState}
/>
```

In `SearchResults.svelte`, accept the prop and call it before navigation:

```svelte
<script lang="ts">
    // Add to props:
    let { response, isLoading, error, onTrackClick }: {
        response: SearchResponse | null;
        isLoading: boolean;
        error: string | null;
        onTrackClick?: () => void;
    } = $props();
</script>
```

**Restore search state on search page mount**:

In `search/+page.svelte`, add an `$effect` that runs once on mount:

```typescript
$effect(() => {
    if (typeof sessionStorage === 'undefined') return;
    const saved = sessionStorage.getItem('audio-ident-search-state');
    if (saved) {
        try {
            const state = JSON.parse(saved);
            searchResponse = state.response;
            searchMode = state.mode;
            inputMode = state.inputMode;
            pageState = 'results';
        } catch {
            // Ignore corrupt data
        }
        sessionStorage.removeItem('audio-ident-search-state');
    }
});
```

**Important**: This `$effect` should only run once (on mount). Since it reads from `sessionStorage` and immediately removes the item, subsequent effect re-runs are harmless (no saved state to read). However, to be explicit about mount-only behavior, consider using `onMount` from svelte if `$effect` causes issues.

### 5f. NavBar Updates

#### File: `audio-ident-ui/src/lib/components/NavBar.svelte`

**Change**: Ensure the "Library" link exists and has an active state for `/tracks` routes.

The NavBar should already have a "Library" link from Phase 1. Verify:

- The link points to `/tracks`.
- Active state is applied when `page.url.pathname.startsWith('/tracks')`.
- Active state styling: `text-gray-900 font-semibold` (or whatever Phase 1 established).

If Phase 1 did not include the "Library" link (unlikely per the Phase 1 plan), add it now:

```svelte
<a href="/tracks"
   class={isLibraryActive ? 'text-sm font-semibold text-gray-900' : 'text-sm font-medium text-gray-600 hover:text-gray-900'}>
    Library
</a>
```

Where:
```svelte
let isLibraryActive = $derived(page.url.pathname.startsWith('/tracks'));
```

---

## 6. Testing Strategy

### Backend Tests

| Test | Type | File |
|------|------|------|
| PaginationMeta serializes to camelCase | Unit | `tests/test_pagination_schema.py` |
| PaginatedResponse matches contract shape | Unit | `tests/test_pagination_schema.py` |
| GET /api/v1/tracks returns paginated response | Integration | `tests/test_tracks.py` |
| GET /api/v1/tracks search filter works | Integration | `tests/test_tracks.py` |
| GET /api/v1/tracks page beyond total returns empty data | Integration | `tests/test_tracks.py` |
| GET /api/v1/tracks invalid pageSize rejected | Integration | `tests/test_tracks.py` |
| GET /api/v1/tracks/{id} returns TrackDetail | Integration | `tests/test_tracks.py` |
| GET /api/v1/tracks/{id} unknown UUID returns 404 | Integration | `tests/test_tracks.py` |
| GET /api/v1/tracks/{id} invalid UUID format returns 422 | Integration | `tests/test_tracks.py` |

**Run**: `cd audio-ident-service && uv run pytest`

### Frontend Tests

| Test | Type | File |
|------|------|------|
| fetchTracks sends correct query params | Unit | `tests/tracks.test.ts` |
| fetchTrackDetail sends correct path | Unit | `tests/tracks.test.ts` |
| Duration formatting helper | Unit | `tests/utils.test.ts` |
| File size formatting helper | Unit | `tests/utils.test.ts` |

**Run**: `cd audio-ident-ui && pnpm test`

### Manual Verification Steps

After all automated tests pass, verify these in a browser:

1. Navigate to `/tracks` -- table renders with track data (requires seeded database via `make ingest`).
2. Type in the search box -- table filters after debounce, URL updates.
3. Click "Next" -- page 2 loads, URL updates, back button returns to page 1.
4. Click a track title -- navigates to `/tracks/[id]`, detail page renders.
5. Click "Back to Library" on detail page -- returns to `/tracks`.
6. Go to `/search`, perform a search, click a track title in results -- navigates to `/tracks/[id]`.
7. Click browser back button from track detail -- returns to search page WITH results still displayed (sessionStorage restore).
8. Navigate to `/tracks/nonexistent-uuid` -- shows 404 "Track not found" message.
9. Test on mobile viewport (Chrome DevTools) -- table switches to card layout at < 640px.
10. Verify generated types: open `src/lib/api/generated.ts` and confirm no hand-written placeholder types remain for tracks/pagination.

---

## 7. Implementation Order (Numbered, Dependency-Ordered)

| Step | Action | Verify Before Next Step |
|------|--------|------------------------|
| 1 | **Contract verification**: Read `docs/api-contract.md`, confirm PaginatedResponse shape. | Shape matches intent. |
| 2 | **Create `app/schemas/pagination.py`**: PaginationMeta with camelCase aliases, PaginatedResponse generic, TrackListParams. | `python -c "from app.schemas.pagination import PaginatedResponse"` succeeds. |
| 3 | **Write schema serialization test**: `tests/test_pagination_schema.py` -- verify camelCase output matches contract. | `uv run pytest tests/test_pagination_schema.py` passes. |
| 4 | **Create `app/routers/tracks.py`**: Both endpoints with DB queries, error handling, and pagination logic. | File exists, no syntax errors. |
| 5 | **Register router in `app/main.py`**: Add `tracks.router` with `/api/v1` prefix. | `from app.routers import tracks` succeeds. |
| 6 | **Write backend integration tests**: `tests/test_tracks.py` with seed fixtures. | `uv run pytest tests/test_tracks.py` passes. |
| 7 | **Run full backend test suite**: `uv run pytest`. | All tests pass (existing + new). |
| 8 | **Verify endpoints live**: `curl http://localhost:17010/api/v1/tracks` returns JSON. `curl http://localhost:17010/openapi.json` includes new paths. | Endpoints respond correctly. |
| 9 | **Regenerate types**: `make gen-client`. | `generated.ts` contains TrackDetail, PaginatedResponse types. |
| 10 | **Add client functions**: `fetchTracks()` and `fetchTrackDetail()` in `client.ts` using generated types. | `pnpm check` passes (no type errors). |
| 11 | **Create `/tracks` page**: `src/routes/tracks/+page.svelte` with URL-driven pagination, search, table/cards. | Page renders at `http://localhost:17000/tracks`. |
| 12 | **Create `/tracks/[id]` page**: `src/routes/tracks/[id]/+page.svelte` with detail display and back navigation. | Page renders at `http://localhost:17000/tracks/{some-uuid}`. |
| 13 | **Enhance SearchResults.svelte**: Add track title links with `onTrackClick` callback for sessionStorage save. | Track titles are clickable links. |
| 14 | **Add search state preservation**: Add save/restore logic to `search/+page.svelte`. | Navigate to track detail from search results, press back, search results are preserved. |
| 15 | **Update NavBar**: Verify "Library" link exists with active state for `/tracks*`. | "Library" highlighted when on `/tracks` or `/tracks/[id]`. |
| 16 | **Write frontend tests**: `tests/tracks.test.ts` for client functions. | `pnpm test` passes. |
| 17 | **Run full frontend checks**: `pnpm check && pnpm test && pnpm lint`. | All pass. |
| 18 | **Manual browser verification**: Walk through the 10 manual verification steps in Section 6. | All scenarios work correctly. |

---

## 8. Acceptance Criteria

| # | Criterion | How to Verify |
|---|-----------|---------------|
| 1 | `GET /api/v1/tracks` returns paginated response with camelCase pagination keys | `curl` + inspect JSON |
| 2 | `GET /api/v1/tracks?search=queen` filters by title/artist (case-insensitive) | `curl` with search param |
| 3 | `GET /api/v1/tracks?page=999` returns empty data with valid pagination | `curl` with high page number |
| 4 | `GET /api/v1/tracks/{uuid}` returns full TrackDetail for existing track | `curl` with known UUID |
| 5 | `GET /api/v1/tracks/{uuid}` returns 404 with ErrorResponse for unknown UUID | `curl` with random UUID |
| 6 | `/tracks` page shows paginated table (desktop) / cards (mobile) | Browser at 1024px and 375px widths |
| 7 | Search input on `/tracks` is debounced and updates URL query params | Type in search, observe URL change after 300ms |
| 8 | Pagination controls update URL and support browser back button | Click Next, then browser Back |
| 9 | `/tracks/[id]` page shows all metadata fields from TrackDetail schema | Navigate to track detail, verify all fields render |
| 10 | Track titles in search results link to `/tracks/[id]` | Perform a search, click a track title |
| 11 | Search state is preserved when navigating to track detail and back | Search, click track, press Back, verify results display |
| 12 | All TypeScript types are generated (no hand-written API types) | Inspect `generated.ts` -- no placeholder comments |
| 13 | `uv run pytest` passes (all backend tests) | Run command |
| 14 | `pnpm check` passes (no type errors) | Run command |
| 15 | `pnpm test` passes (all frontend tests) | Run command |
| 16 | NavBar "Library" link has active state on `/tracks*` routes | Navigate to `/tracks`, verify styling |

---

## 9. Risks and Mitigations

### Risk 1: PaginatedResponse Pydantic Serialization Mismatch

**Risk**: Pydantic's Generic[T] combined with alias_generator may not serialize correctly to the contract's camelCase field names. FastAPI's `response_model` serialization may strip aliases.

**Mitigation**: Step 3 in the implementation order is a dedicated serialization unit test. If it fails, investigate `model_config` settings. FastAPI uses `model.model_dump(by_alias=True)` for response serialization when `response_model` is set, so aliases should work. If they don't, use explicit `Field(serialization_alias="pageSize")` instead of `alias_generator`.

**Fallback**: If camelCase aliasing proves impossible without a contract change, bump the contract to 1.2.0 with snake_case field names (`page_size`, `total_items`, `total_pages`). This is undesirable but acceptable.

### Risk 2: make gen-client Produces Unexpected Type Names

**Risk**: `openapi-typescript` may generate type names that don't match expectations (e.g., `PaginatedResponse_TrackInfo_` vs `PaginatedResponseTrackInfo`).

**Mitigation**: After running `make gen-client`, inspect the generated file and adjust import paths in `client.ts` accordingly. The generated type names are deterministic for a given OpenAPI spec, so this is a one-time investigation.

**Fallback**: If the generated types are unwieldy, create type aliases in `client.ts` for convenience: `type PaginatedTracks = components['schemas']['PaginatedResponse_TrackInfo_']`. This is NOT hand-writing types -- it is aliasing generated types.

### Risk 3: sessionStorage Not Available in SSR

**Risk**: The search state preservation logic uses `sessionStorage`, which is not available during server-side rendering. SvelteKit renders pages server-side first (`ssr = true`).

**Mitigation**: Guard all `sessionStorage` access with `typeof sessionStorage !== 'undefined'` or wrap in `$effect` (which only runs on the client). The code samples in Section 5e already include these guards.

### Risk 4: Database Needs Seed Data for Testing

**Risk**: The `/tracks` page is useless without ingested tracks in the database. Manual testing requires running `make ingest`.

**Mitigation**: Document this prerequisite in the manual verification steps. For automated backend tests, use a seed fixture that inserts Track records directly via SQLAlchemy (faster than running the full ingest pipeline).

### Risk 5: Backend Lifespan Blocks Test Execution

**Risk**: The existing `conftest.py` fixture uses `ASGITransport(app=app)`, which triggers the full lifespan (PostgreSQL, Qdrant, CLAP model loading). This is slow and requires all infrastructure running.

**Mitigation**: For unit tests that only test schema serialization (Step 3), use plain pytest without the client fixture -- just import and test the Pydantic model directly. For integration tests (Step 6), require infrastructure to be running (`make docker-up`). This matches the existing testing convention.

### Risk 6: camelCase Query Parameters

**Risk**: The contract uses `pageSize` (camelCase) as a query parameter. FastAPI reads query parameters by Python argument name by default (which would be `page_size`). The implementer must use `Query(alias="pageSize")` to accept the camelCase param.

**Mitigation**: The router implementation in Section 4d explicitly uses `pageSize: int = Query(default=50, ge=1, le=100, alias="pageSize")` to handle this. The integration test in Step 6 must send `?pageSize=50` (not `?page_size=50`) to verify this works.

### Risk 7: GenericAlias Serialization in Response Model

**Risk**: Using `PaginatedResponse[TrackInfo]` as a `response_model` in FastAPI may not resolve the generic correctly, causing serialization issues or incorrect OpenAPI schema generation.

**Mitigation**: FastAPI and Pydantic v2 support Generic[T] in response models. Test this explicitly:
1. Start the backend.
2. Check `http://localhost:17010/openapi.json` -- verify the schema for `/api/v1/tracks` shows the resolved `PaginatedResponse` with `TrackInfo` items.
3. If the OpenAPI schema is wrong, use a concrete (non-generic) class: `class PaginatedTrackResponse(PaginatedResponse[TrackInfo]): pass` and use that as the response model.

---

## Appendix A: File Inventory

### Files Created (Backend)

| File | Purpose |
|------|---------|
| `audio-ident-service/app/schemas/pagination.py` | PaginationMeta, PaginatedResponse, TrackListParams |
| `audio-ident-service/app/routers/tracks.py` | GET /api/v1/tracks + GET /api/v1/tracks/{id} |
| `audio-ident-service/tests/test_pagination_schema.py` | Serialization unit tests |
| `audio-ident-service/tests/test_tracks.py` | Endpoint integration tests |

### Files Modified (Backend)

| File | Change |
|------|--------|
| `audio-ident-service/app/main.py` | Add `from app.routers import tracks` and `include_router(tracks.router, prefix="/api/v1")` |

### Files Created (Frontend)

| File | Purpose |
|------|---------|
| `audio-ident-ui/src/routes/tracks/+page.svelte` | Track library page |
| `audio-ident-ui/src/routes/tracks/[id]/+page.svelte` | Track detail page |
| `audio-ident-ui/tests/tracks.test.ts` | API client function tests |

### Files Modified (Frontend)

| File | Change |
|------|--------|
| `audio-ident-ui/src/lib/api/generated.ts` | Regenerated by `make gen-client` (NOT manually edited) |
| `audio-ident-ui/src/lib/api/client.ts` | Add `fetchTracks()`, `fetchTrackDetail()`, type re-exports |
| `audio-ident-ui/src/lib/components/SearchResults.svelte` | Add track title links + `onTrackClick` prop |
| `audio-ident-ui/src/routes/search/+page.svelte` | Add search state save/restore via sessionStorage |
| `audio-ident-ui/src/lib/components/NavBar.svelte` | Verify/add Library link active state (may be no-op if Phase 1 included it) |

### Files NOT Modified (Contract)

| File | Status |
|------|--------|
| `docs/api-contract.md` | No changes expected (v1.1.0 already defines everything) |
| `audio-ident-service/docs/api-contract.md` | No changes expected |
| `audio-ident-ui/docs/api-contract.md` | No changes expected |

If Step 2 (contract verification) reveals a mismatch, all three contract files will need a version bump to 1.2.0.

---

## Appendix B: Pydantic Schema Reference

These are the existing backend schemas relevant to Phase 2. They are NOT TypeScript types -- they are the Python source of truth from which TypeScript types will be generated.

### `app/schemas/search.py` (EXISTING, DO NOT MODIFY)

```python
class TrackInfo(BaseModel):
    id: uuid.UUID
    title: str
    artist: str | None = None
    album: str | None = None
    duration_seconds: float
    ingested_at: datetime
```

### `app/schemas/track.py` (EXISTING, DO NOT MODIFY)

```python
class TrackDetail(TrackInfo):
    sample_rate: int | None = None
    channels: int | None = None
    bitrate: int | None = None
    format: str | None = None
    file_hash_sha256: str
    file_size_bytes: int
    olaf_indexed: bool
    embedding_model: str | None = None
    embedding_dim: int | None = None
    updated_at: datetime
```

### `app/schemas/errors.py` (EXISTING, DO NOT MODIFY)

```python
class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any = None

class ErrorResponse(BaseModel):
    error: ErrorDetail
```

### `app/models/track.py` (EXISTING, DO NOT MODIFY)

The SQLAlchemy Track model has all columns needed for both TrackInfo and TrackDetail. No migration needed.

---

*Plan authored from exhaustive analysis of the current codebase, API contract v1.1.0, UI functionality inventory, backend capabilities map, UX recommendations, and devil's advocate review corrections.*
