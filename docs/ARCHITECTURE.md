# Architecture Overview

## 1. What the system does

A user selects files or folders on their own computer. The desktop application
encrypts them locally, and the server splits the encrypted data across several
cloud storage providers the user has connected, so that no single provider holds
a complete copy and losing any one provider does not lose the backup. On
recovery the pieces are fetched, checked against recorded hashes, reassembled,
and decrypted on the user's computer.

## 2. Components

```
 +-------------------------+          HTTPS (TLS, certificate verified)          +---------------------------+
 |  Desktop client         |  <------------------------------------------------> |  Django REST API          |
 |  PySide6, runs alone    |     JSON requests, "Authorization: Token ..."       |  accounts  backups        |
 |                         |                                                     |  providers protection     |
 |  - encrypts / decrypts  |                                                     |  monitoring               |
 |  - reads / writes files |                                                     +-------------+-------------+
 |  - runs schedules       |                                                                   |
 +-------------------------+                                          storage adapter interface |
                                                                                               v
                                              +-----------------+   +-----------------+   +-----------------+
                                              | Provider A      |   | Provider B      |   | Provider C      |
                                              | (S3 / B2 / ...) |   | (S3 / B2 / ...) |   | (S3 / B2 / ...) |
                                              +-----------------+   +-----------------+   +-----------------+
```

| Part | Where | Responsibility |
|---|---|---|
| Desktop client | `client/` | GUI, local file access, **encryption and decryption**, scheduler |
| Authentication | `accounts/` | Registration, login, expiring tokens, password reset, rate limiting |
| Backup and recovery | `backups/` | Jobs, files, versions, schedules, recovery operations, the pipeline |
| Cloud providers | `providers/` | Provider records, encrypted credentials, adapters (local folder, S3, S3-compatible) |
| Data protection | `protection/` | Data-key handling, fragmentation with parity, integrity records |
| Monitoring | `monitoring/` | Activity log |

The client is a separate program from the server. It is not a website and needs no
browser.

## 3. Backup flow

1. The client asks the server to create a backup job and start a file version.
2. The server generates a random data key for that version and returns it over
   HTTPS. Only a copy wrapped with the master key is stored.
3. The client encrypts the file **locally** with that key (Fernet: AES-128-CBC with
   an HMAC-SHA256 authentication tag) and uploads only the ciphertext.
4. The server splits the ciphertext into n-1 equal data fragments plus one XOR
   parity fragment, where n is the number of currently healthy providers (at least 2).
5. Each fragment goes to a different provider through the adapter interface. The
   server records where each one is and its SHA-256 hash, plus a whole-file hash.

## 4. Recovery flow

1. The client asks for a recovery of a chosen file version.
2. The server downloads the fragments from the providers that are reachable.
3. Each fragment is checked against its recorded hash. A missing or corrupted
   fragment is treated as lost.
4. If at most one fragment is lost, it is rebuilt from the others by XOR. The
   whole ciphertext is then verified against the whole-file hash.
5. The client receives the ciphertext and the data key, decrypts locally, and
   writes the file.

## 5. Key design decisions

Each row is a choice that had realistic alternatives; the reason is what to say
when asked "why".

| Decision | Chosen | Alternatives considered | Why |
|---|---|---|---|
| Client type | Standalone PySide6 desktop app + JSON API | Web application; Electron | Requirement that it runs as its own program; encryption belongs on the user's machine; Python throughout |
| Backend | Django + Django REST Framework | Flask, FastAPI | Mature auth, ORM, migrations and test tooling; a small team can finish it |
| Authentication | Custom email-based user model, DRF tokens | Session cookies; third-party login | A desktop client has no cookie store; requirement to build accounts ourselves; Django's password hasher is still used, never custom crypto |
| Encryption | Envelope encryption: random key per file version, wrapped by a master key | Key derived from the login password; one key for everything | A password-derived key would make password reset destroy every backup. Per-version keys limit the damage of any one key leaking |
| Where encryption runs | On the client | On the server | Data is protected before it leaves the computer, as the project brief requires |
| Distribution | n-1 data fragments + 1 XOR parity | Full replication; k-of-n Reed-Solomon | Combines fragmentation (no provider holds the whole file) with redundancy (survives one failure) using very simple, testable code. With 2 providers it becomes mirroring. Full replication wastes space; Reed-Solomon is more code than the project needs |
| Providers | User-chosen; any number from 2 | Fixed three providers | The brief says providers depend on which accounts the user has |
| Cloud access | S3 API (covers AWS S3, Backblaze B2, Wasabi, R2, MinIO) | One SDK per vendor | One adapter reaches most providers; new ones need only a small class |
| Scheduling | Runs in the desktop app while open, catches up on next open | Server-side scheduler; OS service | The files and keys live on the user's computer, so the server cannot run a backup for them |
| Database | SQLite in development | PostgreSQL | Zero setup; Django's ORM makes the move a settings change (not yet done) |

## 6. Data model

`User`, `RateLimitCounter` (accounts) - `BackupJob`, `File`, `FileVersion`,
`BackupSchedule`, `RecoveryOperation` (backups) - `CloudProvider`,
`StorageLocation` (providers) - `IntegrityRecord` (protection) - `ActivityLog`
(monitoring).

Relationships that matter: a `BackupJob` holds `File`s; each run adds a new
`FileVersion` per file, so history is never overwritten. A `FileVersion` has
several `StorageLocation`s, one per fragment, each pointing at a provider and
carrying its `fragment_index`. `IntegrityRecord` stores the hashes used to verify
both whole files and individual fragments.

## 7. Size of the work

About 5,050 lines of application code and 3,650 lines of tests (Python, excluding
migrations), 27 API endpoints, 11 database models, 373 automated tests that run in
about one minute and need no cloud account.

| Area | Code | Tests |
|---|---:|---:|
| client | 2,856 | 1,724 |
| backups | 775 | 693 |
| providers | 475 | 593 |
| accounts | 395 | 342 |
| config | 337 | 114 |
| protection | 175 | 179 |
| monitoring | 40 | 90 |

## 8. Known limitations (state these before an examiner finds them)

- **Not zero-knowledge.** The server holds the master key and can decrypt stored
  data keys. This was a conscious choice (see the password-reset reasoning above).
- **Tolerates one provider failure**, not two. XOR parity protects against exactly one.
- **Reconnecting needs the same storage.** A disconnected provider can be reconnected
  in place with new credentials, which makes its stored fragments reachable again. If
  the credentials lead to a different bucket or folder, the app warns that fragments
  were not found, but it cannot prove the two places are the same; it only samples the
  25 most recent fragments.
- **Local-folder providers are for development and demos only.**
- **Scheduled backups need the app open.** A background service is not built.
- **Rate limits are per client IP**, which needs real-address forwarding behind a proxy.
- **DST** shifts a schedule by an hour until it is next edited.
- **One active session per account.**
- **SQLite** is used; the PostgreSQL move is not done.
- **Not tested against a real commercial cloud account** yet; S3 support is tested
  against a local S3 server (moto).
