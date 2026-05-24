from __future__ import annotations

import uuid

from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid(prefix: str = "socialuser") -> str:
    """Return a short, test-unique username."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def _register_and_login(
    client: AsyncClient, username: str, password: str = "password123"
) -> str:
    """Sign up, sign in, return bearer token."""
    await client.post("/auth/signup", json={"username": username, "password": password})
    resp = await client.post("/auth/signin", json={"username": username, "password": password})
    return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# Follow tests
# ---------------------------------------------------------------------------

async def test_follow_user_success(client: AsyncClient) -> None:
    """Following an existing, different user returns 200."""
    follower = _uid("flwr")
    followed = _uid("flwd")

    follower_token = await _register_and_login(client, follower)
    await _register_and_login(client, followed)  # register target user

    resp = await client.post(
        f"/social/follow/{followed}",
        headers={"Authorization": f"Bearer {follower_token}"},
    )
    assert resp.status_code == 200
    assert "message" in resp.json()


async def test_follow_nonexistent_user_returns_404(client: AsyncClient) -> None:
    """Following a username that does not exist returns 404."""
    username = _uid("ghost")
    token = await _register_and_login(client, username)

    resp = await client.post(
        "/social/follow/definitely_no_such_user_xyz_99999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_follow_yourself_returns_409(client: AsyncClient) -> None:
    """Following yourself returns 409 Conflict (ConflictError in service)."""
    username = _uid("selfflw")
    token = await _register_and_login(client, username)

    resp = await client.post(
        f"/social/follow/{username}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


async def test_follow_same_user_twice_returns_409(client: AsyncClient) -> None:
    """Following the same user a second time returns 409 Conflict."""
    follower = _uid("dblflwr")
    followed = _uid("dblflwd")

    follower_token = await _register_and_login(client, follower)
    await _register_and_login(client, followed)

    headers = {"Authorization": f"Bearer {follower_token}"}

    first = await client.post(f"/social/follow/{followed}", headers=headers)
    assert first.status_code == 200

    second = await client.post(f"/social/follow/{followed}", headers=headers)
    assert second.status_code == 409


async def test_follow_without_auth_returns_401(client: AsyncClient) -> None:
    """Calling follow without a bearer token returns 401."""
    resp = await client.post("/social/follow/anyone")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Unfollow tests
# ---------------------------------------------------------------------------

async def test_unfollow_user_success(client: AsyncClient) -> None:
    """Unfollowing a user you currently follow returns 200."""
    follower = _uid("unflwr")
    followed = _uid("unflwd")

    follower_token = await _register_and_login(client, follower)
    await _register_and_login(client, followed)

    headers = {"Authorization": f"Bearer {follower_token}"}

    follow_resp = await client.post(f"/social/follow/{followed}", headers=headers)
    assert follow_resp.status_code == 200

    unfollow_resp = await client.post(f"/social/unfollow/{followed}", headers=headers)
    assert unfollow_resp.status_code == 200
    assert "message" in unfollow_resp.json()


async def test_unfollow_user_not_following_returns_404(client: AsyncClient) -> None:
    """Unfollowing someone you never followed returns 404."""
    follower = _uid("notflwr")
    target = _uid("notflwd")

    follower_token = await _register_and_login(client, follower)
    await _register_and_login(client, target)

    resp = await client.post(
        f"/social/unfollow/{target}",
        headers={"Authorization": f"Bearer {follower_token}"},
    )
    assert resp.status_code == 404


async def test_unfollow_nonexistent_user_returns_404(client: AsyncClient) -> None:
    """Unfollowing a username that does not exist returns 404."""
    username = _uid("unf_ghost")
    token = await _register_and_login(client, username)

    resp = await client.post(
        "/social/unfollow/definitely_no_such_user_xyz_99999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_unfollow_without_auth_returns_401(client: AsyncClient) -> None:
    """Calling unfollow without a bearer token returns 401."""
    resp = await client.post("/social/unfollow/anyone")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Follow count / feed integration
# ---------------------------------------------------------------------------

async def test_follow_two_users_posts_appear_in_following_feed(client: AsyncClient) -> None:
    """After following 2 users, both of their posts appear in the follow feed."""
    follower = _uid("multi_flwr")
    user_a = _uid("multi_a")
    user_b = _uid("multi_b")

    follower_token = await _register_and_login(client, follower)
    token_a = await _register_and_login(client, user_a)
    token_b = await _register_and_login(client, user_b)

    follower_headers = {"Authorization": f"Bearer {follower_token}"}

    # Follow both users
    r1 = await client.post(f"/social/follow/{user_a}", headers=follower_headers)
    assert r1.status_code == 200
    r2 = await client.post(f"/social/follow/{user_b}", headers=follower_headers)
    assert r2.status_code == 200

    # Each user posts a unique message
    content_a = f"post_a_{uuid.uuid4().hex[:8]}"
    content_b = f"post_b_{uuid.uuid4().hex[:8]}"

    pa = await client.post(
        "/messages",
        json={"content": content_a},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert pa.status_code == 201

    pb = await client.post(
        "/messages",
        json={"content": content_b},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert pb.status_code == 201

    # Fresh unique user — cache miss at offset=0 hits DB directly
    resp = await client.get(
        "/feed/following",
        params={"limit": 50, "offset": 0},
        headers=follower_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    contents = [item["content"] for item in body["items"]]
    assert content_a in contents, f"{content_a!r} not found in follow feed"
    assert content_b in contents, f"{content_b!r} not found in follow feed"
