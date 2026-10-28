import httpx

from zenkai.api import create_app
from zenkai.config import Settings


async def test_operator_session_and_csrf():
    config = Settings(_env_file=None, operator_password="local-test-password", session_secret="test-session-key")
    app = create_app(config)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/lab", json={"kind": "crash"})).status_code == 401
        assert (await client.post("/api/login", json={"password": "wrong"})).status_code == 401
        login = (await client.post("/api/login", json={"password": "local-test-password"})).json()
        assert login["authenticated"]
        assert (await client.post("/api/lab", json={"kind": "crash"})).status_code == 403
        assert (await client.post("/api/lab", json={"kind": "crash"},
                                  headers={"X-CSRF-Token": login["csrf"]})).status_code == 503


async def test_no_credentials_does_not_fabricate_model_health():
    config = Settings(_env_file=None)
    assert "openrouter_api_key" in config.missing()
    from zenkai.intelligence import diagnose
    import pytest
    with pytest.raises(ValueError, match="not configured"):
        await diagnose(config, [{"source": "loki"}])


async def test_https_session_cookie_and_logout():
    config = Settings(_env_file=None, operator_password="test-password",
                      session_secret="test-secret", secure_cookies=True)
    app = create_app(config)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        response = await client.post("/api/login", json={"password": "test-password"})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert "; secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
        assert (await client.get("/api/session")).json()["authenticated"]
        assert (await client.post("/api/logout")).status_code == 403
        csrf = response.json()["csrf"]
        assert (await client.post("/api/logout", headers={"X-CSRF-Token": csrf})).status_code == 200
        assert not (await client.get("/api/session")).json()["authenticated"]
