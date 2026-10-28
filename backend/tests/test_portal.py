import httpx
import pytest

from zenkai import portal


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(portal, "state_path", tmp_path / "portal.json")
    monkeypatch.setattr(portal.config, "lab_token", "test-internal-token")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=portal.app), base_url="http://test")


async def test_directory_contract_drift_and_repair(client):
    async with client:
        assert (await client.get("/api/portal")).json()["signature"] == "northbridge-portal-v1"
        headers = {"X-Lab-Token": "test-internal-token"}
        assert (await client.post("/internal/config", headers=headers,
                                 json={"revision": 1, "provider_schema": "v2"})).status_code == 200
        assert (await client.get("/api/portal")).status_code == 502
        assert (await client.post("/internal/config", headers=headers,
                                 json={"revision": 2, "adapter_field": "offerings"})).status_code == 200
        assert (await client.get("/api/portal")).status_code == 200


async def test_control_requires_token_and_current_revision(client):
    async with client:
        assert (await client.get("/internal/state")).status_code == 403
        assert (await client.post("/internal/config", headers={"X-Lab-Token": "test-internal-token"},
                                 json={"revision": 999, "capacity": 2})).status_code == 409


def test_gateway_target_is_not_arbitrary():
    from zenkai.connector import gateway_config
    with pytest.raises(ValueError):
        gateway_config(22)
    assert "portal:8000" in gateway_config(8000)


def test_gateway_initialisation_replaces_image_seed_but_preserves_existing_state(tmp_path):
    from zenkai.config import Settings
    from zenkai.connector import initialise_gateway, write_gateway
    config = Settings(_env_file=None, gateway_config_dir=tmp_path)
    (tmp_path / "default.conf").write_text("proxy_pass http://api:8000;")
    initialise_gateway(config)
    assert "portal:8000" in (tmp_path / "default.conf").read_text()
    write_gateway(config, 8999)
    initialise_gateway(config)
    assert "portal:8999" in (tmp_path / "default.conf").read_text()
