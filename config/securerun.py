"""
Serve the API over HTTPS:

    python -m config.securerun

Needs the certificates from `python -m config.devcerts`. Host and port can be
changed with BACKUP_HOST and BACKUP_PORT (defaults 127.0.0.1 and 8443).
"""

import os
import sys

import uvicorn

from .devcerts import DEFAULT_DIRECTORY


def main():
    cert = DEFAULT_DIRECTORY / "server-cert.pem"
    key = DEFAULT_DIRECTORY / "server-key.pem"
    if not (cert.exists() and key.exists()):
        sys.exit("No server certificate found. Create one first:\n\n    python -m config.devcerts\n")

    uvicorn.run(
        "config.asgi:application",
        host=os.environ.get("BACKUP_HOST", "127.0.0.1"),
        port=int(os.environ.get("BACKUP_PORT", "8443")),
        ssl_certfile=str(cert),
        ssl_keyfile=str(key),
    )


if __name__ == "__main__":
    main()
