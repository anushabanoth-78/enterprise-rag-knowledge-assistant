from app.core.config import settings
from tests.helpers import register


def test_register_login_me(client):
    h = register(client, "Alice@Example.com")
    me = client.get("/api/auth/me", headers=h).json()
    assert me["email"] == "alice@example.com" and me["is_admin"] is False
    r = client.post("/api/auth/login", data={"username": "alice@example.com", "password": "correct-horse-battery"})
    assert r.status_code == 200 and r.json()["token_type"] == "bearer"


def test_duplicate_email_conflict(client):
    register(client, "a@example.com")
    r = client.post("/api/auth/register", json={"email": "A@example.com", "password": "another-password"})
    assert r.status_code == 409


def test_validation_errors(client):
    assert client.post("/api/auth/register", json={"email": "nope", "password": "longenough1"}).status_code == 422
    assert client.post("/api/auth/register", json={"email": "a@b.co", "password": "short"}).status_code == 422


def test_wrong_password_and_unknown_user_look_identical(client):
    register(client, "a@example.com")
    bad_pw = client.post("/api/auth/login", data={"username": "a@example.com", "password": "wrong-password"})
    no_user = client.post("/api/auth/login", data={"username": "ghost@example.com", "password": "wrong-password"})
    assert bad_pw.status_code == no_user.status_code == 401
    assert bad_pw.json() == no_user.json()


def test_protected_routes_require_a_valid_token(client):
    assert client.get("/api/documents").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_logout_revokes_the_token(client):
    h = register(client, "a@example.com")
    assert client.get("/api/auth/me", headers=h).status_code == 200
    assert client.post("/api/auth/logout", headers=h).status_code == 204
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_login_is_rate_limited_per_ip(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 3)
    codes = [client.post("/api/auth/login", data={"username": "a@example.com", "password": "x" * 9}).status_code
             for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]


def test_user_api_rate_limit(client, monkeypatch):
    h = register(client, "a@example.com")
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    codes = [client.get("/api/documents", headers=h).status_code for _ in range(4)]
    assert codes == [200, 200, 429, 429]


def test_admin_email_gets_admin_flag(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", "boss@example.com, other@example.com")
    boss = register(client, "boss@example.com")
    assert client.get("/api/auth/me", headers=boss).json()["is_admin"] is True


def test_health_endpoints(client):
    assert client.get("/health").json() == {"status": "ok"}
    r = client.get("/health/ready")
    assert r.json()["database"] == "ok"
