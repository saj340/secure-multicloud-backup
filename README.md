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

## Report and defense material

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, flows, design decisions with alternatives, known limitations
- [docs/SECURITY.md](docs/SECURITY.md): each threat, the defence, and where the code and tests are
- [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md): a rehearsed 12-minute demo and likely examiner questions

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

## Scheduled backups

Files and encryption live on the user's computer, so scheduled backups run there too:
in the desktop app, **while it is open and someone is logged in**. The server stores
the schedules and decides when each is due; the app checks once a minute, runs what
is due, and reports the result back.

- A schedule is daily or weekly at a time of day. The time is the user's local time
  (the app sends its UTC offset with the schedule, so "Monday 02:00" means that).
  A daylight-saving change moves it by an hour until the schedule is next edited.
- If the app was closed at the scheduled time, the backup runs when it is next
  opened. Several missed times still mean one run.
- Each run is stored as its own dated backup (for example `Documents 2026-09-21 02:00`),
  so files that appeared or disappeared since last time never leave stale entries.
- A run that can't happen (folder missing, fewer than two providers) is recorded as
  failed and waits for the schedule's next time, rather than retrying every minute.
- It never starts while a backup or restore the user started is running.

Running while the app is closed would need a separate background service, which is
not built.

## Cloud providers

| Type | Notes |
|---|---|
| Local folder | Development and demos only. The API offers it only when `ALLOW_LOCAL_FOLDER_PROVIDERS` is on (defaults to `DEBUG`). |
| Amazon S3 | Bucket, region, access key ID and secret. Always reached at AWS's own addresses. |
| S3-compatible storage | Backblaze B2, Wasabi, Cloudflare R2, MinIO and others: endpoint URL, bucket, keys. |

Provider health is re-checked in the background while someone is logged in: when the Providers
or Dashboard screen opens (at most every 30 seconds) and every 5 minutes. A provider that can't
be reached shows as *Error*, and a warning appears in the app's header on every screen.

Provider credentials are encrypted with the master key before they are stored.
An endpoint URL supplied for S3-compatible storage must be `https` and resolve to
public addresses outside development (`ALLOW_PRIVATE_S3_ENDPOINTS`), so a user can't
aim the server at internal services (server-side request forgery).

## Packaged desktop client (.exe)

```bash
pip install -r requirements-dev.txt
python build_client.py
```

This produces `dist/SecureBackup/SecureBackup.exe`, which runs without Python installed
(about 125 MB with its support files; move the whole `SecureBackup` folder). It is the
same client, so it still needs a server to talk to: start one with
`python -m config.securerun`, or set `BACKUP_API_URL` for another.

- The build runs in `C:\build\smcb` for the same long-path reason as the virtual
  environment, then copies the result into `dist/`.
- The development CA certificate (`certs/dev-ca.pem`) is copied next to the `.exe`,
  and the program trusts it for the local machine only, exactly like the source version.
  A build meant for a real server needs no certificate file, because the server's
  certificate comes from a public CA.
- The executable is not code-signed, so Windows SmartScreen may warn on first launch.
- Checked by launching the built program against the real HTTPS server: it logged in
  and loaded the dashboard, backups and schedules over verified TLS.

## Tests

```bash
pip install -r requirements-dev.txt   # adds moto, a local fake S3 server the tests use
python manage.py test                 # a minute or two; needs no cloud account
```

With the server running, `python tools/rehearse_demo.py` runs the whole demo script end to
end against it and reports PASS or FAIL for each step.
