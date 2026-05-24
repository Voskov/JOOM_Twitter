from __future__ import annotations

import uuid

from httpx import AsyncClient


async def _signup_and_token(client: AsyncClient, username: str) -> str:
    await client.post("/auth/signup", json={"username": username, "password": "password123"})
    resp = await client.post(
        "/auth/signin", json={"username": username, "password": "password123"}
    )
    return resp.json()["access_token"]


async def test_signin_rate_limit(rate_limit_client: AsyncClient) -> None:
    """6th signin from same IP → 429."""
    for i in range(5):
        r = await rate_limit_client.post(
            "/auth/signin", json={"username": f"nouser{i}", "password": "x"}
        )
        assert r.status_code != 429
    r = await rate_limit_client.post(
        "/auth/signin", json={"username": "nouser6", "password": "x"}
    )
    assert r.status_code == 429
    assert "Rate limit exceeded" in r.json()["detail"]


async def test_post_message_rate_limit(rate_limit_client: AsyncClient) -> None:
    """11th PostMessage from same user → 429."""
    token = await _signup_and_token(rate_limit_client, f"rl_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {token}"}
    for _ in range(10):
        r = await rate_limit_client.post("/messages", json={"content": "hi"}, headers=headers)
        assert r.status_code == 201
    r = await rate_limit_client.post("/messages", json={"content": "hi"}, headers=headers)
    assert r.status_code == 429
    assert "Rate limit exceeded" in r.json()["detail"]


async def test_post_message_limit_is_per_user(rate_limit_client: AsyncClient) -> None:
    """User A at limit does not affect user B."""
    user_a = f"a_{uuid.uuid4().hex[:8]}"
    user_b = f"b_{uuid.uuid4().hex[:8]}"
    token_a = await _signup_and_token(rate_limit_client, user_a)
    token_b = await _signup_and_token(rate_limit_client, user_b)

    for _ in range(10):
        await rate_limit_client.post(
            "/messages", json={"content": "hi"}, headers={"Authorization": f"Bearer {token_a}"}
        )

    r = await rate_limit_client.post(
        "/messages", json={"content": "hi"}, headers={"Authorization": f"Bearer {token_b}"}
    )
    assert r.status_code == 201
