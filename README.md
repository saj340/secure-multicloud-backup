# Secure Multi-Cloud Backup and Data Protection Platform

Graduation project: a platform that encrypts files locally before distributing
the protected data across multiple third-party cloud storage providers, then
verifies integrity and reconstructs the original file on recovery.

## Architecture

Each app maps to one module of the system design:

| App | Module | Responsibility |
|---|---|---|
| `accounts` | Authentication & Authorization | Registration, login, profile |
| `backups` | Backup & Recovery Manager | BackupJob, File, FileVersion, BackupSchedule, RecoveryOperation |
| `providers` | Cloud Provider Manager | CloudProvider, StorageLocation, provider adapters |
| `protection` | Data Protection Module | Local encryption, integrity hashing |
| `monitoring` | Monitoring & Logging | ActivityLog, dashboard |

## Setup

```bash
python -m venv venv
source venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
cp .env.example .env           # then edit SECRET_KEY
python manage.py migrate
python manage.py runserver
```

Database is SQLite for now; will move to PostgreSQL once the data model is in place.
