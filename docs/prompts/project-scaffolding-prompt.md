# Goal: Scaffold a service backend and ui frontend from scratch


You are Claude Code with filesystem access. Scaffold a new **monorepo** project with a SvelteKit frontend and FastAPI python backend according to the following specifications.


## User specified information


audio-ident refers to the overall project naming scheme. You must prompt the user to specify the replacement value for audio-ident.


## Hard constraints
- Monorepo layout:
- `audio-ident-ui/` = SvelteKit frontend
- `audio-ident-service/` = FastAPI backend
- `docs/` = project docs
- Include a root `Makefile` that provides a single, consistent developer UX.
- Include `asdf` `.tool-versions` at repo root (and/or in subprojects as required to fulfill tooling requirements).


## Deliverable: a runnable vertical slice (non-negotiable)
When done, a dev can run:
- `make dev` → starts app + service + postgres
- app loads in browser and shows “API reachable” using real API calls
- service exposes:
- `GET /health` (200)
- `GET /api/v1/version` (returns name + git sha + build time)
- app calls the service using **TanStack Query** and displays results.


## Stack requirements


### audio-ident-ui/ stack (SvelteKit)
- SvelteKit + TypeScript + Vite
- Tailwind CSS
- shadcn-svelte
- Lucide icons
- SvelteKit Forms (progressive enhancement where relevant)
- Zod validation
- Svelte stores (only for UI state; server state via TanStack Query)
- TanStack Query (Svelte)
- ESLint + Prettier + svelte-check
- Vitest (at least 1 test)
- `.env.example` and env loading pattern


### audio-ident-service/ stack (FastAPI)
- Python = use a **currently stable** Python 3.x version supported well by FastAPI tooling (do not force unreleased versions)
- `uv` with `pyproject.toml`
- FastAPI
- PostgreSQL
- SQLAlchemy 2.x ORM
- Alembic migrations
- Pydantic v2
- Auth scaffolding: OAuth2 + JWT (stubs are fine, but structure must exist)
- libs: python-jose + passlib[bcrypt] (or argon2 if you prefer)
- Ruff + Black + mypy or pyright
- pytest (at least 1 test)
- `.env.example` and `pydantic-settings` config


## API contract discipline (must implement)
- Service must publish OpenAPI (built-in).
- Repo must include `make gen-client` that generates a typed TypeScript client from the service OpenAPI and places it in `src/lib/api/` (or similar).
- App must import and use that generated client (no hand-rolled fetch wrappers for the vertical slice).


## Repo-level tooling / structure (must implement)
- Root `Makefile` targets (minimum):
- `make install` (installs all dependencis and prepares the environment)`
- `make dev` (runs everything)
- `make test` (runs all tests)
- `make lint` / `make fmt`
- `make typecheck`
- `make gen-client`
- `make db-up` / `make db-reset` (drop + recreate + migrate)
- `docker-compose.yml` for Postgres (and optional pgadmin if you want).
- Database migrations:
- `service/` has an initial Alembic migration for the vertical slice resource.
- Docs:
- `docs/README.md` describing how to run, test, and generate client.


## CLAUDE.md
Create `CLAUDE.md` at repo root, plus minimal `audio-ident-ui/CLAUDE.md` and `audio-ident-service/CLAUDE.md` describing:
- how to bootstrap the project after cloning
- how to run
- conventions
- guard-rails / non-goals
- how to add a new endpoint + regenerate client


## Implementation notes
- Keep it minimal but complete. No “future work” placeholders without wiring.
- Prefer clean structure: `service/app/` (routers, db, models, schemas, settings), `service/tests/`.
- Prefer `app/src/routes/` + `app/src/lib/` organization.


## Done when
- `make dev` works
- UI shows data retrieved from `service` via generated client
- migrations run cleanly
- tests/lint/typecheck targets exist and pass
- guardrails doc + deny checks exist




# Additional considerations
The scaffolded UI and Service must use a unique set of ports for each service to avoid conflicts with other services running on the same machine.
1. The service must be accessible using a public hostname and port.
   1a) You must prompt the user to select the service port, for example port 15010
2. The UI must be accessible using a public hostname and port.
   2a) You must prompt the user to select the UI port, for example port 15000
3. **CRITICAL** Both UI and Service projects must provide the ability to easily change the ports and any other configurables using .env file settings with example defaults provided in .env.example


We are scaffolding a UI and Service projects where the UI will issue REST API calls to the Service.
This requires following an API contract referenced by CLAUDE.md in the monoreport root and CLAUDE.md files in each subproject.
The contract referenced from all three CLAUDE.md files should be present in each repo's docs/api-controract.md file.


The contract rules are:


```
# Prompt: Establish API Contract Pattern for UI + Service Monorepo




> **Purpose**: Use this prompt to establish a frozen API contract pattern between a backend service and frontend UI in any monorepo project.
>
> **When to Use**: When you have (or are creating) a monorepo with separate backend/service and frontend/UI subprojects that communicate via REST API.




---




## The Prompt




Copy and customize the following prompt for your specific project:




---




### PROMPT START




I need you to establish an **API Contract Pattern** for my monorepo project. This pattern ensures type-safe communication between my backend service and frontend UI through a frozen, versioned API contract.




#### My Project Structure




```
{PROJECT_ROOT}/
├── {SERVICE_DIR}/           # Backend service (e.g., Python/FastAPI, Node/Express, Go)
│   └── docs/api-contract.md # Contract lives here (source of truth)
├── {UI_DIR}/                # Frontend UI (e.g., SvelteKit, Next.js, React)
│   └── docs/api-contract.md # Identical copy of contract
└── docs/                    # Shared documentation
```




**Fill in these values:**
- `{PROJECT_ROOT}`: (e.g., `my-app`)
- `{SERVICE_DIR}`: (e.g., `my-app-service`, `backend`, `api`)
- `{UI_DIR}`: (e.g., `my-app-ui`, `frontend`, `web`)
- `{SERVICE_STACK}`: (e.g., `Python FastAPI`, `Node Express`, `Go Gin`)
- `{UI_STACK}`: (e.g., `SvelteKit`, `Next.js`, `React + Vite`)
- `{PROJECT_DESCRIPTION}`: Brief description of what your app does




#### What I Need You to Create




**1. Create `{SERVICE_DIR}/docs/api-contract.md`** with this structure:




```markdown
# {PROJECT_NAME} API Contract




> **Version**: 1.0.0
> **Last Updated**: {TODAY_DATE}
> **Status**: FROZEN - Changes require version bump and UI sync




This document defines the API contract between `{SERVICE_DIR}` (backend) and `{UI_DIR}` (frontend).




---




## Table of Contents




1. [Base Configuration](#base-configuration)
2. [Type Generation Strategy](#type-generation-strategy)
3. [Common Types](#common-types)
4. [Endpoints](#endpoints)
5. [Pagination](#pagination)
6. [Error Handling](#error-handling)
7. [Status Codes](#status-codes)
8. [CORS Configuration](#cors-configuration)
9. [Authentication](#authentication)




---




## Base Configuration




| Environment | Base URL |
|-------------|----------|
| Development | `http://localhost:{SERVICE_PORT}` |
| Production  | `https://api.{DOMAIN}` (TBD) |




All endpoints are prefixed with `/api/v1` except `/health` and `/openapi.json`.




---




## Type Generation Strategy




**This strategy is LOCKED. Do not deviate.**




### Backend ({SERVICE_STACK})




- OpenAPI spec auto-generated at `/openapi.json`
- All response models must be properly typed
- {BACKEND_SPECIFIC_NOTES}




### Frontend ({UI_STACK})




- Generated types at `src/lib/api/generated.ts` (or equivalent)
- Generation script: `npm run gen:api` (or equivalent)
- {FRONTEND_SPECIFIC_NOTES}




**Workflow**: Backend changes models → Backend deploys → UI runs type generation → UI updates




---




## Common Types




### Pagination Wrapper




All list endpoints return paginated responses.




```typescript
interface PaginatedResponse<T> {
   data: T[];
   pagination: {
       page: number;       // Current page (1-indexed)
       pageSize: number;   // Items per page
       totalItems: number; // Total count
       totalPages: number; // Calculated total pages
   };
}
```




### ErrorResponse




All errors follow this shape.




```typescript
interface ErrorResponse {
   error: {
       code: string;       // Machine-readable code
       message: string;    // Human-readable message
       details?: unknown;  // Optional additional context
   };
}
```




---




## Endpoints




### Health




#### `GET /health`




Health check endpoint. No authentication required. No `/api/v1` prefix.




**Response** `200 OK`




```json
{
   "status": "ok",
   "version": "1.0.0"
}
```




---




### {RESOURCE_NAME_1}




{ADD YOUR FIRST RESOURCE ENDPOINTS HERE}




#### {RESOURCE} Schema




```typescript
interface {Resource} {
   id: string;           // UUID
   // Add your fields
   createdAt: string;    // ISO 8601
   updatedAt: string;    // ISO 8601
}
```




#### `GET /api/v1/{resources}`




List all {resources} with pagination.




**Query Parameters**




| Parameter  | Type    | Default | Description |
|------------|---------|---------|-------------|
| `page`     | integer | 1       | Page number |
| `pageSize` | integer | 20      | Items per page (max: 100) |




**Response** `200 OK` - `PaginatedResponse<{Resource}>`




#### `GET /api/v1/{resources}/{id}`




Get single {resource} by ID.




**Response** `200 OK` - {Resource} object




**Response** `404 Not Found`




```json
{
   "error": {
       "code": "{RESOURCE}_NOT_FOUND",
       "message": "{Resource} with ID '...' not found"
   }
}
```




#### `POST /api/v1/{resources}`




Create a new {resource}.




**Request Body**




```json
{
   // Add required fields
}
```




**Response** `201 Created` - {Resource} object




#### `PATCH /api/v1/{resources}/{id}`




Update a {resource}.




**Request Body** - All fields optional




**Response** `200 OK` - Updated {Resource} object




#### `DELETE /api/v1/{resources}/{id}`




Delete a {resource}.




**Response** `204 No Content`




---




## Pagination




High volume list endpoints use consistent pagination. You must ask the user to determine if a list endpoint requires pagination.




### Request Parameters




| Parameter  | Type    | Default | Max | Description |
|------------|---------|---------|-----|-------------|
| `page`     | integer | 1       | -   | 1-indexed page number |
| `pageSize` | integer | 50      | 200 | Items per page |




### Edge Cases




- `page` > `totalPages`: Returns empty `data` array, valid pagination meta
- `page` < 1: Treated as `page=1`
- `pageSize` > 100: Clamped to 100




---




## Error Handling




### Error Response Shape




```json
{
   "error": {
       "code": "ERROR_CODE",
       "message": "Human-readable description",
       "details": { ... }
   }
}
```




### Error Codes




| Code | HTTP Status | Description |
|------|-------------|-------------|
| `VALIDATION_ERROR` | 400 | Invalid request parameters |
| `{RESOURCE}_NOT_FOUND` | 404 | Resource not found |
| `RATE_LIMITED` | 429 | Too many requests |
| `INTERNAL_ERROR` | 500 | Server error |




---




## Status Codes




| Code | Usage |
|------|-------|
| `200 OK` | Successful GET, PATCH, POST (actions) |
| `201 Created` | Successful POST (resource creation) |
| `204 No Content` | Successful DELETE |
| `400 Bad Request` | Validation error |
| `404 Not Found` | Resource not found |
| `409 Conflict` | State conflict |
| `429 Too Many Requests` | Rate limited |
| `500 Internal Server Error` | Unexpected error |




---




## CORS Configuration




### Development




```
Origins: http://localhost:{UI_PORT}
Credentials: Allowed
Methods: All
Headers: All
```




### Production




Configure via environment variable: `CORS_ORIGINS`




---




## Authentication




{DESCRIBE YOUR AUTH STRATEGY OR MARK AS FUTURE}




---




## Changelog




| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | {TODAY_DATE} | Initial contract |




---




_This contract is the source of truth. UI and service implementations must conform to these definitions._
```




**2. Copy the contract to UI:**
```bash
cp {SERVICE_DIR}/docs/api-contract.md {UI_DIR}/docs/api-contract.md
```




**3. Update the root CLAUDE.md** with these sections:




```markdown
## The Golden Rule: API Contract is FROZEN




**Contract Location**: `docs/api-contract.md` exists in BOTH subprojects (must be identical)




### Contract Synchronization Workflow (ONE WAY ONLY)




1. **Update backend contract**: Edit `{SERVICE_DIR}/docs/api-contract.md`
2. **Update backend models**: Update your request/response schemas
3. **Run backend tests**: Verify nothing breaks
4. **Copy contract to frontend**: `cp {SERVICE_DIR}/docs/api-contract.md {UI_DIR}/docs/`
5. **Regenerate frontend types**: `cd {UI_DIR} && npm run gen:api`
6. **Update frontend code**: Use new generated types
7. **Run frontend tests**: Verify type safety




**NEVER**:
- Manually edit generated type files in the frontend
- Change API without updating contract in BOTH repos
- Deploy frontend without regenerating types after backend changes
- Break backward compatibility without version bump




### Contract Change Checklist




- [ ] Update `{SERVICE_DIR}/docs/api-contract.md`
- [ ] Update backend schemas/models
- [ ] Backend tests pass
- [ ] Copy contract to `{UI_DIR}/docs/api-contract.md`
- [ ] Regenerate types (`npm run gen:api`)
- [ ] Update frontend code using new types
- [ ] Frontend tests pass
```




**4. Add type generation script to `{UI_DIR}/package.json`:**




For OpenAPI-based backends:
```json
{
   "scripts": {
       "gen:api": "openapi-typescript $VITE_API_BASE_URL/openapi.json -o src/lib/api/generated.ts"
   }
}
```




**5. Create a `.gitignore` entry for generated types** (optional - some teams prefer to commit them):
```
# Generated API types (regenerate with: npm run gen:api)
# src/lib/api/generated.ts
```




---




#### Additional Requirements




1. **Add version to health endpoint**: The `/health` endpoint should return the current API version
2. **Document all error codes**: Every possible error should have a documented code
3. **Include example responses**: Show realistic example JSON for each endpoint
4. **Keep changelog updated**: Every contract change must be logged with version bump




---




### PROMPT END




---




## Customization Guide




### For Different Backend Stacks




| Stack | OpenAPI Generation | Notes |
|-------|-------------------|-------|
| **FastAPI (Python)** | Automatic at `/openapi.json` | Use Pydantic models for schemas |
| **Express (Node)** | Use `swagger-jsdoc` + `swagger-ui-express` | Document with JSDoc comments |
| **Go (Gin/Chi)** | Use `swag` CLI tool | Generate from code comments |
| **NestJS** | Built-in via `@nestjs/swagger` | Use decorators for schemas |
| **Spring Boot** | Use `springdoc-openapi` | Automatic from controllers |




### For Different Frontend Stacks




| Stack | Type Generation | Import Path |
|-------|-----------------|-------------|
| **SvelteKit** | `openapi-typescript` | `src/lib/api/generated.ts` |
| **Next.js** | `openapi-typescript` | `src/api/generated.ts` |
| **React (Vite)** | `openapi-typescript` | `src/api/generated.ts` |
| **Vue** | `openapi-typescript` | `src/api/generated.ts` |
| **Angular** | `ng-openapi-gen` | `src/app/api/` |




### Type Generation Commands




```bash
# Using openapi-typescript (recommended)
npx openapi-typescript http://localhost:8000/openapi.json -o src/lib/api/generated.ts




# Using openapi-generator-cli
npx @openapitools/openapi-generator-cli generate -i http://localhost:8000/openapi.json -g typescript-fetch -o src/api/




# Using swagger-typescript-api
npx swagger-typescript-api -p http://localhost:8000/openapi.json -o src/api -n generated.ts
```




---




## Enforcement Checklist




When reviewing PRs that touch API:




- [ ] `docs/api-contract.md` updated in backend?
- [ ] Contract copied to frontend?
- [ ] Types regenerated?
- [ ] Version bumped if breaking change?
- [ ] Changelog entry added?
- [ ] Both backend and frontend tests pass?




---




## Anti-Patterns to Avoid




1. **Manual type definitions**: Never write frontend types by hand if you have OpenAPI
2. **Divergent contracts**: Never let the two `api-contract.md` files differ
3. **Undocumented endpoints**: Every endpoint must be in the contract before implementation
4. **Breaking changes without version bump**: Clients depend on stability
5. **Committing generated files without regeneration**: Types can become stale




---




## Example Projects Using This Pattern




This pattern is derived from production monorepos with:
- FastAPI + SvelteKit
- Express + Next.js
- Go + React




The key principle is **contract-first development**: define the API contract, then implement to match.


```







