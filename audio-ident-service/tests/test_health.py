import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_ok(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


@pytest.mark.asyncio
async def test_version_returns_metadata(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "audio-ident-service"
    assert "version" in body
    assert "git_sha" in body
    assert "build_time" in body


@pytest.mark.asyncio
async def test_openapi_available(client: AsyncClient) -> None:
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert "openapi" in schema
    assert "/health" in schema["paths"]
