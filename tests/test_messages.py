from __future__ import annotations

from httpx import AsyncClient


async def test_post_message_success(auth_client: AsyncClient) -> None:
    resp = await auth_client.post("/messages", json={"content": "Hello, world!"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["content"] == "Hello, world!"
    assert body["username"] == "testuser"
    assert "id" in body
    assert "created_at" in body


async def test_post_message_too_long(auth_client: AsyncClient) -> None:
    long_content = "x" * 141
    resp = await auth_client.post("/messages", json={"content": long_content})
    assert resp.status_code == 422


async def test_post_message_empty(auth_client: AsyncClient) -> None:
    resp = await auth_client.post("/messages", json={"content": ""})
    assert resp.status_code == 422


async def test_post_message_exactly_140_chars(auth_client: AsyncClient) -> None:
    content = "a" * 140
    resp = await auth_client.post("/messages", json={"content": content})
    assert resp.status_code == 201
    assert resp.json()["content"] == content


async def test_post_message_requires_auth(client: AsyncClient) -> None:
    resp = await client.post("/messages", json={"content": "No auth here"})
    assert resp.status_code == 401
