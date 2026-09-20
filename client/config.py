"""
Where the client connects, and which certificates it trusts.

    BACKUP_API_URL   the server's API address (default https://127.0.0.1:8443/api)
    BACKUP_CA_CERT   a CA certificate file to trust for the server, instead of the
                     system's trusted CAs (used with a development server)

Passwords, keys and file data must never cross a network unencrypted, so plain
http is only accepted for the local machine itself.
"""

import ipaddress
import os
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_API_URL = "https://127.0.0.1:8443/api"
DEV_CA = Path(__file__).resolve().parent.parent / "certs" / "dev-ca.pem"


def api_url() -> str:
    return os.environ.get("BACKUP_API_URL", DEFAULT_API_URL)


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def require_safe_transport(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and is_loopback(parsed.hostname or ""):
        return
    raise ValueError(
        f"Refusing to connect to {url}: passwords and keys must not cross a network "
        "unencrypted. Use an https:// address."
    )


def ca_cert_for(url: str):
    """The CA certificate file to trust for this server, or None for the system's CAs."""
    explicit = os.environ.get("BACKUP_CA_CERT")
    if explicit:
        return explicit
    # The development CA is only ever trusted for the local machine, so it can't
    # weaken verification of a real server elsewhere.
    parsed = urlparse(url)
    if parsed.scheme == "https" and is_loopback(parsed.hostname or "") and DEV_CA.exists():
        return str(DEV_CA)
    return None
