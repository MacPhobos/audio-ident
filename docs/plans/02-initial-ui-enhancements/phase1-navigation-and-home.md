# Phase 1: Navigation + Home Page Redesign + Search Reachable

> **Date**: 2026-02-15
> **Scope**: Frontend-only changes -- ZERO backend modifications
> **Prerequisite**: None (no dependencies on other phases)
> **Input Documents**:
> - `docs/research/ui-functionality-inventory-2026-02-15.md`
> - `docs/research/backend-api-capabilities-2026-02-15.md`
> - `docs/research/ux-recommendations-2026-02-15.md`
> - `docs/research/ux-devils-advocate-review-2026-02-15.md`
> - `CLAUDE.md` (project conventions)

---

## 1. Objective

**Transform audio-ident from a hidden developer prototype into a navigable application where the primary feature (audio identification) is discoverable from the home page.**

Phase 1 delivers:
1. A global navigation bar visible on every page
2. A redesigned home page with a hero CTA that drives users to `/search`
3. The search page reachable via navigation (not just URL typing)
4. A compact, expandable health/version status section
5. Mobile-responsive layout without a hamburger menu

### Success Criteria (Measurable)

| # | Criterion | How to Verify |
|---|-----------|---------------|
| 1 | Clicking "Identify" in the nav bar navigates to `/search` | Manual + test |
| 2 | Clicking the hero CTA on `/` navigates to `/search` | Manual + test |
| 3 | The nav bar shows active state for the current route | Visual inspection on `/`, `/search` |
| 4 | Both nav items ("Identify" and "Library") are visible on mobile (no hamburger) | Resize to 375px width |
| 5 | Health/version status is visible in compact form and expandable to full detail | Click the status section |
| 6 | The search page no longer has a redundant back-link | Visual inspection |
| 7 | All interactive elements have ARIA attributes | Accessibility audit |
| 8 | `<title>` tags are correct on all pages | Check browser tab |
| 9 | All existing tests still pass | `pnpm test` |
| 10 | New NavBar tests pass | `pnpm test` |

---

## 2. Architecture Decisions

### AD-1: Use `$app/state` for Page Data (Svelte 5 Runes) -- NOT `$app/stores`

**Addresses**: BLOCK-3 from devil's advocate review.

The entire codebase uses Svelte 5 runes exclusively. There are zero store imports anywhere. The nav bar MUST use the Svelte 5 runes-compatible page access:

```typescript
import { page } from '$app/state';
// Access: page.url.pathname (no $ prefix)
```

Do NOT use:
```typescript
// WRONG - Svelte 4 compatibility layer, only store in the app
import { page } from '$app/stores';
```

### AD-2: No Hamburger Menu for Two Nav Items

**Addresses**: SIG-7 from devil's advocate review.

With only two primary nav items ("Identify" and "Library"), a hamburger menu adds unnecessary friction. Both items remain visible at all viewport sizes. The layout compresses gracefully:

- Desktop (>= 640px): Full text labels with comfortable spacing
- Mobile (< 640px): Smaller text and padding, all items still visible in a single row

A hamburger menu should only be introduced when nav items exceed three (Phase 3+, if an admin section is added).

### AD-3: Health Status is Compact but Expandable

**Addresses**: SIG-1 from devil's advocate review.

The current developer-focused health/version cards are valuable during development. Instead of removing them entirely, the home page will show a compact one-line status summary that expands on click to reveal the full health and version details. This preserves developer utility while making the page consumer-friendly.

### AD-4: CLAP Cold-Start Latency Acknowledged in Search UX

**Addresses**: SIG-6 from devil's advocate review.

Phase 1 does not change the search flow itself, but the search mode selector descriptions will be updated to hint at latency differences. The search page already displays `mode_used` in the results footer, which handles the case where `both` mode degrades to `exact` when CLAP is unavailable. No further Phase 1 changes are needed for this concern -- it will be fully addressed in Phase 2 with mode degradation notices.

### AD-5: Component Architecture

**New components:**
- `NavBar.svelte` -- Global navigation bar (in layout)

**Modified components:**
- `+layout.svelte` -- Add NavBar above page content
- `+page.svelte` (home) -- Complete redesign with hero, how-it-works, expandable status
- `search/+page.svelte` -- Remove redundant back-link

**NOT creating separate components for:**
- `HealthDot.svelte` -- The health indicator is simple enough to inline in NavBar. Extracting it adds indirection without reuse value in Phase 1. If Phase 2+ needs it elsewhere, extract at that time.
- `Skeleton.svelte`, `ErrorAlert.svelte` -- Not needed until Phase 2 introduces new pages.

### AD-6: State Management

All state uses Svelte 5 runes. No stores.

| Component | State | Type |
|-----------|-------|------|
| NavBar | `mobileMenuOpen` (if ever needed) | Not needed -- no hamburger menu |
| NavBar | `isSearchActive`, `isLibraryActive` | `$derived` from `page.url.pathname` |
| Home page | `healthQuery`, `versionQuery` | TanStack Query (unchanged) |
| Home page | `statusExpanded` | `$state(false)` -- toggles detail view |

### AD-7: No shadcn-svelte in Phase 1

**Addresses**: MIN-2 from devil's advocate review.

The devil's advocate correctly identifies a "maybe later" anti-pattern. Decision: shadcn-svelte is explicitly **deferred**. Rationale:

1. Phase 1 creates only one new component (NavBar) and modifies two pages. The Tailwind classes used are minimal.
2. Installing shadcn-svelte requires a `components.json` configuration, a `utils.ts` file, and adopting its class variance authority (cva) pattern -- overhead disproportionate to Phase 1 scope.
3. The existing components (AudioRecorder, AudioUploader, SearchResults) use raw Tailwind and would need migration.

If shadcn-svelte is adopted, it should happen as a dedicated migration effort, not mid-feature. All Phase 1 components use raw Tailwind CSS classes consistent with the existing codebase.

---

## 3. File-by-File Implementation Steps

### Overview of Files

| # | File | Action | Dependencies |
|---|------|--------|-------------|
| 1 | `src/lib/components/NavBar.svelte` | CREATE | None |
| 2 | `src/routes/+layout.svelte` | MODIFY | NavBar.svelte |
| 3 | `src/routes/+page.svelte` | MODIFY | None (uses existing client functions) |
| 4 | `src/routes/search/+page.svelte` | MODIFY | None |
| 5 | `src/app.css` | NO CHANGES | N/A |
| 6 | `tests/navbar.test.ts` | CREATE | NavBar.svelte |

---

### 3.1 `src/lib/components/NavBar.svelte` (NEW)

**Path**: `/Users/mac/workspace/audio-ident/audio-ident-ui/src/lib/components/NavBar.svelte`

**Purpose**: Global navigation bar displayed on every page via the root layout.

**What to implement**:

1. Import `page` from `$app/state` (Svelte 5 runes, NOT `$app/stores`)
2. Import `createQuery` from `@tanstack/svelte-query` for health polling
3. Import `fetchHealth` from `$lib/api/client`
4. Import icons from `lucide-svelte`: `Mic`, `Library` (or `Disc3`)
5. Derive active states from `page.url.pathname`
6. Render nav bar with logo, nav items, and health dot
7. All items visible on mobile -- no hamburger menu

**Props interface**: None. NavBar is self-contained (reads page state and health from context).

**State variables**:

```typescript
import { page } from '$app/state';
import { createQuery } from '@tanstack/svelte-query';
import { fetchHealth } from '$lib/api/client';

// Active route detection
let isSearchActive = $derived(page.url.pathname === '/search');
let isTracksActive = $derived(page.url.pathname.startsWith('/tracks'));

// Health status for the status dot
const healthQuery = createQuery<{ status: string; version: string }>(() => ({
  queryKey: ['health'],
  queryFn: fetchHealth,
  refetchInterval: 30_000 // 30s in nav (home page polls at 10s separately)
}));

let isHealthy = $derived(healthQuery.data?.status === 'ok');
let isHealthLoading = $derived(healthQuery.isLoading);
```

**Template structure** (exact implementation):

```svelte
<nav class="border-b border-gray-200 bg-white" aria-label="Main navigation">
  <div class="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
    <!-- Logo / Home link -->
    <a href="/" class="text-lg font-bold tracking-tight text-gray-900 sm:text-xl">
      audio-ident
    </a>

    <!-- Nav items (always visible, even on mobile) -->
    <div class="flex items-center gap-2 sm:gap-4">
      <!-- Identify (primary CTA) -->
      <a
        href="/search"
        class="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium
               transition-colors sm:px-4 sm:py-2 sm:text-sm
               {isSearchActive
                 ? 'bg-indigo-700 text-white'
                 : 'bg-indigo-600 text-white hover:bg-indigo-700'}"
        aria-current={isSearchActive ? 'page' : undefined}
      >
        <Mic class="h-3.5 w-3.5 sm:h-4 sm:w-4" />
        Identify
      </a>

      <!-- Library (secondary link) -->
      <a
        href="/tracks"
        class="inline-flex items-center gap-1.5 px-2 py-1.5 text-xs font-medium
               transition-colors sm:px-3 sm:text-sm
               {isTracksActive
                 ? 'text-gray-900'
                 : 'text-gray-500 hover:text-gray-900'}"
        aria-current={isTracksActive ? 'page' : undefined}
      >
        Library
      </a>

      <!-- Health status dot -->
      <div class="flex items-center" title={healthTooltip}>
        {#if isHealthLoading}
          <span class="block h-2 w-2 rounded-full bg-gray-300" aria-label="Checking service status"></span>
        {:else if isHealthy}
          <span class="block h-2 w-2 rounded-full bg-green-500" aria-label="Service healthy"></span>
        {:else}
          <span class="block h-2 w-2 rounded-full bg-red-500" aria-label="Service unreachable"></span>
        {/if}
      </div>
    </div>
  </div>
</nav>
```

**Computed tooltip**:

```typescript
let healthTooltip = $derived.by(() => {
  if (isHealthLoading) return 'Checking service status...';
  if (isHealthy) return `Service healthy (v${healthQuery.data?.version ?? '?'})`;
  return 'Service unreachable';
});
```

**Accessibility requirements**:
- `<nav aria-label="Main navigation">`
- `aria-current="page"` on the active nav link
- `aria-label` on the health dot `<span>` elements (for screen readers since the dot has no text)
- The `title` attribute on the health dot container provides tooltip on hover

**Responsive behavior**:
- Desktop (>= 640px / `sm:`): `text-sm`, `px-4 py-2` on Identify button, `gap-4` between items
- Mobile (< 640px): `text-xs`, `px-3 py-1.5` on Identify button, `gap-2` between items
- Logo reduces from `text-xl` to `text-lg` on mobile

**Edge cases**:
- Backend down: Health dot turns red, tooltip shows "Service unreachable"
- Health query loading: Health dot shows gray
- TanStack Query retry: The health query uses `retry: 1` from the global QueryClient config

**Key patterns to follow (from existing codebase)**:
- Use `$derived` for computed state (same as `SearchResults.svelte` lines 16-47)
- Use `$props()` destructuring pattern (same as `AudioRecorder.svelte` lines 5-13)
- Use TanStack Query `createQuery` (same as `+page.svelte` lines 11-20)
- Use lucide-svelte icons with `class="h-4 w-4"` sizing convention

---

### 3.2 `src/routes/+layout.svelte` (MODIFY)

**Path**: `/Users/mac/workspace/audio-ident/audio-ident-ui/src/routes/+layout.svelte`

**Current content** (22 lines): QueryClientProvider wrapping a bare div with `{@render children()}`.

**Changes needed**:
1. Import `NavBar` component
2. Add `<NavBar />` above the page content, inside the QueryClientProvider

**Full replacement content**:

```svelte
<script lang="ts">
  import '../app.css';
  import { QueryClient, QueryClientProvider } from '@tanstack/svelte-query';
  import NavBar from '$lib/components/NavBar.svelte';

  let { children } = $props();

  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: 1,
        staleTime: 30_000
      }
    }
  });
</script>

<QueryClientProvider client={queryClient}>
  <div class="min-h-screen bg-gray-50 text-gray-900">
    <NavBar />
    <main>
      {@render children()}
    </main>
  </div>
</QueryClientProvider>
```

**Key changes**:
1. Added `import NavBar` (line 4)
2. Added `<NavBar />` before `{@render children()}` (line 20)
3. Wrapped `{@render children()}` in `<main>` for semantic HTML
4. No other changes -- QueryClient config and outer div are unchanged

**Dependencies**: `NavBar.svelte` must be created first.

**Note**: The `<main>` tag is added here in the layout. This means individual pages should NOT wrap their content in `<main>` -- they are already inside one. The home page currently uses `<main>` and the search page uses `<main>`. Both need to be changed to `<div>` or a `<section>` in their respective modifications (Steps 3.3 and 3.4).

---

### 3.3 `src/routes/+page.svelte` (MODIFY -- Complete Redesign)

**Path**: `/Users/mac/workspace/audio-ident/audio-ident-ui/src/routes/+page.svelte`

**Current content** (122 lines): Health dashboard with two large cards and a connection summary banner.

**Changes needed**: Complete rewrite. New page has three sections:
1. **Hero section** -- heading, subtitle, CTA button to `/search`
2. **"How it works" section** -- three-column feature cards (Record/Upload, Fingerprint Match, Vibe Match)
3. **Compact expandable status section** -- one-line summary that expands to show full health/version details

**Script block**:

```typescript
import { createQuery } from '@tanstack/svelte-query';
import {
  fetchHealth,
  fetchVersion,
  type HealthResponse,
  type VersionResponse
} from '$lib/api/client';
import { Mic, Fingerprint, Waves, ChevronDown, ChevronUp } from 'lucide-svelte';

const healthQuery = createQuery<HealthResponse>(() => ({
  queryKey: ['health'],
  queryFn: fetchHealth,
  refetchInterval: 10_000
}));

const versionQuery = createQuery<VersionResponse>(() => ({
  queryKey: ['version'],
  queryFn: fetchVersion
}));

let statusExpanded = $state(false);

let isHealthy = $derived(healthQuery.data?.status === 'ok');
let isLoading = $derived(healthQuery.isLoading || versionQuery.isLoading);
let hasError = $derived(healthQuery.isError || versionQuery.isError);
```

**Template structure** (Section 1 -- Hero):

```svelte
<svelte:head>
  <title>audio-ident</title>
</svelte:head>

<div class="mx-auto max-w-4xl px-4 py-12 sm:py-20">
  <!-- Hero Section -->
  <section class="mb-16 text-center sm:mb-20">
    <h1 class="mb-4 text-4xl font-bold tracking-tight text-gray-900 sm:text-5xl">
      Identify Any Song
    </h1>
    <p class="mx-auto mb-8 max-w-xl text-lg text-gray-500">
      Record a clip or upload a file to find matching tracks
      using acoustic fingerprinting and AI similarity.
    </p>
    <a
      href="/search"
      class="inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-6 py-3
             text-base font-semibold text-white shadow-sm transition-colors
             hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500
             focus:ring-offset-2 sm:px-8 sm:py-4 sm:text-lg"
    >
      <Mic class="h-5 w-5" />
      Start Identifying
    </a>
  </section>
```

**Template structure** (Section 2 -- How It Works):

```svelte
  <!-- How It Works -->
  <section class="mb-16 sm:mb-20">
    <h2 class="mb-8 text-center text-xl font-semibold text-gray-900 sm:text-2xl">
      How it works
    </h2>
    <div class="grid gap-6 sm:grid-cols-3 sm:gap-8">
      <!-- Card 1: Record or Upload -->
      <div class="rounded-xl border bg-white p-6 text-center">
        <div class="mx-auto mb-4 flex h-12 w-12 items-center justify-center
                    rounded-full bg-indigo-50 text-indigo-600">
          <Mic class="h-6 w-6" />
        </div>
        <h3 class="mb-2 font-semibold text-gray-900">Record or Upload</h3>
        <p class="text-sm text-gray-500">
          Capture audio from your microphone or upload an audio file to identify.
        </p>
      </div>

      <!-- Card 2: Fingerprint Match -->
      <div class="rounded-xl border bg-white p-6 text-center">
        <div class="mx-auto mb-4 flex h-12 w-12 items-center justify-center
                    rounded-full bg-green-50 text-green-600">
          <Fingerprint class="h-6 w-6" />
        </div>
        <h3 class="mb-2 font-semibold text-gray-900">Fingerprint Match</h3>
        <p class="text-sm text-gray-500">
          Exact acoustic identification finds the precise track, like Shazam.
        </p>
      </div>

      <!-- Card 3: Vibe Match -->
      <div class="rounded-xl border bg-white p-6 text-center">
        <div class="mx-auto mb-4 flex h-12 w-12 items-center justify-center
                    rounded-full bg-purple-50 text-purple-600">
          <Waves class="h-6 w-6" />
        </div>
        <h3 class="mb-2 font-semibold text-gray-900">Vibe Match</h3>
        <p class="text-sm text-gray-500">
          AI-powered similarity finds tracks that sound alike, even without an exact match.
        </p>
      </div>
    </div>
  </section>
```

**Template structure** (Section 3 -- Compact Expandable Status):

```svelte
  <!-- System Status (compact, expandable) -->
  <section class="rounded-xl border bg-white">
    <button
      onclick={() => statusExpanded = !statusExpanded}
      class="flex w-full items-center justify-between px-6 py-4 text-left
             transition-colors hover:bg-gray-50"
      aria-expanded={statusExpanded}
      aria-controls="status-detail"
    >
      <div class="flex items-center gap-3">
        {#if isLoading}
          <span class="block h-2.5 w-2.5 rounded-full bg-gray-300"></span>
          <span class="text-sm text-gray-500">Checking service status...</span>
        {:else if hasError}
          <span class="block h-2.5 w-2.5 rounded-full bg-red-500"></span>
          <span class="text-sm text-red-600">
            Backend service is not responding
          </span>
        {:else}
          <span class="block h-2.5 w-2.5 rounded-full bg-green-500"></span>
          <span class="text-sm text-gray-600">
            Service healthy
            {#if versionQuery.data}
              — v{versionQuery.data.version}
              ({versionQuery.data.git_sha})
            {/if}
          </span>
        {/if}
      </div>
      {#if statusExpanded}
        <ChevronUp class="h-4 w-4 text-gray-400" />
      {:else}
        <ChevronDown class="h-4 w-4 text-gray-400" />
      {/if}
    </button>

    {#if statusExpanded}
      <div id="status-detail" class="border-t px-6 py-4">
        <div class="grid gap-6 sm:grid-cols-2">
          <!-- Health Detail -->
          <div>
            <h3 class="mb-3 text-sm font-semibold text-gray-900">Service Health</h3>
            {#if healthQuery.isLoading}
              <p class="text-sm text-gray-400">Checking...</p>
            {:else if healthQuery.isError}
              <p class="text-sm text-red-600">
                {healthQuery.error?.message ?? 'Could not connect to backend.'}
              </p>
            {:else if healthQuery.data}
              <dl class="grid grid-cols-2 gap-1 text-sm text-gray-600">
                <dt class="font-medium">Status</dt>
                <dd>{healthQuery.data.status}</dd>
                <dt class="font-medium">Version</dt>
                <dd class="font-mono">{healthQuery.data.version}</dd>
              </dl>
            {/if}
          </div>

          <!-- Version Detail -->
          <div>
            <h3 class="mb-3 text-sm font-semibold text-gray-900">Version Info</h3>
            {#if versionQuery.isLoading}
              <p class="text-sm text-gray-400">Loading...</p>
            {:else if versionQuery.isError}
              <p class="text-sm text-red-600">Could not fetch version info.</p>
            {:else if versionQuery.data}
              <dl class="grid grid-cols-2 gap-1 text-sm text-gray-600">
                <dt class="font-medium">Name</dt>
                <dd>{versionQuery.data.name}</dd>
                <dt class="font-medium">Version</dt>
                <dd class="font-mono">{versionQuery.data.version}</dd>
                <dt class="font-medium">Git SHA</dt>
                <dd class="font-mono">{versionQuery.data.git_sha}</dd>
                <dt class="font-medium">Build Time</dt>
                <dd>{versionQuery.data.build_time}</dd>
              </dl>
            {/if}
          </div>
        </div>
      </div>
    {/if}
  </section>
</div>
```

**What was removed from the current home page**:
- The two large standalone health/version cards (replaced by compact expandable section)
- The full-width connection summary banner at the bottom
- The `Activity`, `CheckCircle`, `XCircle`, `Info` icon imports (replaced by `Mic`, `Fingerprint`, `Waves`, `ChevronDown`, `ChevronUp`)

**What was preserved**:
- TanStack Query health polling at 10s interval (unchanged)
- TanStack Query version fetch (unchanged)
- All health/version data is still accessible (via expand)
- Error handling for both queries

**Important**: Change `<main>` to `<div>` since the layout now provides `<main>`. The outermost element of this page should be `<div class="mx-auto ...">`, NOT `<main>`.

**Accessibility**:
- `aria-expanded` on the status toggle button
- `aria-controls="status-detail"` linking button to expanded content
- The CTA link is keyboard-focusable and has focus ring styles

**Edge cases**:
- Backend down: Status section shows red dot + "Backend service is not responding". The hero CTA still works (users can still navigate to `/search`; the search itself will fail with a clear error from the existing error handling).
- Backend loading: Status shows gray dot + "Checking service status..."

---

### 3.4 `src/routes/search/+page.svelte` (MODIFY)

**Path**: `/Users/mac/workspace/audio-ident/audio-ident-ui/src/routes/search/+page.svelte`

**Changes needed**:
1. Remove the back-link (`<a href="/">Home</a>` with ArrowLeft icon) -- the NavBar provides navigation now
2. Remove the `ArrowLeft` import from lucide-svelte
3. Change `<main>` to `<div>` since the layout now provides `<main>`

**Specific edits**:

**Edit 1**: Remove ArrowLeft from imports (line 8).

Current:
```typescript
import { Mic, Upload, Lightbulb, ArrowLeft } from 'lucide-svelte';
```

New:
```typescript
import { Mic, Upload, Lightbulb } from 'lucide-svelte';
```

**Edit 2**: Remove the back-link from the header (lines 98-104).

Current:
```svelte
<header class="mb-8 text-center">
  <a
    href="/"
    class="mb-4 inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700"
  >
    <ArrowLeft class="h-4 w-4" />
    Home
  </a>
  <h1 class="text-3xl font-bold tracking-tight sm:text-4xl">Identify Audio</h1>
```

New:
```svelte
<header class="mb-8 text-center">
  <h1 class="text-3xl font-bold tracking-tight sm:text-4xl">Identify Audio</h1>
```

**Edit 3**: Change `<main>` to `<div>` (line 95).

Current:
```svelte
<main class="mx-auto max-w-2xl px-4 py-8 sm:py-12">
```

New:
```svelte
<div class="mx-auto max-w-2xl px-4 py-8 sm:py-12">
```

And the closing tag (last line):
```svelte
</main>
```
becomes:
```svelte
</div>
```

**No other changes to search page in Phase 1.** The recording flow, upload flow, mutation handling, results display, and mode selector all remain exactly as they are.

---

### 3.5 `src/app.css` (NO CHANGES)

**Path**: `/Users/mac/workspace/audio-ident/audio-ident-ui/src/app.css`

Current content is a single line: `@import 'tailwindcss';`

No changes needed. All styling is done via Tailwind utility classes inline in components.

---

### 3.6 `tests/navbar.test.ts` (NEW)

**Path**: `/Users/mac/workspace/audio-ident/audio-ident-ui/tests/navbar.test.ts`

**Purpose**: Test the NavBar component's active state logic and health status rendering.

**What to test**:

1. **Active state detection**: Verify that `isSearchActive` is true when pathname is `/search`
2. **Active state detection**: Verify that `isTracksActive` is true when pathname starts with `/tracks`
3. **Health dot states**: Verify the correct aria-label and color for healthy/unhealthy/loading states

**Note on testing approach**: Since the codebase does not yet use `@testing-library/svelte` for component rendering tests (the existing test file `health.test.ts` tests pure functions with `vi.stubGlobal`), and setting up Svelte component rendering tests requires additional configuration that is out of scope for Phase 1, the NavBar test should follow the existing pattern: test the **logic** (active state derivation) as pure functions, not the rendered DOM.

Extract the active-state logic into a testable utility function:

**Create a small utility** (tested independently):

```typescript
// In the test file, test the logic directly:
import { describe, it, expect } from 'vitest';

describe('NavBar active state logic', () => {
  function isSearchActive(pathname: string): boolean {
    return pathname === '/search';
  }

  function isTracksActive(pathname: string): boolean {
    return pathname.startsWith('/tracks');
  }

  it('isSearchActive returns true for /search', () => {
    expect(isSearchActive('/search')).toBe(true);
  });

  it('isSearchActive returns false for /', () => {
    expect(isSearchActive('/')).toBe(false);
  });

  it('isSearchActive returns false for /tracks', () => {
    expect(isSearchActive('/tracks')).toBe(false);
  });

  it('isTracksActive returns true for /tracks', () => {
    expect(isTracksActive('/tracks')).toBe(true);
  });

  it('isTracksActive returns true for /tracks/some-id', () => {
    expect(isTracksActive('/tracks/some-id')).toBe(true);
  });

  it('isTracksActive returns false for /search', () => {
    expect(isTracksActive('/search')).toBe(false);
  });

  it('isTracksActive returns false for /', () => {
    expect(isTracksActive('/')).toBe(false);
  });
});
```

**Why this approach**: The existing test infrastructure tests API client functions as pure TypeScript. Component rendering tests (using `@testing-library/svelte`) require mocking `$app/state`, `@tanstack/svelte-query`, and other SvelteKit internals, which is a nontrivial setup task better suited as a dedicated initiative. The active-state logic is the most important correctness concern in the NavBar and can be tested as pure functions.

---

## 4. Component Specifications

### 4.1 NavBar.svelte

| Property | Value |
|----------|-------|
| **Props** | None |
| **State** | None (all derived) |
| **Derived** | `isSearchActive`, `isTracksActive`, `isHealthy`, `isHealthLoading`, `healthTooltip` |
| **Events** | None |
| **Queries** | `healthQuery` (TanStack Query, 30s refetch) |

**Props interface**: No props. The component is self-contained.

```typescript
// No $props() call needed
```

**State variables**:

```typescript
// All are $derived -- no $state needed in NavBar
let isSearchActive = $derived(page.url.pathname === '/search');
let isTracksActive = $derived(page.url.pathname.startsWith('/tracks'));
let isHealthy = $derived(healthQuery.data?.status === 'ok');
let isHealthLoading = $derived(healthQuery.isLoading);
let healthTooltip = $derived.by(() => {
  if (isHealthLoading) return 'Checking service status...';
  if (isHealthy) return `Service healthy (v${healthQuery.data?.version ?? '?'})`;
  return 'Service unreachable';
});
```

**Accessibility**:
- `<nav aria-label="Main navigation">`
- `aria-current="page"` on active links
- `aria-label` on health dot spans (screen reader text for color-only indicators)
- Keyboard navigable: all links are standard `<a>` tags

**Responsive behavior**:

| Breakpoint | Logo | Identify Button | Library Link | Health Dot |
|------------|------|----------------|--------------|------------|
| < 640px | `text-lg` | `text-xs px-3 py-1.5` | `text-xs px-2` | `h-2 w-2` |
| >= 640px | `text-xl` | `text-sm px-4 py-2` | `text-sm px-3` | `h-2 w-2` |

**Edge cases**:
- Health query fails: Red dot, tooltip "Service unreachable"
- Health query loading on first render: Gray dot, tooltip "Checking service status..."
- Unknown route (404): No nav item highlighted (both `isSearchActive` and `isTracksActive` are false)

### 4.2 Home Page (`+page.svelte`)

| Property | Value |
|----------|-------|
| **Props** | None (page component) |
| **State** | `statusExpanded: $state(false)` |
| **Derived** | `isHealthy`, `isLoading`, `hasError` |
| **Events** | Click on status toggle |
| **Queries** | `healthQuery` (10s refetch), `versionQuery` (single fetch) |

**State variables**:

```typescript
let statusExpanded = $state(false);
let isHealthy = $derived(healthQuery.data?.status === 'ok');
let isLoading = $derived(healthQuery.isLoading || versionQuery.isLoading);
let hasError = $derived(healthQuery.isError || versionQuery.isError);
```

**Accessibility**:
- `aria-expanded` on the status toggle button
- `aria-controls="status-detail"` linking to the expanded detail section
- CTA button has visible focus ring (`focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2`)

**Responsive behavior**:
- Hero: heading scales from `text-4xl` to `text-5xl` at `sm:`
- How-it-works: stacks vertically on mobile, 3-column grid at `sm:`
- Status section: full-width at all sizes; detail grid is 1-col on mobile, 2-col at `sm:`

**Edge cases**:
- Backend down: Red dot in status bar, error messages in expanded detail. Hero CTA still works.
- Version query pending: Status shows "Checking service status..." until resolved.
- Both queries fail: Status shows "Backend service is not responding" in red.

---

## 5. Navigation Design

### Nav Items and Routes

| Nav Item | Route | Style | Icon |
|----------|-------|-------|------|
| audio-ident (logo) | `/` | Text link, bold | None |
| Identify | `/search` | Filled button (indigo bg, white text) | `Mic` |
| Library | `/tracks` | Text link | None |
| Health dot | N/A (not clickable in Phase 1) | Colored circle | None |

### Active State Detection

```typescript
import { page } from '$app/state';

// Exact match for /search
let isSearchActive = $derived(page.url.pathname === '/search');

// Prefix match for /tracks and /tracks/[id]
let isTracksActive = $derived(page.url.pathname.startsWith('/tracks'));
```

**Active state visual treatment**:
- "Identify" button: `bg-indigo-700` when active (slightly darker), `bg-indigo-600` when inactive
- "Library" link: `text-gray-900` when active, `text-gray-500` when inactive
- `aria-current="page"` is set on the active link for accessibility

**Note**: `/` (home) has no nav item highlight. The logo links to home but does not get an active indicator.

### Mobile Layout

```
+----------------------------------------------------+
| audio-ident     [Identify] [Library]            [*] |
+----------------------------------------------------+
```

- All items remain in a single row
- "audio-ident" text shrinks from `text-xl` to `text-lg`
- "Identify" button padding reduces: `px-3 py-1.5 text-xs`
- "Library" text reduces: `text-xs px-2`
- Gap between items reduces: `gap-2`
- No hamburger menu, no hidden items

### Health Status Indicator

- **Location**: Right side of nav bar
- **Appearance**: 8px (`h-2 w-2`) colored circle
- **Colors**: Green (`bg-green-500`) when healthy, Red (`bg-red-500`) when error, Gray (`bg-gray-300`) when loading
- **Interaction**: `title` attribute shows tooltip on hover (not clickable in Phase 1; clickable link to status page is a Phase 4 enhancement)
- **Polling**: 30-second interval via TanStack Query (separate from the home page's 10-second poll; TanStack Query deduplicates since they share the `['health']` query key -- the shortest interval wins)

---

## 6. Home Page Redesign

### Section 1: Hero

- **Heading**: "Identify Any Song"
- **Subtitle**: "Record a clip or upload a file to find matching tracks using acoustic fingerprinting and AI similarity."
- **CTA Button**: "Start Identifying" with Mic icon, links to `/search`
- **CTA Style**: Large, indigo, prominent -- the visual focal point of the page
- **Vertical spacing**: `py-12 sm:py-20` (generous whitespace above and below)

### Section 2: How It Works

Three cards in a responsive grid explaining the dual-mode search in user-friendly terms:

1. **Record or Upload** (Mic icon, indigo accent): "Capture audio from your microphone or upload an audio file to identify."
2. **Fingerprint Match** (Fingerprint icon, green accent): "Exact acoustic identification finds the precise track, like Shazam."
3. **Vibe Match** (Waves icon, purple accent): "AI-powered similarity finds tracks that sound alike, even without an exact match."

Icons reuse existing `lucide-svelte` imports already in the project. Color accents use Tailwind's standard palette (indigo, green, purple).

### Section 3: Compact Expandable Status

**Collapsed state** (default):
```
[green dot]  Service healthy -- v0.1.0 (3d27f6b)    [v]
```

**Expanded state** (on click):
```
[green dot]  Service healthy -- v0.1.0 (3d27f6b)    [^]
+----------------------------------------------------+
| Service Health          | Version Info              |
|-------------------------|---------------------------|
| Status: ok              | Name: audio-ident-service |
| Version: 0.1.0          | Version: 0.1.0            |
|                         | Git SHA: 3d27f6b          |
|                         | Build Time: unknown       |
+----------------------------------------------------+
```

**When backend is down** (collapsed):
```
[red dot]  Backend service is not responding          [v]
```

**When backend is down** (expanded):
Shows the error message from `healthQuery.error?.message` and "Could not fetch version info" for the version section.

---

## 7. Testing Strategy

### Tests to Write

| File | Test Count | What is Tested |
|------|-----------|----------------|
| `tests/navbar.test.ts` | 7 | Active state logic (isSearchActive, isTracksActive) for various pathnames |
| Existing `tests/health.test.ts` | 3 (unchanged) | API client functions |

### What to Test Per Component

**NavBar (logic tests)**:
- `isSearchActive('/search')` returns `true`
- `isSearchActive('/')` returns `false`
- `isSearchActive('/tracks')` returns `false`
- `isTracksActive('/tracks')` returns `true`
- `isTracksActive('/tracks/abc-123')` returns `true`
- `isTracksActive('/')` returns `false`
- `isTracksActive('/search')` returns `false`

**Why not component rendering tests**: Setting up `@testing-library/svelte` with mocked `$app/state` and `@tanstack/svelte-query` context requires configuration work (mocking SvelteKit module internals). This is tracked as a follow-up for Phase 2+ when more components need testing. The NavBar's critical logic (active state) is testable as pure functions.

### Accessibility Testing

Manual checks (not automated in Phase 1):

1. **Tab order**: Press Tab through the nav bar -- should focus: logo link, Identify link, Library link (health dot is not focusable).
2. **Screen reader**: VoiceOver/NVDA should announce "Main navigation" landmark, active page via `aria-current`.
3. **Color contrast**: Indigo-600 on white exceeds WCAG AA. Green/red dots have text aria-labels as backup.
4. **Keyboard**: All links are standard `<a>` tags, activatable with Enter.
5. **Status section**: Button is keyboard-focusable, `aria-expanded` announces expand/collapse state.

---

## 8. Implementation Order

Execute these steps in this exact order. Each step lists when to verify.

### Step 1: Create `NavBar.svelte`

**File**: `/Users/mac/workspace/audio-ident/audio-ident-ui/src/lib/components/NavBar.svelte`

**Action**: Create the full NavBar component as specified in Section 3.1.

**Verify**: File exists, no TypeScript errors (`pnpm check` should pass with the new file, though it will not render until Step 2).

### Step 2: Modify `+layout.svelte`

**File**: `/Users/mac/workspace/audio-ident/audio-ident-ui/src/routes/+layout.svelte`

**Action**: Import NavBar and add it above page content, wrap children in `<main>`, as specified in Section 3.2.

**Verify**: Run `pnpm dev` and open `http://localhost:17000`. The NavBar should appear at the top of both `/` and `/search`. Click "Identify" to navigate to `/search`. Click the logo to navigate to `/`.

### Step 3: Modify `search/+page.svelte`

**File**: `/Users/mac/workspace/audio-ident/audio-ident-ui/src/routes/search/+page.svelte`

**Action**: Remove back-link, remove ArrowLeft import, change `<main>` to `<div>`, as specified in Section 3.4.

**Verify**: Navigate to `/search`. The back-link should be gone. The page should still function (recording, uploading, searching should all work). The nav bar provides navigation back to home.

### Step 4: Redesign `+page.svelte` (Home)

**File**: `/Users/mac/workspace/audio-ident/audio-ident-ui/src/routes/+page.svelte`

**Action**: Complete rewrite as specified in Section 3.3. Replace the health dashboard with hero + how-it-works + compact expandable status.

**Verify**:
1. Navigate to `http://localhost:17000/`. The hero section should appear with "Identify Any Song" heading and CTA button.
2. Click the CTA button -- should navigate to `/search`.
3. Scroll down to "How it works" -- three feature cards should be visible.
4. Scroll to status section -- compact status line should show health status.
5. Click the status bar -- full health/version details should expand.
6. Click again -- details should collapse.
7. Resize browser to mobile width (375px) -- hero, cards, and status should stack vertically.

### Step 5: Write NavBar Tests

**File**: `/Users/mac/workspace/audio-ident/audio-ident-ui/tests/navbar.test.ts`

**Action**: Create tests as specified in Section 3.6.

**Verify**: Run `pnpm test`. All 7 new tests and all 3 existing tests should pass (10 total).

### Step 6: Run Full Verification

Run all verification commands:

```bash
cd audio-ident-ui
pnpm test          # All tests pass
pnpm check         # Type-check passes
pnpm lint          # Linting passes
pnpm dev           # Manual verification in browser
```

**Browser verification checklist**:
- [ ] `/` shows hero section with CTA
- [ ] `/` shows "How it works" section with three cards
- [ ] `/` shows compact status section
- [ ] Status section expands/collapses on click
- [ ] NavBar visible on `/` and `/search`
- [ ] "Identify" nav item links to `/search`
- [ ] "Library" nav item links to `/tracks` (will show 404 -- expected, as tracks page is Phase 2)
- [ ] Logo links to `/`
- [ ] Health dot shows correct color (green if backend running, red if not)
- [ ] `/search` no longer shows back-link
- [ ] `/search` recording flow still works
- [ ] `/search` upload flow still works
- [ ] Active nav item is highlighted on `/search`
- [ ] Mobile (375px width) -- all nav items visible in single row
- [ ] Mobile -- hero, cards, status stack vertically

---

## 9. Acceptance Criteria

### Functional

| # | Criterion | Verification |
|---|-----------|-------------|
| AC-1 | NavBar appears on every page | Load `/`, `/search`, any other URL |
| AC-2 | "Identify" link navigates to `/search` | Click nav item |
| AC-3 | "Library" link navigates to `/tracks` | Click nav item (404 is expected for Phase 1) |
| AC-4 | Logo link navigates to `/` | Click logo text |
| AC-5 | Hero CTA button navigates to `/search` | Click "Start Identifying" |
| AC-6 | Health dot is green when backend is running | Start backend, check nav |
| AC-7 | Health dot is red when backend is down | Stop backend, wait for health check failure |
| AC-8 | Status section expands on click | Click compact status on home page |
| AC-9 | Status section collapses on second click | Click again |
| AC-10 | Expanded status shows health + version detail | Verify data matches backend |
| AC-11 | Back-link removed from search page | Load `/search`, verify no "Home" link in header |
| AC-12 | Search page recording flow works | Record audio, verify search executes |
| AC-13 | Search page upload flow works | Upload file, verify search executes |

### Visual / Responsive

| # | Criterion | Verification |
|---|-----------|-------------|
| AC-14 | All nav items visible on mobile (no hamburger) | Set viewport to 375px width |
| AC-15 | "How it works" cards stack vertically on mobile | Set viewport to 375px |
| AC-16 | "How it works" cards show as 3-column grid on desktop | Set viewport to 1024px+ |
| AC-17 | CTA button is large and prominent | Visual inspection |

### Accessibility

| # | Criterion | Verification |
|---|-----------|-------------|
| AC-18 | NavBar has `aria-label="Main navigation"` | Inspect DOM |
| AC-19 | Active nav item has `aria-current="page"` | Inspect DOM on `/search` |
| AC-20 | Health dots have `aria-label` text | Inspect DOM |
| AC-21 | Status toggle has `aria-expanded` | Inspect DOM |
| AC-22 | All links keyboard-navigable | Tab through page |

### Code Quality

| # | Criterion | Verification |
|---|-----------|-------------|
| AC-23 | `pnpm test` passes (10 tests: 3 existing + 7 new) | Run command |
| AC-24 | `pnpm check` passes (no type errors) | Run command |
| AC-25 | `pnpm lint` passes | Run command |
| AC-26 | No `$app/stores` imports anywhere | `grep -r '$app/stores' src/` returns nothing |
| AC-27 | No `<main>` tags in individual pages | `grep -r '<main' src/routes/` returns nothing (only in layout) |
| AC-28 | `<title>` tag present on both pages | Check `<svelte:head>` in `+page.svelte` and `search/+page.svelte` |

---

## 10. Risks and Mitigations

### Risk 1: TanStack Query Health Polling Conflict

**Risk**: Both NavBar (30s) and home page (10s) create `healthQuery` with the same `['health']` query key. Could this cause duplicate polling or stale data conflicts?

**Mitigation**: TanStack Query automatically deduplicates queries with the same key. When both components are mounted (on the home page), the shortest `refetchInterval` (10s) wins. When navigating away from home, only the NavBar's 30s interval remains active. This is the correct and expected behavior -- no action needed.

### Risk 2: Library Link Leads to 404

**Risk**: The NavBar includes a "Library" link to `/tracks`, which does not exist until Phase 2. Users clicking it will see a 404 page.

**Mitigation**: This is acceptable for Phase 1. SvelteKit shows a default "Not Found" page for unmatched routes. The "Library" link establishes the nav structure that Phase 2 will populate. Alternative: hide the Library link behind a feature flag, but this adds complexity for zero user benefit (the current audience is developers who understand the phased approach).

### Risk 3: SSR Compatibility

**Risk**: `$app/state` is imported in NavBar. If any code inside NavBar accesses browser-only APIs (like `window`), it will crash during SSR.

**Mitigation**: NavBar uses only `page.url.pathname` from `$app/state` (available in SSR) and TanStack Query (SSR-compatible). There are no `window`, `document`, or `navigator` references. No `onMount` or browser-only guards are needed.

### Risk 4: Tailwind v4 Class Compatibility

**Risk**: The project uses Tailwind CSS v4 (imported via `@import 'tailwindcss'` in `app.css`). Some Tailwind v3 classes may not work identically in v4.

**Mitigation**: All classes used in this plan (`rounded-xl`, `border`, `bg-white`, `text-indigo-600`, `px-4`, `py-3`, `grid`, `gap-6`, `sm:grid-cols-3`, etc.) are standard utility classes that work identically in Tailwind v4. The existing codebase already uses these same classes successfully. No v4-specific syntax is needed.

### Risk 5: Status Section UX on Mobile

**Risk**: The expandable status section uses a `<button>` that spans the full width. On mobile, the touch target may feel too large or the expand/collapse interaction may not be obvious.

**Mitigation**: The chevron icon (ChevronDown/ChevronUp) provides a visual affordance for the toggle. The `hover:bg-gray-50` transition gives feedback on desktop. On mobile, the full-width tap target is actually beneficial (large touch target, easy to hit). No additional mitigation needed.

### Risk 6: Regression in Search Page

**Risk**: Removing the back-link and changing `<main>` to `<div>` could introduce subtle layout shifts or break existing behavior.

**Mitigation**: The back-link is a visual element only -- removing it has no effect on the search flow logic. Changing `<main>` to `<div>` is purely semantic (the layout's `<main>` wraps all pages). Run the full search flow (record + upload + results) after the change to verify. The existing `pnpm test` also verifies API client behavior.

---

*Plan created from exhaustive analysis of the audio-ident-ui codebase (every source file in `src/`), cross-referenced against the UI functionality inventory, backend API capabilities map, UX recommendations, and devil's advocate review. All blocking issues (BLOCK-3) and significant concerns (SIG-1, SIG-6, SIG-7) from the devil's advocate review have been incorporated.*
