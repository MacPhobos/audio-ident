# audio-ident-ui

SvelteKit frontend for the audio-ident project.

## Stack

- **Framework**: SvelteKit + Svelte 5 (Runes API)
- **Language**: TypeScript (strict)
- **Styling**: Tailwind CSS v4
- **Components**: shadcn-svelte (Svelte 5 compatible)
- **Icons**: lucide-svelte
- **Server State**: TanStack Query (@tanstack/svelte-query)
- **Validation**: Zod
- **Testing**: Vitest
- **Linting**: ESLint + Prettier + svelte-check
- **Package Manager**: pnpm

## Commands

```bash
pnpm dev          # Start dev server on port 17000
pnpm build        # Production build
pnpm check        # Type-check with svelte-check
pnpm test         # Run tests
pnpm lint         # Lint + format check
pnpm format       # Auto-format
pnpm gen:api      # Regenerate API types from OpenAPI spec
```

## Architecture

- `src/routes/` — SvelteKit file-based routing
- `src/lib/api/generated.ts` — Auto-generated types from OpenAPI (run `pnpm gen:api`)
- `src/lib/api/client.ts` — Typed API client using generated types
- `src/lib/components/ui/` — shadcn-svelte UI components

## Conventions

- Use Svelte 5 Runes (`$state`, `$derived`, `$effect`, `$props`) — NOT legacy stores or `$:` syntax
- Use TanStack Query for all server state (no manual fetch in `onMount`)
- Use `$props()` for component props, `$bindable()` for two-way binding
- API types come from `generated.ts` — do not hand-write API response types
- All API calls go through `src/lib/api/client.ts`

## Ports

- UI: 17000 (configurable via PORT env var)
- API: 17010 (configurable via VITE_API_BASE_URL env var)
