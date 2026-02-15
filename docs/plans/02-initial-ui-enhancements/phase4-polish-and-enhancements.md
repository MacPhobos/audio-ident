# Phase 4: Polish and Future Enhancements

> **Date**: 2026-02-15
> **Status**: Planning (lower priority, implement selectively after Phases 1-3)
> **Prerequisites**: Phases 1-3 complete (global navigation, track library, track detail, ingest UI)
> **API Contract Version**: 1.1.0 (FROZEN -- changes require version bump)

---

## 1. Objective

Phase 4 transforms audio-ident from a functional developer tool into a polished, secure, and maintainable application. It addresses six areas that were explicitly deferred from Phases 1-3: authentication, search UX refinements, audio playback, component library standardization, test coverage, and developer experience improvements.

### Success Criteria

- Authentication protects the ingest endpoint; unauthorized users cannot mutate the library
- Search experience communicates latency, degradation, and CLAP model status to the user
- Audio playback allows previewing matched tracks inline
- Shared component library eliminates duplicated UI patterns across pages
- Component test coverage reaches 70%+ for all pages and shared components
- All hand-written placeholder types replaced with auto-generated types from OpenAPI
- Zero unused dependencies in package.json

### What Phase 4 Is NOT

Phase 4 is a collection of independent enhancements. It is NOT a monolithic phase. Teams should cherry-pick sub-phases based on need and capacity. Sub-phases 4A-4F can be executed in parallel where dependencies allow.

---

## 2. Sub-phases

### 4A: Authentication System

**Priority within Phase 4**: HIGH (blocks securing the ingest endpoint from SIG-5 in devil's advocate review)
**Effort**: Large
**Can be done independently**: Yes (no dependency on other Phase 4 sub-phases)
**Dependencies**: Phase 3 complete (ingest UI exists and needs protection)

#### Objective

Implement a minimal authentication system using the existing JWT, OAuth2, and argon2 stubs. Gate the ingest page behind login. Provide a frontend login flow with token storage and protected route handling.

#### Backend Work

##### 1. Create User Model and Migration

**File to create**: `audio-ident-service/app/models/user.py`

```
Table: users
Columns:
  id: UUID (PK, default uuid4)
  email: String(255), unique, not null
  hashed_password: Text, not null
  is_active: Boolean, default true
  is_admin: Boolean, default false
  created_at: DateTime(tz), server_default=now()
  updated_at: DateTime(tz), onupdate=now()

Indexes:
  ix_users_email (unique on email)
```

**File to create**: Alembic migration via `uv run alembic revision --autogenerate -m "add users table"`

##### 2. Create Auth Schemas

**File to create**: `audio-ident-service/app/schemas/auth.py`

Pydantic models needed:
- `TokenResponse`: `{ access_token: str, token_type: str = "bearer" }`
- `UserCreate`: `{ email: str, password: str }` (with email validation)
- `UserResponse`: `{ id: UUID, email: str, is_active: bool, is_admin: bool, created_at: datetime }`

##### 3. Create Auth Router

**File to create**: `audio-ident-service/app/routers/auth.py`

Endpoints:
- `POST /api/v1/auth/token` -- OAuth2 password bearer token endpoint. Accepts `username` (email) and `password` via form data. Returns `TokenResponse`.
- `POST /api/v1/auth/register` -- Create a new user account. Accepts `UserCreate`. Returns `UserResponse` (201). This endpoint should be admin-only or disabled in production (controlled by a setting like `REGISTRATION_ENABLED=true`).

Wire existing utilities:
- `app/auth/password.py` -- `hash_password()` for registration, `verify_password()` for login
- `app/auth/jwt.py` -- `create_access_token()` for token generation, `decode_access_token()` for validation
- `app/auth/oauth2.py` -- `oauth2_scheme` for dependency injection on protected routes

##### 4. Create Auth Dependency

**File to create**: `audio-ident-service/app/auth/dependencies.py`

```python
async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """Decode JWT, fetch user from DB, raise 401 if invalid."""

async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Raise 403 if user is not admin."""
```

##### 5. Protect the Ingest Endpoint

**File to modify**: `audio-ident-service/app/routers/ingest.py`

Add `Depends(require_admin)` to the `POST /api/v1/ingest` endpoint. The tracks list and detail endpoints remain public.

##### 6. Register Auth Router

**File to modify**: `audio-ident-service/app/main.py`

```python
app.include_router(auth.router, prefix="/api/v1")
```

##### 7. Seed Initial Admin User

**File to create**: `audio-ident-service/scripts/create_admin.py`

CLI script to create the first admin user: `uv run python -m scripts.create_admin --email admin@example.com --password changeme`

This is needed because the registration endpoint may be disabled in production.

#### Contract Update

Adding `POST /api/v1/auth/token` and `POST /api/v1/auth/register` as new endpoints requires a contract version bump to 1.2.0. Follow the contract synchronization workflow from CLAUDE.md:

1. Update `audio-ident-service/docs/api-contract.md` (add auth endpoints and schemas)
2. Update backend Pydantic schemas (done above)
3. Backend tests pass
4. Copy contract to `audio-ident-ui/docs/api-contract.md`
5. Copy contract to `docs/api-contract.md`
6. Regenerate types: `make gen-client`
7. Update frontend code
8. Frontend tests pass

#### Frontend Work

##### 1. Add Auth Client Functions

**File to modify**: `audio-ident-ui/src/lib/api/client.ts`

After running `make gen-client`, import generated types and add:
- `login(email, password)` -- POST to `/api/v1/auth/token`, store token
- `register(email, password)` -- POST to `/api/v1/auth/register`
- Modify the base `fetchJSON` helper and `searchAudio`/`ingestAudio` to attach `Authorization: Bearer <token>` header when a token exists

##### 2. Token Storage

**File to create**: `audio-ident-ui/src/lib/auth/token.ts`

Store the JWT in `localStorage` (acceptable for this application's threat model). Provide:
- `getToken(): string | null`
- `setToken(token: string): void`
- `clearToken(): void`
- `isAuthenticated(): boolean`

Note: For a production application handling sensitive data, consider `httpOnly` cookies instead. For audio-ident (a music identification tool), localStorage is adequate.

##### 3. Create Login Page

**File to create**: `audio-ident-ui/src/routes/login/+page.svelte`

Simple login form with email and password fields. On success, store token and redirect to the page the user was trying to access (default: `/admin/ingest`). On failure, show error message.

Use `zod` for client-side form validation (this leverages the currently unused dependency):
```typescript
import { z } from 'zod';
const loginSchema = z.object({
  email: z.string().email(),
  password: z.string().min(8),
});
```

##### 4. Protected Route Guard

**File to create**: `audio-ident-ui/src/routes/admin/+layout.ts`

SvelteKit layout load function that checks for a valid token. If no token exists, redirect to `/login?redirect=/admin/ingest`.

```typescript
import { redirect } from '@sveltejs/kit';
import { isAuthenticated } from '$lib/auth/token';

export function load() {
  if (!isAuthenticated()) {
    throw redirect(302, '/login?redirect=/admin/ingest');
  }
}
```

Note: This is a client-side guard only. The backend enforces auth independently via JWT validation. The frontend guard is a UX convenience, not a security measure.

##### 5. Update NavBar

**File to modify**: `audio-ident-ui/src/lib/components/NavBar.svelte`

Add a login/logout indicator in the utility section of the nav bar. When authenticated, show a small user icon or email with a "Logout" action. When not authenticated and on the ingest page, redirect to login.

#### Files Summary (4A)

| Action | File | Notes |
|--------|------|-------|
| Create | `app/models/user.py` | User ORM model |
| Create | `app/schemas/auth.py` | Token, UserCreate, UserResponse |
| Create | `app/routers/auth.py` | token + register endpoints |
| Create | `app/auth/dependencies.py` | get_current_user, require_admin |
| Modify | `app/routers/ingest.py` | Add Depends(require_admin) |
| Modify | `app/main.py` | Register auth router |
| Create | `scripts/create_admin.py` | Seed admin CLI |
| Create | Alembic migration | users table |
| Modify | `docs/api-contract.md` (all 3 copies) | Bump to v1.2.0 |
| Modify | `src/lib/api/client.ts` | Auth headers + login/register functions |
| Create | `src/lib/auth/token.ts` | Token storage utilities |
| Create | `src/routes/login/+page.svelte` | Login page |
| Create | `src/routes/admin/+layout.ts` | Protected route guard |
| Modify | `src/lib/components/NavBar.svelte` | Auth indicator |

#### Acceptance Criteria (4A)

- [ ] `POST /api/v1/auth/token` returns a JWT for valid credentials
- [ ] `POST /api/v1/auth/register` creates a user (when enabled)
- [ ] `POST /api/v1/ingest` returns 401 without a valid token
- [ ] `POST /api/v1/ingest` returns 403 for non-admin users
- [ ] Frontend login page accepts email/password and stores token
- [ ] Navigating to `/admin/ingest` without auth redirects to `/login`
- [ ] After login, user can access `/admin/ingest` and ingest files
- [ ] Logout clears token and redirects to home
- [ ] Contract bumped to 1.2.0 and synchronized across all three locations
- [ ] Types regenerated via `make gen-client`
- [ ] Backend tests pass for all auth endpoints
- [ ] `scripts/create_admin.py` successfully seeds an admin user

---

### 4B: Search UX Enhancements

**Priority within Phase 4**: MEDIUM (addresses SIG-6 from devil's advocate review and improves retention)
**Effort**: Medium
**Can be done independently**: Yes
**Dependencies**: None (all changes are frontend-only, no new backend endpoints)

#### Objective

Improve the search experience with CLAP latency awareness, search history, first-time user onboarding, and keyboard shortcuts for power users.

#### Tasks

##### 1. CLAP Cold-Start and Latency Indicator (SIG-6 Fix)

**File to modify**: `audio-ident-ui/src/routes/search/+page.svelte`

The search page currently shows a generic "Searching for matches..." spinner during the `searching` state. Enhance this:

- After 3 seconds of searching, update the message to: "Still searching... AI similarity matching can take a moment."
- After the response arrives, if `mode_used` differs from the user's selected `mode` (e.g., user selected "both" but response shows `mode_used: "exact"`), display an informational notice below the results: "AI similarity matching was unavailable for this search. Only fingerprint results are shown."
- Add estimated latency hints to the search mode descriptions:
  - "Exact ID (Fingerprint only) -- typically < 1 second"
  - "Similar Vibe (AI matching only) -- may take 1-3 seconds"
  - "Both -- combines fingerprint and AI matching"

Implementation: Use a `$effect` with `setTimeout` to update the spinner message after 3 seconds. Check `searchResponse.mode_used !== searchMode` on results display.

##### 2. Search History (localStorage-Based)

**File to create**: `audio-ident-ui/src/lib/stores/searchHistory.ts`

Store recent searches in localStorage (no backend needed):

```typescript
interface SearchHistoryEntry {
  id: string;           // request_id from SearchResponse
  timestamp: number;    // Date.now()
  mode: SearchMode;
  inputType: 'record' | 'upload';
  fileName?: string;    // for uploads
  topResult?: {         // first match title/artist
    title: string;
    artist: string | null;
    confidence?: number;
    similarity?: number;
  };
}
```

- Store the last 20 searches
- Display on the search page below the mode selector when in `idle` state
- Each history entry shows: timestamp, input type icon (mic/file), top result, and a "details" badge
- Clicking a history entry does NOT replay the search (audio blob is not stored) but shows the previous results if still cached in TanStack Query

**File to modify**: `audio-ident-ui/src/routes/search/+page.svelte`

Add a "Recent Searches" section that appears in the `idle` state when history is not empty.

##### 3. First-Time User Onboarding

**File to modify**: `audio-ident-ui/src/routes/search/+page.svelte`

When the search page loads in `idle` state and there is no search history, show a brief tutorial panel:

```
+----------------------------------------------------------+
| How to identify audio:                                   |
|                                                          |
| 1. Choose a search mode above                           |
| 2. Record audio from your microphone or upload a file    |
| 3. Wait for results -- we'll check both fingerprints     |
|    and AI similarity                                     |
|                                                          |
| Tip: For best results, record at least 5 seconds of     |
| clear audio close to the speaker.                        |
+----------------------------------------------------------+
```

Use `localStorage.getItem('onboarding-dismissed')` to track whether the user has dismissed this. Show a small "x" button to dismiss permanently.

##### 4. Keyboard Shortcuts

**File to create**: `audio-ident-ui/src/lib/shortcuts.ts`

Register global keyboard shortcuts via a `$effect` in the root layout:

| Shortcut | Action | Scope |
|----------|--------|-------|
| `/` | Focus search input on tracks page | `/tracks` only |
| `r` | Start/stop recording | `/search` only, not when typing in an input |
| `Escape` | Cancel current search | `/search` during `searching` state |

Implementation: Use `document.addEventListener('keydown', ...)` guarded by `$effect` in `+layout.svelte`. Check `document.activeElement` to avoid triggering shortcuts when typing in form fields.

**File to modify**: `audio-ident-ui/src/routes/+layout.svelte`

Register the global shortcut listener. Dispatch custom events that individual pages listen for.

##### 5. Mobile Recording Prompt

**File to modify**: `audio-ident-ui/src/lib/components/AudioRecorder.svelte`

When recording starts on a mobile device (detect via `navigator.maxTouchPoints > 0` or `window.matchMedia('(pointer: coarse)')`), show a brief toast-style prompt: "Hold your phone near the speaker for best results." Dismiss automatically after 3 seconds.

#### Files Summary (4B)

| Action | File | Notes |
|--------|------|-------|
| Modify | `src/routes/search/+page.svelte` | Latency indicator, history, onboarding |
| Create | `src/lib/stores/searchHistory.ts` | localStorage search history |
| Create | `src/lib/shortcuts.ts` | Keyboard shortcut handler |
| Modify | `src/routes/+layout.svelte` | Register shortcuts |
| Modify | `src/lib/components/AudioRecorder.svelte` | Mobile recording prompt |

#### Acceptance Criteria (4B)

- [ ] After 3 seconds of searching, spinner message updates with latency explanation
- [ ] When `mode_used` differs from requested mode, a notice is displayed
- [ ] Search mode dropdown shows estimated latency hints
- [ ] Last 20 searches are persisted in localStorage and displayed in idle state
- [ ] First-time users see onboarding panel; dismissal is remembered
- [ ] `/` focuses track search input on the library page
- [ ] `r` key toggles recording on the search page (when not focused on an input)
- [ ] `Escape` cancels an in-progress search
- [ ] Mobile users see a "hold near speaker" prompt when recording starts

---

### 4C: Audio Playback

**Priority within Phase 4**: MEDIUM (high user value but requires new backend endpoint)
**Effort**: Medium
**Can be done independently**: Yes
**Dependencies**: Phase 2 complete (track detail page must exist), contract update required

#### Objective

Enable users to listen to matched tracks directly in the browser. Add a streaming audio endpoint to the backend and an inline audio player to the track detail page and search results.

#### Backend Work

##### 1. Create Audio Streaming Endpoint

**File to create**: `audio-ident-service/app/routers/audio.py`

Endpoint: `GET /api/v1/tracks/{id}/audio`

Behavior:
- Look up the track by UUID in PostgreSQL
- Retrieve the raw audio file path from `track.file_path`
- Return the file as a streaming response with appropriate `Content-Type` (based on `track.format`) and `Content-Disposition: inline`
- Support HTTP Range requests for seeking (use FastAPI's `FileResponse` or `StreamingResponse` with range header parsing)
- Return 404 if track not found, 404 if file missing on disk

Considerations:
- **Licensing/rights**: This endpoint serves the original audio file. In a production deployment with copyrighted content, this endpoint MUST be gated behind authentication (Phase 4A). For development purposes, it can initially be unprotected.
- **File size**: Raw audio files can be large (10-50 MB for high-quality tracks). HTTP Range support is essential for efficient seeking.
- **Cache headers**: Set `Cache-Control: private, max-age=3600` to allow browser caching but prevent CDN caching of audio content.

##### 2. Register Audio Router

**File to modify**: `audio-ident-service/app/main.py`

```python
app.include_router(audio.router, prefix="/api/v1")
```

#### Contract Update

Adding `GET /api/v1/tracks/{id}/audio` requires a contract version bump. If 4A has already bumped to 1.2.0, this can be included in the same version or bumped to 1.3.0 depending on sequencing.

Follow the contract synchronization workflow.

#### Frontend Work

##### 1. Create AudioPlayer Component

**File to create**: `audio-ident-ui/src/lib/components/AudioPlayer.svelte`

Props: `trackId: string`, `format: string | null`

A minimal audio player using the native HTML5 `<audio>` element:
- Source URL: `/api/v1/tracks/{trackId}/audio`
- Controls: play/pause, seek bar, volume, current time / duration
- Use Tailwind styling consistent with the rest of the app
- Loading state: show skeleton while audio metadata loads
- Error state: "Audio unavailable" message if endpoint returns 404

Do NOT implement a custom audio player from scratch. The native `<audio controls>` element with Tailwind styling is sufficient and accessible by default.

##### 2. Add Player to Track Detail Page

**File to modify**: `audio-ident-ui/src/routes/tracks/[id]/+page.svelte`

Add the `AudioPlayer` component below the track title/artist section, above the metadata cards. Only render if the track has audio available (check `format` field is not null).

##### 3. Add Preview in Search Results

**File to modify**: `audio-ident-ui/src/lib/components/SearchResults.svelte`

Add a small play button next to each track title in search results. On click, expand a mini-player below the result card. Only one track can play at a time -- clicking play on another track stops the current one.

Implementation: Use a shared `currentlyPlaying` state (Svelte 5 rune) to track which track ID is currently playing. The mini-player renders inline below the active result card.

#### Files Summary (4C)

| Action | File | Notes |
|--------|------|-------|
| Create | `app/routers/audio.py` | Streaming audio endpoint |
| Modify | `app/main.py` | Register audio router |
| Modify | `docs/api-contract.md` (all 3 copies) | Add streaming endpoint |
| Create | `src/lib/components/AudioPlayer.svelte` | HTML5 audio player |
| Modify | `src/routes/tracks/[id]/+page.svelte` | Embed full player |
| Modify | `src/lib/components/SearchResults.svelte` | Inline preview player |

#### Acceptance Criteria (4C)

- [ ] `GET /api/v1/tracks/{id}/audio` returns the audio file with correct Content-Type
- [ ] HTTP Range requests work for seeking
- [ ] Track detail page shows an audio player when audio is available
- [ ] Search results show a play button per match
- [ ] Only one track plays at a time across the entire page
- [ ] Audio player gracefully handles 404 (file missing) with user-friendly message
- [ ] Contract updated and types regenerated

---

### 4D: Component Library and Design System

**Priority within Phase 4**: MEDIUM (reduces long-term maintenance, prevents UI inconsistency)
**Effort**: Medium
**Can be done independently**: Yes (purely frontend, no backend changes)
**Dependencies**: None, but best done AFTER Phases 1-3 so all components exist to evaluate

#### Objective

Standardize the UI component library by either adopting shadcn-svelte or extracting a project-specific design system. Establish consistent design tokens, eliminate duplicated patterns, and optionally add dark mode support.

#### Decision: shadcn-svelte

The `audio-ident-ui/CLAUDE.md` lists shadcn-svelte as part of the stack, but it is not installed. The devil's advocate review (MIN-2) identified this as a "decide now or never" item.

**Recommendation**: Adopt shadcn-svelte in Phase 4D. The benefits outweigh the migration cost:

- Provides accessible, well-tested primitives (Button, Input, Select, Card, Table, Dialog, Toast)
- Built for Svelte 5 with runes support
- Uses Tailwind CSS (already installed)
- Reduces custom component code and maintenance burden
- Consistent styling without a custom design system

**Alternative**: If the team decides against shadcn-svelte, document this decision and remove all references from CLAUDE.md. Extract shared components manually instead.

#### Tasks

##### 1. Install shadcn-svelte

**Commands**:
```bash
cd audio-ident-ui
pnpm dlx shadcn-svelte@latest init
```

This creates:
- `src/lib/components/ui/` directory (currently missing, referenced in CLAUDE.md)
- Component configuration files
- Updates to Tailwind config for design tokens

##### 2. Add Core Components

```bash
pnpm dlx shadcn-svelte@latest add button
pnpm dlx shadcn-svelte@latest add input
pnpm dlx shadcn-svelte@latest add card
pnpm dlx shadcn-svelte@latest add table
pnpm dlx shadcn-svelte@latest add select
pnpm dlx shadcn-svelte@latest add dialog
pnpm dlx shadcn-svelte@latest add badge
pnpm dlx shadcn-svelte@latest add alert
pnpm dlx shadcn-svelte@latest add skeleton
pnpm dlx shadcn-svelte@latest add toast
pnpm dlx shadcn-svelte@latest add tabs
```

##### 3. Migrate Existing Components

| Current Pattern | Replace With | Affected Files |
|----------------|-------------|----------------|
| Hand-styled `<button>` elements | `<Button>` from shadcn | All pages |
| Hand-styled `<input>` elements | `<Input>` from shadcn | Login page, tracks search |
| Hand-styled card divs | `<Card>` from shadcn | Home page, track detail, search results |
| Hand-styled select dropdown | `<Select>` from shadcn | Search page mode selector |
| Pulse-animation skeleton divs | `<Skeleton>` from shadcn | All loading states |
| Red error alert divs | `<Alert>` from shadcn | All error states |
| Custom tab implementation | `<Tabs>` from shadcn | SearchResults component |
| Color-coded badges | `<Badge>` from shadcn | SearchResults confidence/similarity |

This is a refactoring task with no behavior changes. Each migration should be a separate commit.

##### 4. Design Tokens

**File to modify**: Tailwind config (via shadcn-svelte's theme configuration)

Establish consistent design tokens:
- **Colors**: Primary (indigo-600), success (green-600), warning (amber-500), error (red-600), surface (gray-50/white)
- **Spacing**: Use Tailwind's default scale consistently (4px base unit)
- **Typography**: Default sans-serif stack, heading sizes: h1=2xl, h2=xl, h3=lg
- **Border radius**: Use `rounded-lg` consistently (from shadcn defaults)

##### 5. Dark Mode (Optional)

If dark mode is desired:
- shadcn-svelte supports dark mode via CSS class strategy (`class="dark"`)
- Add a theme toggle in the NavBar utility section
- Store preference in `localStorage`
- Use `$effect` to apply/remove the `dark` class on `document.documentElement`

If dark mode is explicitly a non-goal, document this decision and ensure all color choices have sufficient contrast in light mode only.

##### 6. Leverage Zod for Form Validation

**Currently unused**: `zod` v4.3.6 is declared in package.json but never imported.

Use zod for runtime validation of:
- Login form fields (email format, password length)
- Ingest page file validation (complement existing client-side checks)
- Track library search input (sanitize special characters)
- API response validation (optional -- validate that API responses match expected schemas before rendering, catching contract violations at runtime)

**File to modify**: Pages with forms (`login/+page.svelte`, `admin/ingest/+page.svelte`, `tracks/+page.svelte`)

#### Files Summary (4D)

| Action | File | Notes |
|--------|------|-------|
| Create | `src/lib/components/ui/*` | shadcn-svelte components |
| Modify | Tailwind config | Design tokens |
| Modify | All pages and components | Migrate to shadcn primitives |
| Modify | `src/routes/+layout.svelte` | Dark mode toggle (optional) |
| Modify | Form pages | Add zod validation |

#### Acceptance Criteria (4D)

- [ ] shadcn-svelte initialized and `src/lib/components/ui/` directory exists
- [ ] Core shadcn components installed: Button, Input, Card, Table, Select, Dialog, Badge, Alert, Skeleton, Toast, Tabs
- [ ] All existing hand-styled buttons replaced with `<Button>` component
- [ ] All existing hand-styled cards replaced with `<Card>` component
- [ ] All loading skeletons use the `<Skeleton>` component
- [ ] All error alerts use the `<Alert>` component
- [ ] SearchResults tabs migrated to `<Tabs>` component (ARIA behavior preserved)
- [ ] Design tokens documented and applied consistently
- [ ] Zod used for form validation on at least one page
- [ ] (Optional) Dark mode toggle functional with persistent preference
- [ ] All existing tests still pass after migration

---

### 4E: Testing and Quality

**Priority within Phase 4**: HIGH (current test coverage is critically low: 3 tests total)
**Effort**: Large
**Can be done independently**: Yes (purely testing, no production code changes)
**Dependencies**: Best done AFTER 4D (testing shadcn components is easier than testing hand-styled HTML)

#### Objective

Establish comprehensive test coverage for all frontend components and pages. Leverage the already-installed but unused `@testing-library/svelte`. Add end-to-end tests with Playwright. Conduct an accessibility audit.

#### Tasks

##### 1. Component Tests with @testing-library/svelte

**Currently unused**: `@testing-library/svelte` v5.3.1 is declared in devDependencies but never imported.

Tests to create:

| Test File | Component | Key Scenarios |
|-----------|-----------|---------------|
| `tests/components/NavBar.test.ts` | NavBar | Active state for current route; all links render; mobile layout renders inline (not hamburger) for 2 items |
| `tests/components/HealthDot.test.ts` | HealthDot | Green when healthy; red when error; tooltip shows last check time |
| `tests/components/AudioRecorder.test.ts` | AudioRecorder | Mock MediaRecorder; verify level meter renders; verify minimum duration enforcement; verify cleanup on unmount |
| `tests/components/AudioUploader.test.ts` | AudioUploader | File validation (too large, wrong type); drag-and-drop events; selected file info display |
| `tests/components/SearchResults.test.ts` | SearchResults | Tab rendering with exact+vibe data; smart default tab selection (>85% confidence = exact tab); empty state; error state; keyboard navigation |
| `tests/components/Pagination.test.ts` | Pagination | Correct page numbers; disabled prev on first page; disabled next on last page; onClick fires with correct page |

Each test file should use the AAA pattern (Arrange-Act-Assert) and mock API calls via `vi.fn()`.

**Vitest config update** (if needed):

**File to modify**: `audio-ident-ui/vitest.config.ts` or `vite.config.ts`

Ensure the test environment is set to `jsdom` (already configured) and that `@testing-library/svelte` can resolve Svelte components correctly.

##### 2. Page Integration Tests

| Test File | Page | Key Scenarios |
|-----------|------|---------------|
| `tests/pages/home.test.ts` | `/` | Hero section renders; CTA links to /search; health polling works; status section shows/hides detail |
| `tests/pages/search.test.ts` | `/search` | State machine transitions (idle->recording->searching->results); mode selector works; cancel aborts request; error handling |
| `tests/pages/tracks.test.ts` | `/tracks` | Table renders with mock data; search filters; pagination controls; empty state |
| `tests/pages/trackDetail.test.ts` | `/tracks/[id]` | Metadata display; 404 handling; back navigation |

##### 3. E2E Tests with Playwright

**Package to install**: `@playwright/test`

**File to create**: `audio-ident-ui/playwright.config.ts`
**Directory to create**: `audio-ident-ui/e2e/`

E2E test scenarios:

| Test File | Flow | Notes |
|-----------|------|-------|
| `e2e/navigation.spec.ts` | Navigate all pages via nav bar | Verify all routes load without errors |
| `e2e/search-upload.spec.ts` | Upload a test audio file and view results | Use a small test fixture file |
| `e2e/tracks-browse.spec.ts` | Browse tracks, search, paginate, view detail | Requires seeded test data |
| `e2e/auth-flow.spec.ts` | Login, access ingest, logout, verify redirect | Requires Phase 4A |

E2E tests require the full stack running. Add a `make test-e2e` target that starts the backend + frontend in test mode and runs Playwright.

##### 4. Accessibility Audit

Use `axe-core` (via `@axe-core/playwright` for E2E or `vitest-axe` for component tests) to audit accessibility:

- All pages pass axe-core automated checks with zero critical violations
- Focus management: verify focus moves to results when search completes
- Keyboard navigation: verify all interactive elements are reachable via Tab
- Screen reader: verify `aria-live` regions announce search results
- Color contrast: verify all text meets WCAG 2.1 AA (4.5:1 ratio)

The existing SearchResults component already has good ARIA practices. The audit should verify these are maintained and extended to new components.

##### 5. Performance Testing (Core Web Vitals)

Use Lighthouse CI to measure:
- **LCP (Largest Contentful Paint)**: Target < 2.5s for home page
- **FID (First Input Delay)**: Target < 100ms
- **CLS (Cumulative Layout Shift)**: Target < 0.1

Add a `make lighthouse` target that runs Lighthouse against the dev server.

Key performance concerns:
- The AudioRecorder creates an `AnalyserNode` with real-time requestAnimationFrame updates. Verify this does not cause layout shifts or high CPU on mobile.
- The tracks table with 50+ rows should use virtualization if scroll performance degrades (consider `tanstack-virtual` if needed).

#### Files Summary (4E)

| Action | File | Notes |
|--------|------|-------|
| Create | `tests/components/*.test.ts` (6 files) | Component tests |
| Create | `tests/pages/*.test.ts` (4 files) | Page integration tests |
| Create | `e2e/*.spec.ts` (4 files) | E2E tests |
| Create | `playwright.config.ts` | Playwright configuration |
| Modify | `package.json` | Add Playwright devDependency, test scripts |
| Modify | `Makefile` | Add `test-e2e` and `lighthouse` targets |

#### Acceptance Criteria (4E)

- [ ] `@testing-library/svelte` actively used in component tests
- [ ] Component test coverage: NavBar, HealthDot, AudioRecorder, AudioUploader, SearchResults, Pagination
- [ ] Page integration tests for home, search, tracks, track detail
- [ ] At least 4 E2E tests passing with Playwright
- [ ] Zero critical axe-core accessibility violations
- [ ] Core Web Vitals meet targets (LCP < 2.5s, FID < 100ms, CLS < 0.1)
- [ ] `make test` runs all unit + component tests
- [ ] `make test-e2e` runs Playwright tests
- [ ] Test coverage report shows 70%+ for components

---

### 4F: Developer Experience

**Priority within Phase 4**: MEDIUM (reduces friction for future development)
**Effort**: Small-Medium
**Can be done independently**: Yes
**Dependencies**: Phase 2+ complete (backend endpoints must exist for type generation)

#### Objective

Fix technical debt: replace placeholder types with auto-generated ones, remove or leverage unused dependencies, add missing UX patterns (loading skeletons, error boundaries), and add SEO metadata to all pages.

#### Tasks

##### 1. Fix Type Generation

**Prerequisite**: Backend must be running with all endpoints registered (Phases 2-3 complete).

**Steps**:
1. Start the backend: `make dev`
2. Verify OpenAPI spec is complete: `curl http://localhost:17010/openapi.json | python -m json.tool`
3. Regenerate types: `make gen-client`
4. Verify generated types include: `TrackInfo`, `TrackDetail`, `ExactMatch`, `VibeMatch`, `SearchResponse`, `PaginatedResponse`, `IngestResponse`, `IngestReport`, `IngestError`, `ErrorResponse`
5. Remove all hand-written placeholder types from `generated.ts`
6. Update `client.ts` imports to use generated types exclusively
7. Run `pnpm check` to verify type compatibility

**File to modify**: `audio-ident-ui/src/lib/api/generated.ts` (regenerated, not manually edited)
**File to modify**: `audio-ident-ui/src/lib/api/client.ts` (update imports)

##### 2. Leverage or Remove Unused Dependencies

| Package | Current State | Action |
|---------|--------------|--------|
| `zod` | Declared, unused | USE IT (4D uses it for form validation; 4F uses it for API response validation) |
| `@testing-library/svelte` | Declared, unused | USE IT (4E uses it for component tests) |

Both dependencies have clear use cases in Phase 4. Do NOT remove them.

##### 3. Loading Skeleton Components

If shadcn-svelte is adopted (4D), use its `<Skeleton>` component. If not:

**File to create**: `audio-ident-ui/src/lib/components/Skeleton.svelte`

A reusable skeleton loader with configurable dimensions:
```svelte
<script lang="ts">
  let { width = '100%', height = '1rem', rounded = 'rounded' } = $props();
</script>
<div class="animate-pulse bg-gray-200 {rounded}" style="width: {width}; height: {height}" />
```

Use this in all pages to replace the current inline skeleton HTML (e.g., SearchResults already has pulse animation skeletons).

##### 4. Error Boundary Components

**File to create**: `audio-ident-ui/src/lib/components/ErrorBoundary.svelte`

A wrapper component that catches rendering errors and displays a fallback UI:
```svelte
<script lang="ts">
  import { onMount } from 'svelte';
  let { children, fallback } = $props();
  let error = $state<Error | null>(null);
</script>

{#if error}
  <!-- Render fallback with error message -->
{:else}
  {@render children()}
{/if}
```

Note: Svelte 5 does not have a built-in error boundary mechanism like React. This implementation uses a try/catch pattern with `$effect` for async errors. For synchronous rendering errors, SvelteKit's `+error.svelte` page handles them at the route level.

**File to create**: `audio-ident-ui/src/routes/+error.svelte`

A custom error page that displays friendly error messages instead of the default SvelteKit error page. Include:
- The error message
- A "Go home" button
- A "Try again" button (calls `invalidateAll()`)
- The HTTP status code in a subtle badge

##### 5. SEO Metadata Per Page

**File to modify**: Each page's `<svelte:head>` section.

| Route | Title | Description |
|-------|-------|-------------|
| `/` | `audio-ident - Audio Identification Service` | `Identify songs instantly using acoustic fingerprinting and AI similarity matching.` |
| `/search` | `Identify Audio - audio-ident` | `Record a clip or upload a file to find matching tracks.` |
| `/tracks` | `Track Library - audio-ident` | `Browse all ingested tracks in the audio identification library.` |
| `/tracks/[id]` | `{Track Title} - audio-ident` | `Detailed metadata for {Track Title} by {Artist}.` |
| `/admin/ingest` | `Ingest Audio - audio-ident` | `Add new tracks to the audio identification library.` |
| `/login` | `Login - audio-ident` | `Sign in to access admin features.` |

Use `<svelte:head>` with `<title>` and `<meta name="description">` tags. The track detail page should use the track title dynamically.

##### 6. Degraded State Behavior (SIG-2 Fix)

**File to create**: `audio-ident-ui/src/lib/components/DegradedBanner.svelte`

A non-blocking amber banner that appears below the nav bar when the backend health check fails:

```
+------------------------------------------------------------------+
| [!] Backend service unavailable. Some features may not work.  [x] |
+------------------------------------------------------------------+
```

Implementation:
- Subscribe to the health query from TanStack Query (already polling every 10s)
- When health fails, show the banner
- When health recovers, hide the banner automatically
- The `[x]` dismisses the banner for the current session
- Do NOT block navigation or disable features -- just inform the user

**File to modify**: `audio-ident-ui/src/routes/+layout.svelte`

Add `<DegradedBanner />` between the NavBar and the page content slot.

#### Files Summary (4F)

| Action | File | Notes |
|--------|------|-------|
| Regenerate | `src/lib/api/generated.ts` | Via `make gen-client` |
| Modify | `src/lib/api/client.ts` | Update imports to generated types |
| Create | `src/lib/components/Skeleton.svelte` | Only if not using shadcn |
| Create | `src/lib/components/ErrorBoundary.svelte` | Error wrapper |
| Create | `src/routes/+error.svelte` | Custom error page |
| Create | `src/lib/components/DegradedBanner.svelte` | Backend-down banner |
| Modify | `src/routes/+layout.svelte` | Add degraded banner |
| Modify | All page `<svelte:head>` sections | SEO metadata |

#### Acceptance Criteria (4F)

- [ ] `make gen-client` produces types for all backend endpoints
- [ ] Zero hand-written placeholder types remain in `generated.ts`
- [ ] `client.ts` uses only generated type imports
- [ ] `pnpm check` passes with zero type errors
- [ ] All pages have descriptive `<title>` and `<meta description>` tags
- [ ] Track detail page title includes the track name dynamically
- [ ] Custom `+error.svelte` page renders for 404s and other errors
- [ ] Degraded state banner appears when backend health check fails
- [ ] Degraded state banner auto-hides when backend recovers
- [ ] `zod` is actively used for at least one validation purpose
- [ ] `@testing-library/svelte` is actively used in at least one test

---

## 3. Implementation Order

### Dependency Graph

```
Phase 4A (Auth)         -- independent, but best done first to secure ingest
Phase 4B (Search UX)    -- fully independent, frontend-only
Phase 4C (Playback)     -- independent, needs contract update
Phase 4D (Components)   -- independent, but affects all other sub-phases
Phase 4E (Testing)      -- best done after 4D (test final components)
Phase 4F (DX)           -- best done after 4D (types + error handling)
```

### Recommended Sequence

```
     4A (Auth)
        |
        v
     4D (Components) -----> 4B (Search UX)
        |                      |
        v                      v
     4F (DX)              4C (Playback)
        |
        v
     4E (Testing)    <-- tests everything above
```

**Rationale**:
1. **4A first**: Secures the ingest endpoint. Can be done immediately after Phase 3.
2. **4D early**: Component library decisions affect all subsequent UI work. Doing 4D before 4B/4C/4F avoids building components that get rewritten.
3. **4B and 4C parallel**: These are independent and can be worked on simultaneously by different developers.
4. **4F after 4D**: Type generation and error handling benefit from having the component library established.
5. **4E last**: Testing should cover the final state of all components. Running tests too early means re-testing after refactors.

### Parallelization Opportunities

| Track 1 | Track 2 | Track 3 |
|---------|---------|---------|
| 4A (Auth) | 4B (Search UX) | -- |
| 4D (Components) | 4C (Playback) | -- |
| 4F (DX) | -- | -- |
| 4E (Testing) | -- | -- |

- 4A and 4B can start simultaneously (no dependencies on each other)
- 4D and 4C can start simultaneously after 4A completes
- 4E should be last to avoid re-testing

---

## 4. Effort and Priority Summary

| Sub-phase | Effort | Priority | Independent | Backend Changes | Contract Change |
|-----------|--------|----------|-------------|-----------------|-----------------|
| **4A: Auth** | Large | HIGH | Yes | New model, router, migration | Yes (v1.2.0) |
| **4B: Search UX** | Medium | MEDIUM | Yes | None | No |
| **4C: Playback** | Medium | MEDIUM | Yes | New streaming endpoint | Yes |
| **4D: Components** | Medium | MEDIUM | Yes | None | No |
| **4E: Testing** | Large | HIGH | Yes | None | No |
| **4F: DX** | Small-Medium | MEDIUM | Yes | None | No |

### If You Can Only Do Three

If time or capacity is limited, prioritize:

1. **4A (Auth)** -- Security gap is the highest-risk item. The ingest endpoint is currently unprotected.
2. **4E (Testing)** -- 3 tests for an entire frontend is a liability. Adding component tests catches regressions early.
3. **4F (DX)** -- Fixing type generation and adding the degraded state banner are small wins with high impact.

### If You Can Only Do One

Do **4A (Auth)**. An unprotected mutation endpoint is the only sub-phase that creates a real security vulnerability.

---

## 5. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **shadcn-svelte incompatibility with Svelte 5 runes** | Low | High | Test installation in a branch first. shadcn-svelte explicitly supports Svelte 5 as of early 2025. If issues arise, fall back to manual component extraction. |
| **Auth implementation delays Phase 4** | Medium | Medium | Auth is isolated. Other sub-phases can proceed without it. The ingest page was already accessible without auth in Phase 3. |
| **Type generation fails (backend cannot start)** | Medium | Medium | CLAUDE.md documents a fallback: commit `openapi.json` to the repo so types can be regenerated from the static file. Use this if the backend has startup issues. |
| **Audio streaming endpoint exposes copyrighted content** | High (in production) | High | Gate behind authentication (4A). In development, this is not a concern since the library contains test data. Document that production deployments MUST enable auth before 4C. |
| **Component library migration breaks existing behavior** | Low | Medium | Each shadcn migration should be a separate commit. Run existing tests after each migration. The 3 existing tests plus manual testing catch regressions. |
| **Playwright E2E tests are flaky** | Medium | Low | Use explicit waits, not timeouts. Run against a dedicated test database with seeded data. Retry failed tests once before marking as failed. |
| **Search history localStorage grows unbounded** | Low | Low | Cap at 20 entries (oldest removed). Each entry is ~200 bytes. 20 entries = 4KB. Not a concern. |
| **JWT in localStorage is vulnerable to XSS** | Medium | Medium | For audio-ident (a music identification tool, not a banking app), this is an acceptable trade-off. Document that production deployments with sensitive data should migrate to `httpOnly` cookies. |
| **CLAP model cold-start in production** | Low | Medium | The backend already does warm-up inference at startup. The 4B latency indicator handles the edge case where inference is slower than expected. |
| **Dark mode breaks existing Tailwind styles** | Medium | Low | If adopting dark mode, audit all hardcoded `bg-*` and `text-*` classes for `dark:` variants. This is a styling-only change with no functional risk. |

---

## 6. What to Defer Beyond Phase 4

These items are explicitly out of scope for Phase 4. They represent future product enhancements that should not be attempted until Phases 1-4 are complete and stable.

| Feature | Rationale for Deferral |
|---------|----------------------|
| **PWA / Offline Support** | Requires service worker complexity, offline audio search is not feasible (backend-dependent), and the app is not yet production-deployed. Revisit when there is a real mobile user base. |
| **Internationalization (i18n)** | The app has ~50 user-facing strings. i18n adds significant complexity for no current benefit. Defer until there is demand from non-English-speaking users. |
| **Analytics / Telemetry** | No production deployment exists. Adding analytics before deployment is premature. When deploying, consider privacy-respecting options like Plausible or PostHog. |
| **Batch Operations** | Batch delete, batch re-index, batch export. These are admin power-user features that the CLI already handles. Web UI for batch operations adds complexity without clear user demand. |
| **Public Sharing of Search Results** | Sharing a search result via URL requires persisting search state server-side (not just sessionStorage). This is a product feature, not a polish item. |
| **API Rate Limiting** | The backend does not yet have rate limiting middleware. This should be added when the app is deployed publicly, as part of a production hardening effort (separate from Phase 4). |
| **Track Deletion UI** | The backend supports `olaf_delete_track()` and `delete_track_embeddings()`. A `DELETE /api/v1/tracks/{id}` endpoint could be added, but it is a destructive operation that requires careful UX (confirmation, undo) and admin auth. Defer until auth (4A) is stable. |
| **WebSocket for Real-Time Ingestion Progress** | The ingest pipeline currently runs synchronously. Adding WebSocket-based progress updates requires significant backend refactoring (background tasks, progress reporting). The current synchronous approach with loading indicators is adequate. |
| **Advanced Search Filters** | Filtering by album, date range, format, indexing status. These are useful for large libraries but require backend query parameter extensions and contract updates. Defer until the library exceeds ~1000 tracks. |
| **User Management UI** | Creating, editing, and deleting users via the web UI. The `scripts/create_admin.py` CLI script handles this for now. A user management page is a Phase 5+ item. |

---

## 7. Contract Change Summary

Phase 4 requires the following API contract changes:

| Sub-phase | Contract Change | New Version |
|-----------|----------------|-------------|
| 4A (Auth) | Add `POST /api/v1/auth/token`, `POST /api/v1/auth/register`, `TokenResponse`, `UserCreate`, `UserResponse` schemas | 1.2.0 |
| 4C (Playback) | Add `GET /api/v1/tracks/{id}/audio` streaming endpoint | 1.2.0 or 1.3.0 |

If 4A and 4C are implemented in the same release cycle, bundle into a single contract bump (1.2.0). If implemented sequentially, use separate versions.

All other sub-phases (4B, 4D, 4E, 4F) require NO contract changes.

**Contract workflow reminder** (from CLAUDE.md):
1. Update `audio-ident-service/docs/api-contract.md`
2. Update backend Pydantic schemas
3. Backend tests pass
4. Copy to `audio-ident-ui/docs/api-contract.md`
5. Copy to `docs/api-contract.md`
6. Regenerate types: `make gen-client`
7. Update frontend code
8. Frontend tests pass

---

*Plan synthesized from: UI functionality inventory, backend API capabilities map, UX recommendations, devil's advocate review, CLAUDE.md conventions, and API contract v1.1.0. All file paths are verified against the actual repository structure.*
