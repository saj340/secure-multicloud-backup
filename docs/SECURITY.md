# Security Design

For each threat: what could go wrong, what the system does, and where to see it
(code and test). Numbers are the real configured values.

## 1. What is protected, and from whom

| Asset | Threat | Defence |
|---|---|---|
| File contents | A cloud provider reads or leaks them | Encrypted on the client before upload; no provider holds a complete file |
| File contents | A provider is down, deletes or corrupts data | XOR parity rebuilds any one lost fragment; hashes detect corruption |
| Data in transit | Eavesdropping, man-in-the-middle | TLS; the client verifies the server certificate |
| Accounts | Password guessing | Rate limiting, strong hashing, password rules |
| Accounts | A stolen or guessed password | Optional two-factor login: a time-based code from an authenticator app (§5b) |
| Accounts | Stolen session token | Tokens expire after 12 hours; one session per account; password change ends all others |
| Provider credentials | Database leak | Stored encrypted with the master key; never returned by the API |
| Server | Being pointed at internal services (SSRF) | User-supplied endpoints must be https and resolve to public addresses |

## 2. Encryption and keys

- **No custom cryptography.** Encryption uses the `cryptography` library's Fernet
  (AES-128-CBC + HMAC-SHA256, authenticated). Password hashing uses Django's PBKDF2
  with 1,000,000 iterations.
- **Envelope encryption.** Every file version gets a fresh random data key. The key is
  stored only in wrapped (encrypted) form, under the master key `MASTER_KEK` from the
  server's `.env` file. Losing one data key exposes one version of one file.
- **The data key is not derived from the login password.** Password reset works
  without the old password, so a password-derived key would be lost on every reset
  and orphan all backups. Trade-off accepted knowingly: the platform operator could
  decrypt user data. This is disclosed, not hidden.
- **Encryption is local.** The server receives ciphertext only. The plaintext data
  key exists briefly in server memory when issued, and in client memory while in use.
- **Tampering is detected twice:** Fernet's authentication tag fails on modified
  ciphertext, and SHA-256 hashes catch changed fragments before reassembly.
- Code: `protection/crypto.py`, `client/crypto.py`. Tests: `protection/tests.py`
  (includes corrupted and malformed input raising `DecryptionError`).

### 2b. Backing up the master key

The master key is a single point of failure: every wrapped file key and every provider's
credentials depend on it, so losing it (a deleted `.env`, a dead disk) makes every backup
unreadable, however safe the cloud copies are. Three management commands cover this:

| Command | What it does |
|---|---|
| `check_master_key` | Tries the key in `.env` on every stored file key and provider credential and reports how many decrypt. Fails loudly if any does not. With nothing stored it says so instead of claiming success. |
| `backup_master_key FILE` | Writes the key to FILE, encrypted under a passphrase (at least 12 characters, typed twice, never given on the command line). Refuses to overwrite a file, and refuses any location inside the project folder, which is a git repository. It reads the file back and decrypts it before reporting success. |
| `restore_master_key FILE` | Decrypts the backup with the passphrase, runs the same check against the database, and only then shows the `MASTER_KEK=` line to put in `.env`. A key that does not decrypt the stored data is rejected. It changes nothing by itself. |

The passphrase is turned into a key with scrypt (n=2^15, r=8, p=1: deliberately slow and
memory-hungry, so guessing passphrases is expensive) and the key is encrypted with Fernet. The
file holds a random salt, the scrypt settings, a short fingerprint of the key (a hash, so it
identifies the key without revealing it) and the encrypted key. A backup file is treated as
untrusted input: settings that would demand absurd memory or time are refused before any work
is done, the same defensive habit as the restore path checks in §6b.

The backup file and the passphrase must be stored in different places, away from this
computer. This reduces the risk but does not remove it: whoever has both can decrypt every
backup, so they are as sensitive as the key itself.
Code: `protection/keytools.py`, `protection/management/commands/`.
Tests: `protection/test_keytools.py`, including one that replaces the key (stranding the data)
and shows the backup brings it back.

## 3. Fragmentation, redundancy, integrity

- With n healthy providers the ciphertext is cut into n-1 equal data fragments plus
  one parity fragment (the XOR of all of them). Any n-1 fragments rebuild the rest.
- No single provider stores the whole ciphertext when n >= 3. With n = 2 it degrades
  to mirroring, which is stated in the documentation.
- Every fragment has a recorded SHA-256; the whole file has one too. On recovery a
  fragment whose hash does not match is treated exactly like a missing one, and the Activity log
  gets a warning naming the piece and the provider, so a cloud that silently damages data is noticed
  even though the file was still rebuilt from the other pieces.
- If two fragments are lost the recovery fails cleanly and says so; it never returns
  wrong data.
- Code: `protection/fragmentation.py`. Tests: `protection/test_fragmentation.py`,
  provider-outage and corrupted-fragment tests in `backups/test_manager.py` and
  `providers/test_s3.py`.

## 4. Transport security

- The server runs over HTTPS (`python -m config.securerun`).
- The client always verifies certificates. It trusts only the project's development CA
  (`certs/dev-ca.pem`) and only for the local machine. Nothing is installed into the
  operating system's trust store.
- The client refuses plain `http://` to any non-local address.
- The development CA's private key is discarded right after signing the server
  certificate.
- With `DEBUG=False` the server enables HTTPS redirect, secure cookies and HSTS; a
  test runs Django's `check --deploy` against that configuration.
- Tests: `client/test_tls.py`, and `client/test_tls_end_to_end.py`, which starts a real
  TLS server and confirms good certificates work and bad ones are refused.

## 5. Accounts and sessions

- Custom `User` model, login by email, passwords validated (length, common passwords,
  similarity to email) and hashed with PBKDF2.
- **Tokens expire after 12 hours.** Logging in issues a new token and ends the previous
  session. Any 401 anywhere in the app returns to the login window with a notice.
- **Changing the password** requires the current one, applies the same rules, and
  signs out every other session.
- **Password reset** uses a short code (not a link, because the client is a desktop
  application) and is rate limited.
- **Rate limits** (attempts per window, from `RATE_LIMITS`):

| Scope | Limit |
|---|---|
| Login, per account and IP | 5 per 15 minutes |
| Login, per IP across accounts | 30 per 15 minutes |
| Reset code requests, per email | 3 per hour |
| Reset code requests, per IP | 10 per hour |
| Reset code attempts, per IP | 10 per 15 minutes |
| Password change attempts, per user | 5 per 15 minutes |
| Wrong two-factor codes at login, per account (any IP) | 5 per 15 minutes |
| Wrong passwords or codes when managing two-factor, per user | 5 per 15 minutes |
| Registrations, per IP | 20 per hour |

  A blocked request gets HTTP 429 with `Retry-After` and is refused **before** the
  password is checked, so a guesser cannot tell whether a blocked guess was right.
  Unknown emails are counted and answered exactly like real ones, so the login form
  cannot be used to find out who has an account. Counters are stored hashed in the
  database and survive restarts.
- Code: `accounts/`. Tests: `accounts/test_auth.py`, `accounts/test_ratelimit.py`.

## 5b. Two-factor login (TOTP)

A password alone can be guessed, phished or leaked, so a user can turn on a second factor: the
6-digit code an authenticator app (Google Authenticator, Microsoft Authenticator, Authy...)
shows, which changes every 30 seconds (RFC 6238). The codes come from the `pyotp` library.

- **Turning it on** (Settings): the user confirms their password, scans a QR code (or types the
  key) into the app, and enters a code from it. Two-factor only switches on once a code has
  proved the app is set up correctly, so a typo can't lock the user out of their own account.
  Eight one-time **backup codes** are then shown once, for a lost phone.
- **Logging in:** the password is checked first; if the account has two-factor on, the server
  answers "code required" and creates no session until a valid code arrives. A wrong password
  gets the same answer as before, so the "code required" reply never reveals whether an account
  has two-factor, only that the password was right.
- **A code works once.** The server keeps the newest 30-second step it has accepted and refuses
  anything not newer, so a code that was watched over a shoulder or sniffed can't be replayed.
  The check is a single atomic database update, so two requests racing with one code cannot both
  succeed. Codes one period either side of the clock are accepted, for clock drift.
- **Guessing codes:** someone with the password has 1,000,000 possible codes to try. Wrong codes
  count against the normal login limits *and* against a separate limit per account that applies
  from any address (5 per 15 minutes), so spreading guesses over many IP addresses doesn't help.
- **Storage:** the shared secret is encrypted under the master key, like provider credentials,
  and never returned after enrolment. Backup codes (80 random bits each) are stored only as
  SHA-256 hashes, each usable once; using one is recorded as a warning in the activity log.
- **Turning it off, or renewing backup codes,** needs the password *and* a current code (or a
  backup code). Starting enrolment needs the password too, so a stolen session token alone cannot
  enrol an attacker's authenticator or switch protection off. Renewing replaces all old codes.
- **Password reset does not bypass it:** an emailed reset code changes the password, but the next
  login still asks for the second factor.
- Code: `accounts/twofactor.py` and the views in `accounts/views.py`. Tests:
  `accounts/test_twofactor.py`. Mutation testing found one real bug here: full-width and
  Arabic-Indic digits passed `str.isdigit()` yet crashed the constant-time comparison, which only
  accepts ASCII; the check now requires ASCII digits and a test covers it.
- Not built: remembering a trusted device, hardware keys (WebAuthn), SMS codes.

## 6. Provider credentials and server-side request forgery

- Cloud credentials are encrypted with the master key before storage and are never
  included in API responses.
- A user can supply an endpoint URL for S3-compatible storage. Outside development it
  must be `https` and every address it resolves to must be public, so a user cannot
  make the server call internal services such as `localhost` or cloud metadata
  addresses. Amazon S3 is always reached at AWS's own addresses and cannot be pointed
  elsewhere.
- Local-folder providers exist only when `ALLOW_LOCAL_FOLDER_PROVIDERS` is on
  (default: development only), so production users cannot ask the server to write to
  its own disk.
- Code: `providers/endpoints.py`, `providers/manager.py`. Tests:
  `providers/test_endpoints.py`, `providers/test_s3.py`.

## 6b. Restoring files safely

When a whole backup is restored, each file's destination is built from the path stored on the
server. That path is untrusted data: a compromised or buggy server could send one containing
`..` to try to make the app write over files elsewhere on the computer (the same idea as the
"zip slip" attack on archive extractors). The app refuses any path that is absolute, contains
`..`, or resolves to somewhere outside the folder the user chose, and reports it as a failure
instead of writing it. A file is also written under a temporary name and renamed only when it
is complete, so a failed restore never leaves half a file behind.
Code: `safe_target` and `plan_restore` in `client/backup_service.py`.
Tests: `client/test_restore_backup.py`.

## 7. Authorization

Every backup, file, provider, schedule and recovery is owned by a user, and queries
are filtered by the requesting user, so one account cannot read or change another's
data. Object ids belonging to someone else return "not found".

## 8. Auditability

`ActivityLog` records, with timestamps and a severity level:

- **Accounts:** registration, sign-in, sign-out, failed sign-ins (a warning, with the
  email and IP), password changes and password resets.
- **Providers:** connected, disconnected, reconnected (a warning if stored fragments
  were not found at the new location), and *changes* in reachability (a warning when
  one becomes unreachable, an info entry when it recovers; repeated checks of an
  unchanged provider add nothing).
- **Backups:** runs started and finished, files restored or failed to restore, pieces that
  failed their integrity check on recovery (a warning), schedule runs and changes. (The per-file "stored" and "restore started" entries are kept in the
  database as an audit trail but hidden from the Activity screen, so a large backup
  doesn't bury everything else.)

Passwords, tokens and provider credentials are never written to the log. A failed
sign-in is logged identically whether or not the account exists, so the log write
does not become a timing signal for finding accounts. Blocked (rate-limited) attempts
are not logged individually, which keeps a guessing attack from flooding the log.
Users read their own entries in the app's **Activity** screen (`GET /api/monitoring/activity/`,
newest first, at most 500). It returns only the caller's entries and leaves out the
metadata (the email and IP address of failed sign-ins), so the screen shows what happened
and when and nothing more. Administrators can also see all entries in the Django admin.

## 9. What this design does not defend against

- A malicious or compromised **server operator** (holds the master key, and so can also read
  users' two-factor secrets).
- Loss of the master key *without a backup of it*: wrapped data keys become unrecoverable,
  and with them every backup. §2b provides a passphrase-protected backup and a check; it
  works only if the user actually makes and safely stores the backup. Key rotation (re-wrapping
  everything under a new key) and a key management service are not built.
- Simultaneous loss or corruption of **two** providers.
- Malware on the user's own computer (it sees plaintext before encryption).
- Denial of service beyond simple rate limits.

Being explicit about these is part of the design: they are the boundaries of the
claim "secure", not omissions.
