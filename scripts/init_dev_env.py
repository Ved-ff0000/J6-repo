"""Create a local .env with a random signing key; never overwrite existing configuration."""

import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
destination = root / ".env"
try:
    with destination.open("x", encoding="utf-8") as output:
        destination.chmod(0o600)
        template = (root / ".env.example").read_text(encoding="utf-8")
        # Keep local Python and Compose MinIO credentials aligned without printing either.
        storage_secret = secrets.token_urlsafe(36)
        template = template.replace("leaseguard-local-storage-only", storage_secret)
        template = template.replace("leaseguard-local-graph-only", secrets.token_urlsafe(36))
        output.write(template)
        output.write(f"\nLEASEGUARD_JWT_SECRET={secrets.token_urlsafe(48)}\n")
except FileExistsError:
    raise SystemExit(".env already exists; left unchanged. Configure its signing key manually.") from None
print("Created .env with a random development signing key (not displayed).")
