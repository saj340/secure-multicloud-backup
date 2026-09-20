# Demo Script

Total time about 12 minutes. Every step says what to click, what to say, and what
the audience should see. Rehearse it once end to end the day before.

## The day before: run the automated rehearsal

With the server running, `python tools/rehearse_demo.py` performs every step of this
script through the same client code the app uses (backup, restore with hash comparison,
a provider down, a tampered fragment, two providers down, reconnect, schedule, activity)
and prints PASS or FAIL for each claim. All 22 checks should pass. It does not replace a
run-through with the real screens, but it tells you the system itself is healthy.
It creates a throwaway account and leaves its work files in your temp folder.

## Before the audience arrives

1. Server terminal (venv active):
   ```bash
   python manage.py migrate
   python -m config.devcerts      # only if certs/ is missing
   python -m config.securerun
   ```
2. Prepare three empty folders that will act as cloud providers, for example
   `C:\demo\provider-a`, `C:\demo\provider-b`, `C:\demo\provider-c`.
3. Prepare a folder `C:\demo\documents` with 4 or 5 varied files (a text file, a PDF,
   an image, a spreadsheet).
4. Make sure `ALLOW_LOCAL_FOLDER_PROVIDERS` is on (it is when `DEBUG=True`), because
   the demo providers are folders.
5. Start the client (`python -m client.main`) and have the login window ready.

## Part 1: Account and secure connection (1 minute)

- Point at the login window: "It says the connection is encrypted. The app checks the
  server's certificate and will not talk to anything else."
- Register a new account. Say: "Accounts are our own system. Passwords are hashed with
  PBKDF2, one million rounds. Weak passwords are rejected."
- Log in.

## Part 2: Connect providers (2 minutes)

- Open **Cloud Providers**, click **Add provider**, type *Local folder*, name it
  "Cloud A", path `C:\demo\provider-a`. Repeat for B and C.
- Say: "These are folders standing in for cloud accounts, because the demo has no
  paid accounts. The real S3 and S3-compatible adapter is the same interface. It is
  tested against a local S3 server, and Backblaze, Wasabi or AWS need only a bucket
  and keys."
- Show all three as *Healthy*.

## Part 3: Encrypted, distributed backup (3 minutes)

- Open **New Backup**, choose `C:\demo\documents`, name it "Demo", start.
- While the progress bar runs, say: "Each file is encrypted here on this computer
  first. The server only ever receives ciphertext."
- Open File Explorer to `provider-a`, `-b`, `-c` side by side. Say: "The file is cut
  into two data pieces plus one parity piece, one per provider. None of these can be
  opened. Open one in Notepad, and it is unreadable, and no single folder has the
  whole file."
- Open **Backup History**: show the files, sizes, version numbers, and status.

## Part 4: Recovery and verification (2 minutes)

- Open **Recover Files**, pick a file, choose a destination, recover.
- Compare the original and the recovered file: open both, or run
  ```bash
  certutil -hashfile C:\demo\documents\<file> SHA256
  certutil -hashfile C:\demo\restored\<file> SHA256
  ```
  Say: "Identical hashes, so the bytes are identical."

## Part 5: The strong demo: a provider goes down (2 minutes)

- In File Explorer **rename** `provider-b` to `provider-b-offline`.
- In **Cloud Providers**, select `provider-b` and click **Check**. Its status turns to
  *Error*. (The list does not notice by itself: it only looks when you press Check, so
  skip this step and it will still say Connected.)
- In **Recover Files**, recover the same file again.
- Say: "One provider is gone. The missing piece is rebuilt from the other data piece
  and the parity piece. Same result." Compare the hashes again.
- Rename the folder back and press **Check** again; the status returns to *Connected*.
- Optional extra: in **Cloud Providers** select a provider, **Disconnect** it, then
  **Reconnect…** it with the same folder. The app reports that it found the stored
  backup pieces again. Reconnecting with a different, empty folder instead shows the
  "not found" warning.

## Part 6: Tampering is detected (1 minute)

- Open a fragment file in `provider-c`, change a few bytes in a hex editor or with
  ```bash
  python -c "p=r'C:\demo\provider-c\<fragment file>'; d=bytearray(open(p,'rb').read()); d[10]^=255; open(p,'wb').write(d)"
  ```
- Recover again. Say: "The hash no longer matches, so that piece is treated as
  lost and rebuilt. Corrupted data is never handed back silently."

## Part 7: Extra features, if time allows (1 minute)

- **Backup Schedules**: create a daily schedule, show *Run now*, and explain that it
  runs while the app is open and catches up when it is reopened.
- **Activity**: show the log of everything just done. Filter to warnings and point at
  the "Provider unreachable" entry from Part 5 and any failed sign-in.
- **Settings**: change password; mention that it signs out every other session and
  that login attempts are rate limited.

## Questions to expect, and short answers

| Question | Answer |
|---|---|
| Can the platform read my files? | Technically yes: the server holds the master key that wraps the data keys. Chosen so password reset does not destroy backups; a zero-knowledge design is future work. |
| Why not derive the key from my password? | Reset would lose all data, and forgetting a password would too. |
| What if two providers fail? | Recovery reports failure. XOR parity tolerates one. Reed-Solomon would tolerate more; it is the documented upgrade. |
| Why XOR parity and not replication? | No provider holds a whole file, and the storage overhead is 1/(n-1) instead of 100% or more. |
| Why not use an existing backup tool? | The project's point is the integration: local encryption, multi-provider distribution, integrity verification and recovery under failure. |
| Did you write your own encryption? | No. Fernet from the `cryptography` library and Django's password hasher. The custom part is the design around them. |
| Is it tested? | 375 automated tests, including end-to-end tests over real TLS and a local S3 server, provider outages and corrupted data. |
| What would you do next? | PostgreSQL, a background service for schedules, Reed-Solomon, key management service. |

## If something goes wrong

- **Client cannot connect:** is the server terminal still running and showing port
  8443? Are the certificates in `certs/` present?
- **Local folder not offered:** `DEBUG` must be `True` in `.env`.
- **A backup fails with fewer than two providers:** connect at least two healthy
  providers before starting.
