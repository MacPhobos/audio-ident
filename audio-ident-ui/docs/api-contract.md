# audio-ident API Contract

> **Version**: 1.0.0
> **Last Updated**: 2026-02-14
> **Status**: FROZEN - Changes require version bump and UI sync

This document defines the API contract between `audio-ident-service` (FastAPI backend) and `audio-ident-ui` (SvelteKit frontend).

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

| Environment | Base URL                            |
| ----------- | ----------------------------------- |
| Development | `http://localhost:17010`            |
| Production  | `https://api.audio-ident.com` (TBD) |

All endpoints are prefixed with `/api/v1` except `/health` and `/openapi.json`.

---

## Type Generation Strategy

**This strategy is LOCKED. Do not deviate.**

### Backend (Python FastAPI)

- OpenAPI spec auto-generated at `/openapi.json`
- All response models defined as Pydantic v2 `BaseModel` subclasses
- Pydantic models are the single source of truth for API types

### Frontend (SvelteKit)

- Generated types at `src/lib/api/generated.ts`
- Generation script: `pnpm gen:api`
- Uses `openapi-typescript` to generate types from OpenAPI spec

**Workflow**: Backend changes models → Backend deploys → UI runs type generation → UI updates

---

## Common Types

### Pagination Wrapper

All list endpoints return paginated responses.

```typescript
interface PaginatedResponse<T> {
	data: T[];
	pagination: {
		page: number; // Current page (1-indexed)
		pageSize: number; // Items per page
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
		code: string; // Machine-readable code
		message: string; // Human-readable message
		details?: unknown; // Optional additional context
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

### Version

#### `GET /api/v1/version`

Returns service version metadata. No authentication required.

**Response** `200 OK`

```json
{
	"name": "audio-ident-service",
	"version": "1.0.0",
	"git_sha": "abc1234",
	"build_time": "2026-02-14T00:00:00Z"
}
```

---

## Pagination

High volume list endpoints use consistent pagination.

### Request Parameters

| Parameter  | Type    | Default | Max | Description           |
| ---------- | ------- | ------- | --- | --------------------- |
| `page`     | integer | 1       | -   | 1-indexed page number |
| `pageSize` | integer | 50      | 100 | Items per page        |

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
		"details": {}
	}
}
```

### Error Codes

| Code               | HTTP Status | Description                |
| ------------------ | ----------- | -------------------------- |
| `VALIDATION_ERROR` | 400         | Invalid request parameters |
| `NOT_FOUND`        | 404         | Resource not found         |
| `RATE_LIMITED`     | 429         | Too many requests          |
| `INTERNAL_ERROR`   | 500         | Server error               |

---

## Status Codes

| Code                        | Usage                                 |
| --------------------------- | ------------------------------------- |
| `200 OK`                    | Successful GET, PATCH, POST (actions) |
| `201 Created`               | Successful POST (resource creation)   |
| `204 No Content`            | Successful DELETE                     |
| `400 Bad Request`           | Validation error                      |
| `404 Not Found`             | Resource not found                    |
| `409 Conflict`              | State conflict                        |
| `429 Too Many Requests`     | Rate limited                          |
| `500 Internal Server Error` | Unexpected error                      |

---

## CORS Configuration

### Development

```
Origins: http://localhost:17000
Credentials: Allowed
Methods: All
Headers: All
```

### Production

Configure via environment variable: `CORS_ORIGINS`

---

## Authentication

Scaffolded, stubs only. OAuth2 + JWT structure exists in the service but is not enforced.

- Token endpoint: `POST /api/v1/auth/token` (stub)
- Token type: Bearer JWT
- Password hashing: argon2

Full authentication will be implemented in a future version.

---

## Changelog

| Version | Date       | Changes                                     |
| ------- | ---------- | ------------------------------------------- |
| 1.0.0   | 2026-02-14 | Initial contract: health, version endpoints |

---

_This contract is the source of truth. UI and service implementations must conform to these definitions._
