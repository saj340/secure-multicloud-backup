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
cp .env.example .env           # then edit SECRET_KEY
python manage.py migrate
python manage.py runserver     # starts the API backend on :8000
```

In a second terminal, with the same venv active:

```bash
python -m client.main           # launches the desktop client
```

Database is SQLite for now; will move to PostgreSQL once the data model is in place.

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
python manage.py test                 # takes a few minutes; needs no cloud account
```
