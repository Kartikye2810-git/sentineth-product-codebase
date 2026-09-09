"""Real credentials on every tenant route; no shared authentication override."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.clock import utcnow
from app.db.models import AuditEvent, Invitation, Membership, OrganizationApiKey, User, UserSession
from app.security import hash_api_key
from tests.pdf_builder import build_pdf


def auth(token):
    return {"Authorization": "Bearer " + token}


def tenant(client):
    response = client.post("/organizations", json={"name": "Phase 3"})
    assert response.status_code == 200, response.text
    return response.json()


def role_key(client, root, role):
    response = client.post(root + "/api-keys", json={"role": role})
    assert response.status_code == 201, response.text
    return response.json()


def routes(root):
    missing = str(uuid4())
    return [
        ("GET", "/sources", {}),
        ("GET", f"/sources/{missing}", {}),
        ("GET", "/documents", {}),
        ("GET", f"/documents/{missing}", {}),
        (
            "POST",
            "/documents",
            {"files": {"file": ("a.pdf", build_pdf(["Example policy"]), "application/pdf")}},
        ),
        ("POST", f"/documents/{missing}/reindex", {}),
        ("DELETE", f"/documents/{missing}", {}),
        ("POST", "/query", {"json": {"query": "policy"}}),
        ("POST", "/search", {"json": {"query": "policy"}}),
        ("GET", "/api-keys", {}),
        ("POST", "/api-keys", {"json": {}}),
        ("POST", "/api-keys/rotate", {}),
        ("DELETE", f"/api-keys/{missing}", {}),
        ("GET", "/members", {}),
        ("PATCH", f"/members/{missing}", {"json": {"role": "owner"}}),
        ("DELETE", f"/members/{missing}", {}),
        ("POST", "/invitations", {"json": {"email": "person@example.com"}}),
        ("GET", "/invitations", {}),
        ("DELETE", f"/invitations/{missing}", {}),
        ("GET", "/audit-events", {}),
        ("GET", "/usage", {}),
    ]


@pytest.mark.parametrize(
    "credential",
    [
        "missing",
        "wrong_scheme",
        "malformed",
        "expired",
        "revoked",
        "foreign",
        "expired_session",
        "revoked_session",
        "foreign_session",
    ],
)
def test_every_tenant_route_enforces_credentials(client, db_session_factory, credential):
    org = tenant(client)
    root = "/organizations/" + org["id"]
    headers = auth("not-a-real-credential")
    if credential == "missing":
        headers = {"Authorization": ""}
    elif credential == "wrong_scheme":
        headers = {"Authorization": "Basic anything"}
    elif credential == "foreign":
        headers = auth(tenant(client)["api_key"])
    elif credential in {"expired", "revoked"}:
        headers = auth(org["api_key"])
        with db_session_factory() as db:
            key = db.scalar(
                select(OrganizationApiKey).where(
                    OrganizationApiKey.token_hash == hash_api_key(org["api_key"])
                )
            )
            setattr(
                key,
                "expires_at" if credential == "expired" else "revoked_at",
                utcnow() - timedelta(seconds=1),
            )
            db.commit()
    elif credential.endswith("session"):
        headers = client.owner_headers
        with db_session_factory() as db:
            session = db.scalar(select(UserSession))
            if credential == "foreign_session":
                db.delete(db.get(Membership, (UUID(client.owner_id), UUID(org["id"]))))
            else:
                setattr(
                    session,
                    "expires_at" if credential == "expired_session" else "revoked_at",
                    utcnow() - timedelta(seconds=1),
                )
            db.commit()
    expected = 401 if credential in {"missing", "wrong_scheme"} else 403
    for method, path, kwargs in routes(root):
        response = client.request(method, root + path, headers=headers, **kwargs)
        assert response.status_code == expected, (credential, method, path, response.text)


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_scoped_keys_cannot_administer_or_escalate(client, role):
    org = tenant(client)
    root = "/organizations/" + org["id"]
    key = role_key(client, root, role)
    headers = auth(key["api_key"])
    assert client.get(root + "/documents", headers=headers).status_code == 200
    for method, path, kwargs in routes(root):
        if (
            path == "/api-keys/rotate"
            or path.startswith(("/documents", "/sources"))
            or path in {"/search", "/query"}
        ):
            continue
        assert client.request(method, root + path, headers=headers, **kwargs).status_code == 403, (
            path
        )
    response = client.post(
        root + "/documents",
        headers=headers,
        files={"file": ("a.pdf", build_pdf(["Policy"]), "application/pdf")},
    )
    assert response.status_code == (202 if role == "member" else 403)
    if role == "viewer":
        for method, path in [
            ("DELETE", "/documents/" + str(uuid4())),
            ("POST", "/documents/" + str(uuid4()) + "/reindex"),
        ]:
            assert client.request(method, root + path, headers=headers).status_code == 403
    replacement = client.post(root + "/api-keys/rotate", headers=headers)
    assert replacement.status_code == 200
    assert replacement.json()["role"] == role
    assert client.get(root + "/documents", headers=headers).status_code == 403


def test_invitation_join_role_changes_removal_and_last_owner(client, db_session_factory):
    org = tenant(client)
    root = "/organizations/" + org["id"]
    invite = client.post(
        root + "/invitations", json={"email": " New@Example.com ", "role": "viewer"}
    )
    assert invite.status_code == 201, invite.text
    payload = {
        "email": "new@example.com",
        "password": "new-password-long",
        "token": invite.json()["token"],
    }
    joined = client.post("/auth/accept-invitation", json=payload)
    assert joined.status_code == 200, joined.text
    headers = auth(joined.json()["access_token"])
    user = client.get("/auth/me", headers=headers).json()
    assert client.post("/auth/accept-invitation", json=payload).status_code == 400
    assert client.get(root + "/documents", headers=headers).status_code == 200
    assert client.get(root + "/members", headers=headers).status_code == 403
    # A machine owner cannot add human owners.
    assert (
        client.post(
            root + "/invitations",
            headers=auth(org["api_key"]),
            json={"email": "another@example.com"},
        ).status_code
        == 403
    )
    assert (
        client.patch(root + "/members/" + client.owner_id, json={"role": "viewer"}).status_code
        == 409
    )
    assert client.delete(root + "/members/" + client.owner_id).status_code == 409
    assert client.patch(root + "/members/" + user["id"], json={"role": "owner"}).status_code == 204
    assert client.get(root + "/members", headers=headers).status_code == 200
    assert client.delete(root + "/members/" + user["id"]).status_code == 204
    assert client.get(root + "/documents", headers=headers).status_code == 403
    with db_session_factory() as db:
        stored = db.scalar(select(Invitation))
        assert stored.token_hash != payload["token"]
        account = db.get(User, UUID(user["id"]))
        assert account.password_hash.startswith("$argon2id$")
    events = client.get(root + "/audit-events").json()
    assert {
        "invitation.created",
        "membership.accepted",
        "membership.role_changed",
        "membership.removed",
    } <= {e["action"] for e in events}
    assert payload["token"] not in str(events)


@pytest.mark.parametrize("state", ["expired", "revoked", "wrong_email", "wrong_token"])
def test_invalid_invitations_do_not_create_accounts(client, db_session_factory, state):
    root = "/organizations/" + tenant(client)["id"]
    invitation = client.post(root + "/invitations", json={"email": "new@example.com"}).json()
    if state in {"expired", "revoked"}:
        with db_session_factory() as db:
            stored = db.get(Invitation, UUID(invitation["id"]))
            setattr(
                stored,
                "expires_at" if state == "expired" else "revoked_at",
                utcnow() - timedelta(seconds=1),
            )
            db.commit()
    payload = {
        "email": "wrong@example.com" if state == "wrong_email" else "new@example.com",
        "password": "new-password-long",
        "token": "x" * 32 if state == "wrong_token" else invitation["token"],
    }
    assert client.post("/auth/accept-invitation", json=payload).status_code == 400
    with db_session_factory() as db:
        assert len(list(db.scalars(select(User)))) == 1


def test_login_logout_password_change_and_throttle(client, monkeypatch):
    payload = {"email": "owner@example.com", "password": "test-password-long"}
    assert (
        client.post(
            "/organizations", json={"name": "No login"}, headers={"Authorization": ""}
        ).status_code
        == 401
    )
    assert client.post("/auth/login", json={**payload, "password": "wrong"}).status_code == 401
    login = client.post("/auth/login", json=payload)
    assert login.status_code == 200
    headers = auth(login.json()["access_token"])
    assert client.post("/auth/logout", headers=headers).status_code == 204
    assert client.get("/auth/me", headers=headers).status_code == 403
    assert (
        client.post(
            "/auth/change-password",
            json={"current_password": payload["password"], "new_password": "replacement-password"},
        ).status_code
        == 204
    )
    assert client.get("/auth/me").status_code == 403
    assert client.post("/auth/login", json=payload).status_code == 401
    assert (
        client.post("/auth/login", json={**payload, "password": "replacement-password"}).status_code
        == 200
    )
    for _ in range(11):
        last = client.post(
            "/auth/login", json={"email": "missing@example.com", "password": "wrong"}
        )
    assert last.status_code == 429


def test_audit_is_immutable_and_document_actor_survives_deletion(client, db_session_factory):
    org = tenant(client)
    root = "/organizations/" + org["id"]
    response = client.post(
        root + "/documents",
        headers={"X-Request-ID": "upload-test"},
        files={"file": ("a.pdf", build_pdf(["Policy"]), "application/pdf")},
    )
    assert response.status_code == 202
    doc = response.json()["id"]
    client.process_one()
    assert client.delete(root + "/documents/" + doc).status_code == 202
    client.process_one()
    events = client.get(root + "/audit-events").json()
    upload = next(e for e in events if e["action"] == "document.upload_queued")
    assert upload["actor_id"] == client.owner_id and upload["request_id"] == "upload-test"
    assert any(e["action"] == "document.deleted" and e["resource_id"] == doc for e in events)
    with db_session_factory() as db:
        event = db.scalar(select(AuditEvent))
        event.action = "tampered"
        with pytest.raises(ValueError, match="append-only"):
            db.commit()


def test_readiness_metrics_and_secret_validation(client, monkeypatch, tmp_path):
    from app import health
    from app.settings import Settings, get_settings

    monkeypatch.setattr(health, "database_ready", lambda _: True)
    monkeypatch.setattr(health, "vectors_ready", lambda: True)
    monkeypatch.setenv("SENTINETH_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("SENTINETH_METRICS_TOKEN", "m" * 32)
    get_settings.cache_clear()
    assert client.get("/ready").status_code == 200
    monkeypatch.setattr(health, "vectors_ready", lambda: False)
    assert client.get("/ready").status_code == 503
    assert client.get("/live").status_code == 200
    assert client.get("/metrics").status_code == 401
    metrics = client.get("/metrics", headers=auth("m" * 32))
    assert metrics.status_code == 200
    assert "sentineth_http_duration_seconds" in metrics.text
    assert client.owner_headers["Authorization"] not in metrics.text
    response = client.get("/live", headers={"X-Request-ID": "x" * 200})
    assert len(response.headers["X-Request-ID"]) == 32
    with pytest.raises(ValueError):
        Settings(
            _env_file=None, DATABASE_URL="sqlite://", environment="production"
        ).validate_runtime()


def test_validation_never_echoes_credentials(client):
    secret = "must-not-appear-in-response"
    response = client.post(
        "/auth/accept-invitation", json={"email": "invalid", "password": secret, "token": secret}
    )
    assert response.status_code == 422
    assert secret not in response.text
    response = client.post(
        "/auth/login", json={"email": "owner@example.com", "password": secret * 10}
    )
    assert response.status_code == 422
    assert secret not in response.text


def test_usage_is_attributed_and_unknown_cost_is_not_zero(client, db_session_factory):
    from types import SimpleNamespace

    from app.observability import capture_usage, finish_query, usage_context

    org = tenant(client)
    usage = []
    token = usage_context.set(usage)
    try:
        capture_usage(
            SimpleNamespace(
                model="test-model",
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20, cost=0.002),
            ),
            "fallback",
        )
        capture_usage(
            SimpleNamespace(
                model="test-model", usage=SimpleNamespace(prompt_tokens=50, completion_tokens=10)
            ),
            "fallback",
        )
    finally:
        usage_context.reset(token)
    with db_session_factory() as db:
        finish_query(db, UUID(org["id"]), "query", 1, usage)
    response = client.get("/organizations/" + org["id"] + "/usage")
    assert response.status_code == 200
    assert response.json() == [
        {
            "model": "test-model",
            "requests": 2,
            "prompt_tokens": 150,
            "completion_tokens": 30,
            "reported_cost_usd": 0.002,
            "requests_with_reported_cost": 1,
        }
    ]


def test_settings_use_secret_files_without_exposing_values(tmp_path, monkeypatch):
    from app.settings import Settings

    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    (tmp_path / "DATABASE_URL").write_text(
        "postgresql+psycopg://user:private-password@localhost/example"
    )
    (tmp_path / "NVIDIA_API_KEY").write_text("private-nvidia-key")
    settings = Settings(_env_file=None, _secrets_dir=tmp_path)
    assert settings.nvidia_api_key.get_secret_value() == "private-nvidia-key"
    assert "private-password" not in repr(settings)
    assert "private-nvidia-key" not in repr(settings)
