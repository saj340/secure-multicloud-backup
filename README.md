# Secure Multi-Cloud Backup and Data Protection Platform

Graduation project: a platform that encrypts files locally before distributing
the protected data across multiple third-party cloud storage providers, then
verifies integrity and reconstructs the original file on recovery.

## Architecture

Two separate programs: a Django JSON API backend, and a standalone PySide6
desktop application (no browser involved) that talks to it.

| Component | Module | Responsibility |
|---|---|---|
| `client/` | Client Application | PySide6 desktop GUI, calls the API |
| `accounts` | Authentication & Authorization | Registration, login, profile — exposed as `/api/accounts/` |
| `backups` | Backup & Recovery Manager | BackupJob, File, FileVersion, BackupSchedule, RecoveryOperation |
| `providers` | Cloud Provider Manager | CloudProvider, StorageLocation, provider adapters |
| `protection` | Data Protection Module | Local encryption, integrity hashing |
| `monitoring` | Monitoring & Logging | ActivityLog, dashboard |

Auth uses DRF token authentication (not browser sessions), since the desktop
client has no cookie store — the client sends `Authorization: Token <key>`.

## Setup

The virtual environment lives **outside** this folder, at `C:\venvs\smcb`, not
in a local `venv/`. Reason: this project sits under a deeply nested path
(`...\American University of Maddaba Courses\Fall 2026\...`), and PySide6
ships files with very long internal paths — combined, that exceeds Windows'
260-character path limit and pip fails to install. A short venv path avoids
it entirely without needing to enable Windows Long Path support system-wide.

```bash
python -m venv /c/venvs/smcb
source /c/venvs/smcb/Scripts/activate
pip install -r requirements.txt
cp .env.example .env           # then edit SECRET_KEY and MASTER_KEK
python manage.py migrate
python -m config.devcerts      # once: creates a development CA and server certificate in certs/
python -m config.securerun     # starts the API over HTTPS on https://127.0.0.1:8443
```

In a second terminal, with the same venv active:

```bash
python -m client.main           # launches the desktop client
```

Database is SQLite for now; will move to PostgreSQL once the data model is in place.

## Secure communication (HTTPS)

The client and server talk over TLS, and the client will not send a password,
key or file to a server whose certificate it can't verify.

- `python -m config.devcerts` creates a private development CA and one server
  certificate for `localhost` / `127.0.0.1` in `certs/` (git-ignored). The CA's
  private key is discarded right after signing. The client trusts only
  `certs/dev-ca.pem`, and only for the local machine, so nothing is installed
  into the operating system's trusted certificates.
- The client refuses plain `http://` for any address except the local machine.
- To point the client elsewhere: `BACKUP_API_URL=https://backup.example.com/api`.
  A real server uses a certificate from a public CA, which the system already
  trusts. For a private CA set `BACKUP_CA_CERT` to its certificate file.
- With `DEBUG=False` the server enables HTTPS redirect, secure cookies and HSTS,
  and a test runs Django's `check --deploy` against that configuration.
- For quick local work without TLS, `python manage.py runserver` still works if the
  client is started with `BACKUP_API_URL=http://127.0.0.1:8000/api`; the login
  window then shows "Not encrypted".

The tests expect `DEBUG=True` (as in `.env.example`), since the production settings
redirect plain http to https.

## Accounts and sessions

- A login token stops working `AUTH_TOKEN_LIFETIME_HOURS` (default 12) after it was
  issued. Logging in again issues a new one and ends any earlier session, so an
  account has one active session. When a session has expired the app returns to the
  login window with a notice, from whichever screen made the request.
- Changing the password (Settings) needs the current password, applies the same
  strength rules as registration (including "too similar to your email"), signs out
  every other session, and gives the app that made the change a fresh token.
- Only the name is editable in the profile; the email is the login identity.
- Guessable operations are rate limited (`RATE_LIMITS` in `config/settings.py`, code in
  `accounts/ratelimit.py`): 5 failed logins per IP and email per 15 minutes (30 per IP
  across accounts), 3 reset codes per email per hour, wrong reset codes, wrong current
  passwords in Settings, and sign-ups. A blocked request gets a 429 with `Retry-After`,
  and is refused before the password is checked, so a guesser can't learn whether a
  guess was right. Unknown emails are counted and answered exactly like real ones.
  Counters live in the database (hashed), so they survive restarts.
- Limits are per client IP. Behind a reverse proxy every request appears to come from
  the proxy, so it would need to pass the real client address through before this is
  useful in production.

## Cloud providers

| Type | Notes |
|---|---|
| Local folder | Development and demos only. The API offers it only when `ALLOW_LOCAL_FOLDER_PROVIDERS` is on (defaults to `DEBUG`). |
| Amazon S3 | Bucket, region, access key ID and secret. Always reached at AWS's own addresses. |
| S3-compatible storage | Backblaze B2, Wasabi, Cloudflare R2, MinIO and others: endpoint URL, bucket, keys. |

Provider credentials are encrypted with the master key before they are stored.
An endpoint URL supplied for S3-compatible storage must be `https` and resolve to
public addresses outside development (`ALLOW_PRIVATE_S3_ENDPOINTS`), so a user can't
aim the server at internal services (server-side request forgery).

## Tests

```bash
pip install -r requirements-dev.txt   # adds moto, a local fake S3 server the tests use
python manage.py test                 # a minute or two; needs no cloud account
```
