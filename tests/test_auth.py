from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import Settings
from tests.conftest import login

SECRET = "test-secret-test-secret-test-secret-123"


def make_token(secret=SECRET, **overrides):
    now = datetime.now(timezone.utc)
    claims = {"sub": "acme_employee", "company_id": "acme", "role": "employee",
              "iat": now, "exp": now + timedelta(minutes=5)}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, secret, algorithm="HS256")


def ask(client, token=None, header=None):
    h = header if header is not None else ({"Authorization": f"Bearer {token}"} if token else {})
    return client.post("/questions", json={"question": "Can I book a cab on Saturday?"}, headers=h)


def test_valid_token_is_accepted_and_company_comes_from_token(client, container):
    r = ask(client, make_token())
    assert r.status_code == 200
    assert all(c["document"].startswith("acme_") for c in r.json()["citations"])


def test_login_returns_token_for_the_users_own_company(client):
    h = login(client, "northstar_employee")
    r = client.post("/questions", json={"question": "Can I book a Saturday cab?"}, headers=h)
    assert all(c["document"].startswith("northstar_") for c in r.json()["citations"])


def test_wrong_password_and_unknown_user_rejected(client):
    assert client.post("/auth/token", json={"username": "acme_employee", "password": "nope"}).status_code == 401
    assert client.post("/auth/token", json={"username": "ghost", "password": "demo-pass"}).status_code == 401


def test_login_cannot_pick_a_company(client):
    r = client.post("/auth/token", json={"username": "acme_employee", "password": "demo-pass", "company_id": "orbit"})
    assert r.status_code == 422


def test_missing_token_401_and_no_retrieval(client, container, monkeypatch):
    called = []
    monkeypatch.setattr(container.retriever, "retrieve", lambda *a, **k: called.append(1))
    assert ask(client).status_code == 401
    assert called == []


def test_invalid_signature_401(client):
    assert ask(client, make_token(secret="x" * 40)).status_code == 401


def test_expired_token_401(client):
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert ask(client, make_token(exp=past)).status_code == 401


def test_missing_claims_401(client):
    assert ask(client, make_token(company_id=None)).status_code == 401
    assert ask(client, make_token(role=None)).status_code == 401
    assert ask(client, make_token(sub=None)).status_code == 401


def test_malformed_auth_header_401(client):
    assert ask(client, header={"Authorization": "Bearer"}).status_code == 401
    assert ask(client, header={"Authorization": "Token abc"}).status_code == 401
    assert ask(client, header={"Authorization": "Bearer not.a.jwt"}).status_code == 401


def test_alg_none_token_rejected(client):
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": "acme_employee", "company_id": "acme", "role": "employee",
                        "iat": now, "exp": now + timedelta(minutes=5)}, key=None, algorithm="none")
    assert ask(client, token).status_code == 401


def test_validly_signed_token_with_forged_company_is_rejected(client):
    # Even with a correct signature, the company claim must match the identity store.
    assert ask(client, make_token(company_id="northstar")).status_code == 401


def test_token_for_unknown_user_rejected(client):
    assert ask(client, make_token(sub="ghost")).status_code == 401


def test_employee_cannot_use_admin_routes(client, headers):
    assert client.get("/admin/policies/versions", headers=headers["acme_employee"]).status_code == 403
    assert client.get("/admin/policies/versions", headers={}).status_code == 401


def test_admin_sees_only_own_company(client, headers):
    r = client.get("/admin/policies/active", headers=headers["orbit_admin"])
    assert r.json()["company_id"] == "orbit" and r.json()["active"]["version"] == "v1"


def test_short_jwt_secret_is_refused_at_startup():
    import pytest
    with pytest.raises(Exception):
        Settings(jwt_secret="short")
