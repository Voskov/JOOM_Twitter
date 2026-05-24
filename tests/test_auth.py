from __future__ import annotations

import uuid

from httpx import AsyncClient


def _unique(prefix: str = "user") -> str:
    """Return a username that is unique within a test run."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def test_signup_success(client: AsyncClient) -> None:
    username = _unique()
    resp = await client.post("/auth/signup", json={"username": username, "password": "password123"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == username


async def test_signup_duplicate_username(client: AsyncClient) -> None:
    username = _unique()
    payload = {"username": username, "password": "password123"}
    await client.post("/auth/signup", json=payload)
    resp = await client.post("/auth/signup", json=payload)
    assert resp.status_code == 409


async def test_signup_short_password(client: AsyncClient) -> None:
    resp = await client.post(
        "/auth/signup",
        json={"username": _unique(), "password": "short"},
    )
    assert resp.status_code == 422


async def test_signup_invalid_username_chars(client: AsyncClient) -> None:
    resp = await client.post(
        "/auth/signup",
        json={"username": "invalid user!", "password": "password123"},
    )
    assert resp.status_code == 422


async def test_signin_success(client: AsyncClient) -> None:
    username = _unique()
    await client.post("/auth/signup", json={"username": username, "password": "password123"})
    resp = await client.post("/auth/signin", json={"username": username, "password": "password123"})
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["access_token"]
    # Cookie should be set
    assert "access_token" in resp.cookies


async def test_signin_wrong_password(client: AsyncClient) -> None:
    username = _unique()
    await client.post("/auth/signup", json={"username": username, "password": "password123"})
    resp = await client.post(
        "/auth/signin", json={"username": username, "password": "wrongpassword"}
    )
    assert resp.status_code == 401


async def test_signin_nonexistent_user(client: AsyncClient) -> None:
    resp = await client.post(
        "/auth/signin",
        json={"username": "doesnotexist_xyz", "password": "password123"},
    )
    assert resp.status_code == 401


async def test_signout_success(client: AsyncClient) -> None:
    username = _unique()
    await client.post("/auth/signup", json={"username": username, "password": "password123"})
    signin_resp = await client.post(
        "/auth/signin", json={"username": username, "password": "password123"}
    )
    token = signin_resp.json()["access_token"]
    resp = await client.post(
        "/auth/signout",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


async def test_signout_token_reuse_after_signout(client: AsyncClient) -> None:
    username = _unique()
    await client.post("/auth/signup", json={"username": username, "password": "password123"})
    signin_resp = await client.post(
        "/auth/signin", json={"username": username, "password": "password123"}
    )
    token = signin_resp.json()["access_token"]
    auth_headers = {"Authorization": f"Bearer {token}"}

    signout_resp = await client.post("/auth/signout", headers=auth_headers)
    assert signout_resp.status_code == 200

    # The blacklisted token must now be rejected on any protected endpoint
    reuse_resp = await client.post("/auth/signout", headers=auth_headers)
    assert reuse_resp.status_code == 401
