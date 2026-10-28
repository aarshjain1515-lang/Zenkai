"""Create ignored local configuration without printing or overwriting credentials."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
target = root / ".env"
lines = target.read_text().splitlines() if target.exists() else []
existing = {line.split("=", 1)[0]: line.split("=", 1)[1] for line in lines if "=" in line and not line.startswith("#")}
defaults = {
    "GRAFANA_ADMIN_PASSWORD": secrets.token_urlsafe(24),
    "DATABASE_URL": "", "DATABASE_URL_UNPOOLED": "", "OPENROUTER_API_KEY": "",
    "OPENROUTER_MODEL": "nvidia/nemotron-3.5-super-vl:free",
    "OPERATOR_PASSWORD": secrets.token_urlsafe(18), "SESSION_SECRET": secrets.token_urlsafe(32),
    "LAB_TOKEN": secrets.token_urlsafe(32), "AUTO_APPROVE_SAFE": "true",
}
for key, value in defaults.items():
    if key not in existing:
        lines.append(f"{key}={value}")
    elif not existing[key] and value:
        lines = [f"{key}={value}" if line.startswith(f"{key}=") else line for line in lines]
target.write_text("\n".join(lines) + "\n")
target.chmod(0o600)
(root / "logs").mkdir(exist_ok=True)
print("Local .env is ready. Existing credentials were preserved; no secret values were printed.")
print("Add the Neon connection strings and OpenRouter key before enabling recovery.")
