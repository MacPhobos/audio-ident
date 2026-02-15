# audio-ident API Contract

> **Version**: 1.1.0
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

### TrackInfo

Core track metadata returned by search, list, and detail endpoints.

```typescript
interface TrackInfo {
	id: string; // UUID
	title: string;
	artist: string | null;
	album: string | null;
	duration_seconds: number;
	ingested_at: string; // ISO 8601
}
```

### TrackDetail

Extended track metadata including fingerprint and embedding status.

```typescript
interface TrackDetail extends TrackInfo {
	sample_rate: number | null;
	channels: number | null;
	bitrate: number | null;
	format: string | null;
	file_hash_sha256: string;
	file_size_bytes: number;
	olaf_indexed: boolean;
	embedding_model: string | null;
	embedding_dim: number | null;
	updated_at: string; // ISO 8601
}
```

### ExactMatch

A fingerprint-based match result from Olaf.

```typescript
interface ExactMatch {
	track: TrackInfo;
	confidence: number; // 0.0-1.0
	offset_seconds: number | null;
	aligned_hashes: number;
}
```

### VibeMatch

An embedding-similarity match result from CLAP/Qdrant.

```typescript
interface VibeMatch {
	track: TrackInfo;
	similarity: number; // 0.0-1.0
	embedding_model: string;
}
```

### SearchResponse

Returned by the search endpoint.

```typescript
interface SearchResponse {
	request_id: string; // UUID
	query_duration_ms: number;
	exact_matches: ExactMatch[];
	vibe_matches: VibeMatch[];
	mode_used: 'exact' | 'vibe' | 'both';
}
```

### IngestResponse

Returned by the ingest endpoint for a single file.

```typescript
interface IngestResponse {
	track_id: string; // UUID
	title: string;
	artist: string | null;
	status: 'ingested' | 'duplicate' | 'error';
}
```

### IngestReport

Returned by the ingest endpoint for batch (directory) ingestion.

```typescript
interface IngestReport {
	total: number;
	ingested: number;
	duplicates: number;
	errors: IngestError[];
}
```

### IngestError

Describes a single file that failed during batch ingestion.

```typescript
interface IngestError {
	file: string;
	error: string;
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

### Search

#### `POST /api/v1/search`

Upload an audio clip and search for matches. Supports exact fingerprint matching (Olaf), similarity-based vibe matching (CLAP embeddings), or both.

**Request** `multipart/form-data`

| Field         | Type    | Required | Default  | Description                                   |
| ------------- | ------- | -------- | -------- | --------------------------------------------- |
| `audio`       | file    | yes      | -        | Audio file upload (max 10 MB)                 |
| `mode`        | string  | no       | `"both"` | Search mode: `"exact"`, `"vibe"`, or `"both"` |
| `max_results` | integer | no       | `10`     | Maximum results per match type (1-50)         |

**Supported audio formats**: MP3, WAV, FLAC, OGG, WebM, MP4/AAC

**Response** `200 OK`

```json
{
	"request_id": "550e8400-e29b-41d4-a716-446655440000",
	"query_duration_ms": 342,
	"exact_matches": [
		{
			"track": {
				"id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
				"title": "Bohemian Rhapsody",
				"artist": "Queen",
				"album": "A Night at the Opera",
				"duration_seconds": 354.5,
				"ingested_at": "2026-02-14T10:30:00Z"
			},
			"confidence": 0.95,
			"offset_seconds": 12.3,
			"aligned_hashes": 847
		}
	],
	"vibe_matches": [
		{
			"track": {
				"id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
				"title": "Somebody to Love",
				"artist": "Queen",
				"album": "A Day at the Races",
				"duration_seconds": 296.2,
				"ingested_at": "2026-02-14T10:31:00Z"
			},
			"similarity": 0.87,
			"embedding_model": "clap-htsat-large"
		}
	],
	"mode_used": "both"
}
```

**Error Codes**

| Code                  | HTTP Status | Description                                                |
| --------------------- | ----------- | ---------------------------------------------------------- |
| `FILE_TOO_LARGE`      | 400         | Uploaded file exceeds 10 MB limit                          |
| `UNSUPPORTED_FORMAT`  | 400         | Audio format not recognized or unsupported                 |
| `AUDIO_TOO_SHORT`     | 400         | Audio clip is shorter than 3 seconds                       |
| `SEARCH_TIMEOUT`      | 504         | Search did not complete within the allowed time            |
| `SERVICE_UNAVAILABLE` | 503         | One or more search backends (Olaf, Qdrant) are unavailable |

---

### Ingest

#### `POST /api/v1/ingest`

Ingest audio file(s) into the system. Accepts either a single file upload or a server-side directory path for batch ingestion. Admin/CLI endpoint.

**Request** `multipart/form-data`

| Field       | Type   | Required | Description                                       |
| ----------- | ------ | -------- | ------------------------------------------------- |
| `audio`     | file   | no\*     | Single audio file to ingest                       |
| `directory` | string | no\*     | Server-side directory path containing audio files |

\* Exactly one of `audio` or `directory` must be provided.

**Supported audio formats**: MP3, WAV, FLAC, OGG

**Response (single file)** `201 Created`

```json
{
	"track_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
	"title": "Bohemian Rhapsody",
	"artist": "Queen",
	"status": "ingested"
}
```

**Response (directory batch)** `200 OK`

```json
{
	"total": 150,
	"ingested": 142,
	"duplicates": 6,
	"errors": [
		{
			"file": "/path/to/corrupt.mp3",
			"error": "Failed to decode audio: invalid frame header"
		},
		{
			"file": "/path/to/tiny.wav",
			"error": "Audio too short: 1.2s (minimum 3s)"
		}
	]
}
```

**Error Codes**

| Code                  | HTTP Status | Description                                                        |
| --------------------- | ----------- | ------------------------------------------------------------------ |
| `VALIDATION_ERROR`    | 400         | Neither `audio` nor `directory` provided, or both provided         |
| `UNSUPPORTED_FORMAT`  | 400         | Audio format not recognized or unsupported                         |
| `AUDIO_TOO_SHORT`     | 400         | Audio clip is shorter than 3 seconds                               |
| `AUDIO_TOO_LONG`      | 400         | Audio clip exceeds 30 minutes                                      |
| `DIRECTORY_NOT_FOUND` | 400         | Specified directory does not exist on the server                   |
| `SERVICE_UNAVAILABLE` | 503         | One or more data stores (PostgreSQL, Olaf, Qdrant) are unavailable |

---

### List Tracks

#### `GET /api/v1/tracks`

List ingested tracks with optional search filtering. Returns paginated results using the standard `PaginatedResponse<TrackInfo>` wrapper.

**Query Parameters**

| Parameter  | Type    | Default | Max | Description                                                  |
| ---------- | ------- | ------- | --- | ------------------------------------------------------------ |
| `page`     | integer | 1       | -   | 1-indexed page number                                        |
| `pageSize` | integer | 50      | 100 | Items per page                                               |
| `search`   | string  | -       | -   | Filter by title or artist (case-insensitive substring match) |

**Response** `200 OK`

```json
{
	"data": [
		{
			"id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
			"title": "Bohemian Rhapsody",
			"artist": "Queen",
			"album": "A Night at the Opera",
			"duration_seconds": 354.5,
			"ingested_at": "2026-02-14T10:30:00Z"
		}
	],
	"pagination": {
		"page": 1,
		"pageSize": 50,
		"totalItems": 142,
		"totalPages": 3
	}
}
```

**Error Codes**

| Code               | HTTP Status | Description              |
| ------------------ | ----------- | ------------------------ |
| `VALIDATION_ERROR` | 400         | Invalid query parameters |

---

### Track Detail

#### `GET /api/v1/tracks/{id}`

Retrieve full metadata for a single track, including fingerprint and embedding status.

**Path Parameters**

| Parameter | Type          | Description      |
| --------- | ------------- | ---------------- |
| `id`      | string (UUID) | Track identifier |

**Response** `200 OK`

```json
{
	"id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
	"title": "Bohemian Rhapsody",
	"artist": "Queen",
	"album": "A Night at the Opera",
	"duration_seconds": 354.5,
	"ingested_at": "2026-02-14T10:30:00Z",
	"sample_rate": 44100,
	"channels": 2,
	"bitrate": 320000,
	"format": "mp3",
	"file_hash_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
	"file_size_bytes": 14230528,
	"olaf_indexed": true,
	"embedding_model": "clap-htsat-large",
	"embedding_dim": 512,
	"updated_at": "2026-02-14T10:30:05Z"
}
```

**Error Codes**

| Code               | HTTP Status | Description                            |
| ------------------ | ----------- | -------------------------------------- |
| `NOT_FOUND`        | 404         | Track with the given ID does not exist |
| `VALIDATION_ERROR` | 400         | Invalid UUID format                    |

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

| Code                  | HTTP Status | Description                                            |
| --------------------- | ----------- | ------------------------------------------------------ |
| `VALIDATION_ERROR`    | 400         | Invalid request parameters                             |
| `FILE_TOO_LARGE`      | 400         | Uploaded file exceeds size limit                       |
| `UNSUPPORTED_FORMAT`  | 400         | Audio format not recognized or unsupported             |
| `AUDIO_TOO_SHORT`     | 400         | Audio clip shorter than 3 seconds                      |
| `AUDIO_TOO_LONG`      | 400         | Audio clip exceeds 30 minutes                          |
| `DIRECTORY_NOT_FOUND` | 400         | Server-side directory does not exist                   |
| `NOT_FOUND`           | 404         | Resource not found                                     |
| `RATE_LIMITED`        | 429         | Too many requests                                      |
| `INTERNAL_ERROR`      | 500         | Server error                                           |
| `SERVICE_UNAVAILABLE` | 503         | Backend service (Olaf, Qdrant, PostgreSQL) unavailable |
| `SEARCH_TIMEOUT`      | 504         | Search did not complete in time                        |

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
| `503 Service Unavailable`   | Backend dependency unavailable        |
| `504 Gateway Timeout`       | Search timeout                        |

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

| Version | Date       | Changes                                                           |
| ------- | ---------- | ----------------------------------------------------------------- |
| 1.1.0   | 2026-02-14 | Add search, ingest, tracks list, track detail endpoints and types |
| 1.0.0   | 2026-02-14 | Initial contract: health, version endpoints                       |

---

_This contract is the source of truth. UI and service implementations must conform to these definitions._
