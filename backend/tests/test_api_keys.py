"""Organization API-key lifecycle.

The rotate endpoint was unreachable before Phase 0: organization_id had no
type annotation, so FastAPI handed the route a str and SQLAlchemy raised
AttributeError adapting it to a Uuid column. None of this had test coverage.
"""

from datetime import timedelta

from app.clock import utcnow


def create_organization(client, name="Acme Inc"):
    """Returns (organization_id, api_key) for a freshly created org."""
    response = client.post("/organizations", json={"name": name})
    assert response.status_code == 200, response.text
    body = response.json()
    return body["id"], body["api_key"]


def auth(api_key):
    return {"Authorization": f"Bearer {api_key}"}


def test_rotate_issues_a_new_key_and_revokes_the_old_one(api_key_client):
    org_id, first_key = create_organization(api_key_client)

    response = api_key_client.post(
        f"/organizations/{org_id}/api-keys/rotate", headers=auth(first_key)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    second_key = body["api_key"]

    assert second_key != first_key
    assert body["active"] is True
    assert body["revoked_at"] is None
    assert body["expires_at"] is None

    # The replacement works and the key that authorized the rotation no
    # longer does.
    assert api_key_client.get(
        f"/organizations/{org_id}/documents", headers=auth(second_key)
    ).status_code == 200
    assert api_key_client.get(
        f"/organizations/{org_id}/documents", headers=auth(first_key)
    ).status_code == 403


def test_rotate_rejects_missing_and_foreign_credentials(api_key_client):
    org_id, _ = create_organization(api_key_client)
    other_id, other_key = create_organization(api_key_client, "Other Inc")

    assert api_key_client.post(
        f"/organizations/{org_id}/api-keys/rotate"
    ).status_code == 401

    assert api_key_client.post(
        f"/organizations/{org_id}/api-keys/rotate", headers=auth(other_key)
    ).status_code == 403

    # The rejected calls issued nothing.
    keys = api_key_client.get(
        f"/organizations/{other_id}/api-keys", headers=auth(other_key)
    ).json()
    assert len(keys) == 1


def test_listing_keys_returns_metadata_and_never_a_token(api_key_client):
    org_id, first_key = create_organization(api_key_client)
    second_key = api_key_client.post(
        f"/organizations/{org_id}/api-keys/rotate", headers=auth(first_key)
    ).json()["api_key"]

    response = api_key_client.get(
        f"/organizations/{org_id}/api-keys", headers=auth(second_key)
    )

    assert response.status_code == 200, response.text
    keys = response.json()

    assert len(keys) == 2
    assert [key["active"] for key in keys] == [False, True]
    assert keys[0]["revoked_at"] is not None

    for key in keys:
        assert set(key) == {
            "id",
            "created_at",
            "expires_at",
            "revoked_at",
            "active",
        }

    serialized = response.text
    assert first_key not in serialized
    assert second_key not in serialized


def test_revoking_a_key_makes_it_unusable(api_key_client):
    org_id, first_key = create_organization(api_key_client)
    second_key = api_key_client.post(
        f"/organizations/{org_id}/api-keys/rotate", headers=auth(first_key)
    ).json()["api_key"]

    keys = api_key_client.get(
        f"/organizations/{org_id}/api-keys", headers=auth(second_key)
    ).json()
    active_id = next(key["id"] for key in keys if key["active"])

    assert api_key_client.delete(
        f"/organizations/{org_id}/api-keys/{active_id}", headers=auth(second_key)
    ).status_code == 204

    assert api_key_client.get(
        f"/organizations/{org_id}/documents", headers=auth(second_key)
    ).status_code == 403


def test_revoking_an_already_revoked_key_keeps_the_original_timestamp(
    api_key_client,
):
    org_id, first_key = create_organization(api_key_client)
    second_key = api_key_client.post(
        f"/organizations/{org_id}/api-keys/rotate", headers=auth(first_key)
    ).json()["api_key"]

    keys = api_key_client.get(
        f"/organizations/{org_id}/api-keys", headers=auth(second_key)
    ).json()
    revoked = next(key for key in keys if not key["active"])

    assert api_key_client.delete(
        f"/organizations/{org_id}/api-keys/{revoked['id']}",
        headers=auth(second_key),
    ).status_code == 204

    keys = api_key_client.get(
        f"/organizations/{org_id}/api-keys", headers=auth(second_key)
    ).json()
    assert keys[0]["revoked_at"] == revoked["revoked_at"]


def test_a_key_cannot_revoke_another_organizations_key(api_key_client):
    org_id, org_key = create_organization(api_key_client)
    other_id, other_key = create_organization(api_key_client, "Other Inc")

    other_key_id = api_key_client.get(
        f"/organizations/{other_id}/api-keys", headers=auth(other_key)
    ).json()[0]["id"]

    # Right key, wrong organization in the path.
    assert api_key_client.delete(
        f"/organizations/{other_id}/api-keys/{other_key_id}",
        headers=auth(org_key),
    ).status_code == 403

    # Own organization in the path, but the key id belongs elsewhere.
    assert api_key_client.delete(
        f"/organizations/{org_id}/api-keys/{other_key_id}",
        headers=auth(org_key),
    ).status_code == 404

    assert api_key_client.get(
        f"/organizations/{other_id}/documents", headers=auth(other_key)
    ).status_code == 200


def test_expired_keys_are_rejected(api_key_client):
    org_id, first_key = create_organization(api_key_client)

    response = api_key_client.post(
        f"/organizations/{org_id}/api-keys/rotate",
        headers=auth(first_key),
        json={"expires_at": (utcnow() - timedelta(hours=1)).isoformat()},
    )

    assert response.status_code == 200, response.text
    body = response.json()

    # Issued, recorded, and already unusable.
    assert body["active"] is False
    assert body["revoked_at"] is None
    assert api_key_client.get(
        f"/organizations/{org_id}/documents", headers=auth(body["api_key"])
    ).status_code == 403


def test_a_future_expiry_is_stored_and_the_key_works(api_key_client):
    org_id, first_key = create_organization(api_key_client)
    expires_at = utcnow() + timedelta(days=30)

    response = api_key_client.post(
        f"/organizations/{org_id}/api-keys/rotate",
        headers=auth(first_key),
        json={"expires_at": expires_at.isoformat() + "Z"},
    )

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["active"] is True
    assert api_key_client.get(
        f"/organizations/{org_id}/documents", headers=auth(body["api_key"])
    ).status_code == 200

    keys = api_key_client.get(
        f"/organizations/{org_id}/api-keys", headers=auth(body["api_key"])
    ).json()
    assert keys[-1]["expires_at"].startswith(expires_at.isoformat()[:19])
