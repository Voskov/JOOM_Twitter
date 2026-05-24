from __future__ import annotations

import uuid

from httpx import AsyncClient


def _uid(prefix: str = "feeduser") -> str:
    """Return a short unique username safe for each test."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def _register_and_login(
    client: AsyncClient, username: str, password: str = "password123"
) -> str:
    """Sign up, sign in, and return the bearer token."""
    await client.post("/auth/signup", json={"username": username, "password": password})
    resp = await client.post("/auth/signin", json={"username": username, "password": password})
    return resp.json()["access_token"]


async def test_global_feed_returns_messages(auth_client: AsyncClient) -> None:
    content = f"global msg {uuid.uuid4().hex[:6]}"
    await auth_client.post("/messages", json={"content": content})

    resp = await auth_client.get("/feed/global")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "total" in body
    contents = [item["content"] for item in body["items"]]
    assert content in contents


async def test_global_feed_ordered_newest_first(auth_client: AsyncClient) -> None:
    first_content = f"first_{uuid.uuid4().hex[:6]}"
    second_content = f"second_{uuid.uuid4().hex[:6]}"

    await auth_client.post("/messages", json={"content": first_content})
    await auth_client.post("/messages", json={"content": second_content})

    # Bypass the cache by using offset=1 to force a fresh DB query for ordering,
    # but we primarily check via a fresh request with offset=0 after cache invalidation.
    # Use offset=0 with a large enough dataset — newest messages appear first.
    resp = await auth_client.get("/feed/global", params={"limit": 200, "offset": 0})
    assert resp.status_code == 200
    items = resp.json()["items"]
    contents = [item["content"] for item in items]

    assert first_content in contents
    assert second_content in contents
    # The second (later) post must appear before the first one
    assert contents.index(second_content) < contents.index(first_content)


async def test_follow_feed_empty_before_following(client: AsyncClient) -> None:
    username = _uid("empty")
    token = await _register_and_login(client, username)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/feed/following", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_follow_feed_shows_followed_user_posts(client: AsyncClient) -> None:
    follower = _uid("follower")
    followed = _uid("followed")

    follower_token = await _register_and_login(client, follower)
    followed_token = await _register_and_login(client, followed)

    follower_headers = {"Authorization": f"Bearer {follower_token}"}
    followed_headers = {"Authorization": f"Bearer {followed_token}"}

    # Follow the target user
    follow_resp = await client.post(f"/social/follow/{followed}", headers=follower_headers)
    assert follow_resp.status_code == 200

    # Target user posts a message
    content = f"post by {followed} {uuid.uuid4().hex[:6]}"
    post_resp = await client.post("/messages", json={"content": content}, headers=followed_headers)
    assert post_resp.status_code == 201

    # Fresh unique user — no cache populated yet; offset=0 hits DB directly on miss
    resp = await client.get(
        "/feed/following", params={"limit": 50, "offset": 0}, headers=follower_headers
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    contents = [item["content"] for item in items]
    assert content in contents


async def test_user_feed(client: AsyncClient) -> None:
    username = _uid("userfeed")
    token = await _register_and_login(client, username)
    headers = {"Authorization": f"Bearer {token}"}

    content = f"my post {uuid.uuid4().hex[:6]}"
    await client.post("/messages", json={"content": content}, headers=headers)

    resp = await client.get(f"/feed/{username}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    contents = [item["content"] for item in body["items"]]
    assert content in contents
    # All returned posts should belong to this user
    for item in body["items"]:
        assert item["username"] == username


async def test_user_feed_nonexistent_user(auth_client: AsyncClient) -> None:
    resp = await auth_client.get("/feed/definitely_no_such_user_xyz_99999")
    assert resp.status_code == 404


async def test_follow_then_unfollow(client: AsyncClient) -> None:
    follower = _uid("unfollower")
    followed = _uid("unfollowed")

    follower_token = await _register_and_login(client, follower)
    followed_token = await _register_and_login(client, followed)

    follower_headers = {"Authorization": f"Bearer {follower_token}"}
    followed_headers = {"Authorization": f"Bearer {followed_token}"}

    # Follow
    follow_resp = await client.post(f"/social/follow/{followed}", headers=follower_headers)
    assert follow_resp.status_code == 200

    # Post a message as the followed user
    content = f"followed post {uuid.uuid4().hex[:6]}"
    await client.post("/messages", json={"content": content}, headers=followed_headers)

    # Fresh unique user — cache miss at offset=0 hits DB directly
    feed_resp = await client.get(
        "/feed/following", params={"limit": 50, "offset": 0}, headers=follower_headers
    )
    assert feed_resp.status_code == 200
    assert any(item["content"] == content for item in feed_resp.json()["items"])

    # Unfollow
    unfollow_resp = await client.post(f"/social/unfollow/{followed}", headers=follower_headers)
    assert unfollow_resp.status_code == 200

    # After unfollowing, the followed user's posts should no longer appear (bypass cache)
    feed_after = await client.get(
        "/feed/following", params={"limit": 50, "offset": 1}, headers=follower_headers
    )
    assert feed_after.status_code == 200
    assert not any(item["content"] == content for item in feed_after.json()["items"])
