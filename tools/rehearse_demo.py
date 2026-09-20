"""
Runs docs/DEMO_SCRIPT.md against a running server, using the real client code, and
checks every claim the demo makes: encrypted fragments, byte-identical restores, one
provider down, a tampered fragment, two providers down, reconnect, schedules, activity.

    python -m config.securerun                 (in another terminal)
    python tools/rehearse_demo.py [work-folder]

It registers a throwaway account and leaves it, and the work folder, behind.
"""
import hashlib
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

PROJECT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, PROJECT)
os.chdir(PROJECT)

from client.api_client import ApiClient, ApiError
from client.backup_service import load_backups, restore_version, run_backup
from client.scheduler import run_schedule

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "smcb-rehearsal"
EMAIL, PASSWORD = f"rehearsal-{int(time.time())}@example.com", "Rehearsal-Correct-Horse-88"
MARKER = b"CONFIDENTIAL-MARKER-4471"
results = []


def check(label, ok, detail=""):
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def timed(label, fn):
    start = time.monotonic()
    value = fn()
    print(f"      {label}: {time.monotonic() - start:.1f}s")
    return value


shutil.rmtree(ROOT, ignore_errors=True)
docs, restored = ROOT / "documents", ROOT / "restored"
(docs / "sub").mkdir(parents=True)
restored.mkdir()
providers = {name: ROOT / name for name in ("provider-a", "provider-b", "provider-c")}
for folder in providers.values():
    folder.mkdir()

(docs / "notes.txt").write_bytes(MARKER + b" meeting notes. " * 500)
(docs / "budget.csv").write_bytes(MARKER + b"\nmonth,amount\n" + b"jan,100\n" * 2000)
(docs / "photo.jpg").write_bytes(os.urandom(300_000))
(docs / "big-report.pdf").write_bytes(MARKER + os.urandom(6_000_000))
(docs / "sub" / "résumé-تقرير.txt").write_bytes(MARKER + "unicode name test".encode() * 100)
originals = {p.name: p for p in docs.rglob("*") if p.is_file()}

api = ApiClient()
print("== Part 1: account and secure connection")
print("  transport:", api.base_url)
api.register(EMAIL, "Rehearsal User", PASSWORD, PASSWORD)
user = api.login(EMAIL, PASSWORD)
check("registered and logged in over verified TLS", user["email"] == EMAIL)

print("== Part 2: connect providers")
for name, folder in providers.items():
    api.connect_provider("local_folder", name, {"path": str(folder)})
listed = api.list_providers()
check("three providers connected", [p["status"] for p in listed] == ["connected"] * 3)

print("== Part 3: encrypted, distributed backup")
result = timed("backup of 5 files (~6.4 MB)", lambda: run_backup(api, "Demo", [str(docs)]))
check("backup completed with every file", result.status == "completed" and len(result.succeeded) == 5, f"{result.status}, ok={len(result.succeeded)}, failed={result.failed}")
stored = [p for f in providers.values() for p in f.rglob("*") if p.is_file()]
check("fragments exist in all three providers", all(any(f.rglob("*.bin")) for f in providers.values()), f"{len(stored)} objects")
leaked = [p for p in stored if MARKER in p.read_bytes()]
check("no stored object contains readable plaintext", not leaked)
backups = load_backups(api)
versions = {v["original_filename"]: v["id"] for j in backups for f in j["files"] for v in f["versions"]}
check("history lists all five files", len(versions) == 5, ", ".join(sorted(versions)))


def restore_all(tag):
    bad = []
    for name, version_id in versions.items():
        dest = restored / tag / name
        try:
            restore_version(api, version_id, dest)
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{name}: {exc}")
            continue
        if sha(dest) != sha(originals[name]):
            bad.append(f"{name}: hash differs")
    return bad


print("== Part 4: recovery and verification")
bad = timed("restore 5 files", lambda: restore_all("normal"))
check("all restored files are byte-identical", not bad, "; ".join(bad))

print("== Part 5: a provider goes down")
providers["provider-b"].rename(ROOT / "provider-b-offline")
statuses = [p["status"] for p in api.list_providers()]
print("      before pressing Check, the list still says:", statuses)
api.check_provider(next(p["id"] for p in api.list_providers() if p["display_name"] == "provider-b"))
statuses = {p["display_name"]: p["status"] for p in api.list_providers()}
check("Check notices the outage", statuses["provider-b"] == "error", str(statuses))
bad = restore_all("outage")
check("every file still restores with one provider down", not bad, "; ".join(bad))
(ROOT / "provider-b-offline").rename(providers["provider-b"])
api.check_provider(next(p["id"] for p in api.list_providers() if p["display_name"] == "provider-b"))
check("provider recovers after rename back", {p["display_name"]: p["status"] for p in api.list_providers()}["provider-b"] == "connected")

print("== Part 6: tampering is detected")
victim = max((p for p in providers["provider-c"].rglob("*.bin")), key=lambda p: p.stat().st_size)
data = bytearray(victim.read_bytes())
data[10] ^= 0xFF
victim.write_bytes(bytes(data))
bad = restore_all("tamper")
check("restore still returns correct data after corruption", not bad, "; ".join(bad))
print("      corrupted:", victim.name)

print("== Two providers down is refused cleanly")
providers["provider-a"].rename(ROOT / "a-off")
providers["provider-b"].rename(ROOT / "b-off")
try:
    restore_version(api, versions["notes.txt"], restored / "twodown" / "notes.txt")
    check("recovery with two providers down fails", False, "it succeeded?!")
except ApiError as exc:
    check("recovery with two providers down fails with a message", True, exc.message)
check("no partial file left behind", not (restored / "twodown" / "notes.txt").exists() and not list((restored / "twodown").glob("*.part")) if (restored / "twodown").exists() else True)
(ROOT / "a-off").rename(providers["provider-a"])
(ROOT / "b-off").rename(providers["provider-b"])

print("== Reconnect")
pid = next(p["id"] for p in api.list_providers() if p["display_name"] == "provider-a")
api.disconnect_provider(pid)
same = api.reconnect_provider(pid, {"path": str(providers["provider-a"])})
check("reconnect finds the stored pieces", same["fragments_missing"] == 0 and same["fragments_checked"] > 0, f"{same['fragments_checked']} checked")

print("== Part 7: schedules and activity")
schedule = api.create_schedule("Nightly docs", str(docs), "daily", "02:00")
outcome = run_schedule(api, schedule)
check("Run now on a schedule completes", outcome.status == "completed", outcome.status)
events = [(e["level"], e["action"]) for e in api.list_activity(100)]
print("      activity (newest first):", [a for _l, a in events][:14])
for expected in ("login", "registered", "provider_connected", "provider_unreachable", "provider_recovered", "provider_reconnected", "backup_run_started"):
    check(f"activity contains {expected}", any(a == expected for _l, a in events))

print()
print(f"{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
