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

## 3. Fragmentation, redundancy, integrity

- With n healthy providers the ciphertext is cut into n-1 equal data fragments plus
  one parity fragment (the XOR of all of them). Any n-1 fragments rebuild the rest.
- No single provider stores the whole ciphertext when n >= 3. With n = 2 it degrades
  to mirroring, which is stated in the documentation.
- Every fragment has a recorded SHA-256; the whole file has one too. On recovery a
  fragment whose hash does not match is treated exactly like a missing one.
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
| Registrations, per IP | 20 per hour |

  A blocked request gets HTTP 429 with `Retry-After` and is refused **before** the
  password is checked, so a guesser cannot tell whether a blocked guess was right.
  Unknown emails are counted and answered exactly like real ones, so the login form
  cannot be used to find out who has an account. Counters are stored hashed in the
  database and survive restarts.
- Code: `accounts/`. Tests: `accounts/test_auth.py`, `accounts/test_ratelimit.py`.

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

## 7. Authorization

Every backup, file, provider, schedule and recovery is owned by a user, and queries
are filtered by the requesting user, so one account cannot read or change another's
data. Object ids belonging to someone else return "not found".

## 8. Auditability

`ActivityLog` records, with timestamps and a severity level:

- **Accounts:** registration, sign-in, sign-out, failed sign-ins (a warning, with the
  email and IP), password changes and password resets.
- **Providers:** connected, disconnected, and *changes* in reachability (a warning when
  one becomes unreachable, an info entry when it recovers; repeated checks of an
  unchanged provider add nothing).
- **Backups:** runs started and finished, recoveries started, completed and failed,
  schedule changes.

Passwords, tokens and provider credentials are never written to the log. A failed
sign-in is logged identically whether or not the account exists, so the log write
does not become a timing signal for finding accounts. Blocked (rate-limited) attempts
are not logged individually, which keeps a guessing attack from flooding the log.
There is no screen for reading the log yet; entries are visible in the Django admin.

## 9. What this design does not defend against

- A malicious or compromised **server operator** (holds the master key).
- Loss of the master key: wrapped data keys become unrecoverable, and with them
  every backup. In production the key needs its own protected backup or a key
  management service.
- Simultaneous loss or corruption of **two** providers.
- Malware on the user's own computer (it sees plaintext before encryption).
- Denial of service beyond simple rate limits.

Being explicit about these is part of the design: they are the boundaries of the
claim "secure", not omissions.
