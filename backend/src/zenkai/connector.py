import asyncio
import json

import docker
import httpx


def gateway_config(port):
    if port not in (8000, 8999):
        raise ValueError("Unregistered upstream port")
    return '''log_format zenkai escape=json '{"service":"gateway","level":"info","status":$status,"request":"$request_method $uri","duration":$request_time,"upstream":"$upstream_addr"}';
server {
    listen 80;
    resolver 127.0.0.11 valid=5s ipv6=off;
    root /usr/share/nginx/html;
    index index.html;
    access_log /var/log/zenkai/gateway.log zenkai;
    error_log /var/log/zenkai/gateway-error.log warn;
    location /api/ {
        set $portal_host portal:PORT;
        proxy_pass http://$portal_host$request_uri;
        proxy_connect_timeout 2s;
        proxy_read_timeout 5s;
    }
    location / { try_files $uri /index.html; }
}
'''.replace("PORT", str(port))


def read_gateway(config):
    path = config.gateway_config_dir / "state.json"
    return json.loads(path.read_text()) if path.exists() else {"revision": 1, "upstream_port": 8000}


def initialise_gateway(config):
    config.gateway_config_dir.mkdir(parents=True, exist_ok=True)
    # Docker may seed a fresh volume with the web image's console config.
    # Only our state marker proves the protected-target gateway was initialised.
    if not (config.gateway_config_dir / "state.json").exists():
        write_gateway(config, 8000)
    elif read_gateway(config).get("template_version") != 2:
        write_gateway(config, read_gateway(config)["upstream_port"])


def write_gateway(config, port):
    config.gateway_config_dir.mkdir(parents=True, exist_ok=True)
    state = read_gateway(config)
    for name, value in {"default.conf": gateway_config(port), "state.json": json.dumps({
        "revision": state["revision"] + 1, "upstream_port": port, "template_version": 2})}.items():
        temporary = config.gateway_config_dir / f"{name}.tmp"
        temporary.write_text(value)
        temporary.replace(config.gateway_config_dir / name)


async def probe(config):
    started = asyncio.get_running_loop().time()
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{config.gateway_url}/api/portal")
        try:
            body = response.json()
        except ValueError:
            body = {}
        return {"healthy": response.status_code == 200 and body.get("signature") == "northbridge-portal-v1",
                "status_code": response.status_code, "latency_ms": round((asyncio.get_running_loop().time() - started) * 1000),
                "error": body.get("detail") if response.status_code != 200 else None}
    except httpx.HTTPError:
        return {"healthy": False, "status_code": 0, "latency_ms": 3000, "error": "Connection failed"}


class Connector:
    def __init__(self, config):
        self.config = config

    def container(self, service):
        if service not in ("portal", "gateway"):
            raise ValueError("Unregistered service")
        client = docker.from_env()
        container = client.containers.get(f"{self.config.docker_project}-{service}-1")
        labels = container.labels
        if labels.get("com.docker.compose.project") != self.config.docker_project or labels.get("com.docker.compose.service") != service:
            raise ValueError("Docker target is outside the Zenkai project")
        return container

    async def portal_state(self):
        async with httpx.AsyncClient(timeout=4) as client:
            response = await client.get(f"{self.config.portal_url}/internal/state", headers={"X-Lab-Token": self.config.lab_token})
            response.raise_for_status()
            return response.json()

    async def snapshot(self):
        container = await asyncio.to_thread(self.container, "portal")
        await asyncio.to_thread(container.reload)
        running = container.status == "running"
        state = await self.portal_state() if running else {"revision": 0, "provider_schema": "unknown", "adapter_field": "unknown", "capacity": 0}
        return {"gateway": read_gateway(self.config), "portal": {**state, "running": running}}

    async def mutate_portal(self, **changes):
        current = await self.portal_state()
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(f"{self.config.portal_url}/internal/config", headers={"X-Lab-Token": self.config.lab_token},
                                         json={"revision": current["revision"], **changes})
            response.raise_for_status()
            return response.json()

    async def reload_gateway(self):
        container = await asyncio.to_thread(self.container, "gateway")
        for command in (["nginx", "-t"], ["nginx", "-s", "reload"]):
            result = await asyncio.to_thread(container.exec_run, command)
            if result.exit_code:
                raise ValueError("Gateway validation or reload failed")

    async def inject(self, fault):
        if fault == "upstream":
            write_gateway(self.config, 8999)
            await self.reload_gateway()
        elif fault == "schema":
            await self.mutate_portal(provider_schema="v2")
        elif fault == "overload":
            await self.mutate_portal(capacity=2)
        elif fault == "crash":
            container = await asyncio.to_thread(self.container, "portal")
            await asyncio.to_thread(container.kill)
        else:
            raise ValueError("Unsupported failure injection")

    async def execute(self, proposal):
        if proposal.action == "restore_upstream":
            previous = read_gateway(self.config)["upstream_port"]
            write_gateway(self.config, proposal.upstream_port)
            try:
                await self.reload_gateway()
            except Exception:
                write_gateway(self.config, previous)
                await self.reload_gateway()
                raise
        elif proposal.action == "restart_service":
            container = await asyncio.to_thread(self.container, "portal")
            await asyncio.to_thread(container.start)
        elif proposal.action == "patch_schema_mapping":
            await self.mutate_portal(adapter_field=proposal.schema_field)
        elif proposal.action == "set_capacity":
            await self.mutate_portal(capacity=proposal.capacity)

    async def reset(self):
        container = await asyncio.to_thread(self.container, "portal")
        await asyncio.to_thread(container.reload)
        if container.status != "running":
            await asyncio.to_thread(container.start)
            await asyncio.sleep(2)
        await self.mutate_portal(reset=True)
        write_gateway(self.config, 8000)
        await self.reload_gateway()


if __name__ == "__main__":
    from .config import settings
    initialise_gateway(settings())
