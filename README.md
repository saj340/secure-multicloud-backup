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
