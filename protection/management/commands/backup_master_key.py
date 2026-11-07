from getpass import getpass
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from protection.keytools import KeyBackupError, check_key, fingerprint, make_backup, read_backup


class Command(BaseCommand):
    help = (
        "Save the master key to a file encrypted with a passphrase. Keep that file (and "
        "the passphrase) somewhere other than this computer."
    )

    def add_arguments(self, parser):
        parser.add_argument("output", help="Where to write the backup file (must not exist yet).")

    def handle(self, *args, **options):
        out = Path(options["output"]).expanduser().resolve()
        if out.exists():
            raise CommandError(f"{out} already exists; choose a new file name.")
        if out.is_relative_to(Path(settings.BASE_DIR).resolve()):
            raise CommandError(
                "Don't keep the backup inside the project folder: it is a git repository "
                "and the file could end up published. Choose a location outside it."
            )
        if not out.parent.is_dir():
            raise CommandError(f"The folder {out.parent} does not exist.")

        kek = settings.MASTER_KEK
        check = check_key(kek)
        if check.bad:
            self.stderr.write(self.style.WARNING(
                f"Warning: this key does not decrypt {check.bad} stored secret(s). "
                "It may not be the key that was used."
            ))

        passphrase = getpass("Passphrase for the backup: ")
        if getpass("Type it again: ") != passphrase:
            raise CommandError("The two passphrases are different; nothing was written.")
        try:
            text = make_backup(kek, passphrase)
            with open(out, "x", encoding="utf-8") as handle:
                handle.write(text)
            # A backup that can't be read back is worse than none: prove this one opens.
            if read_backup(out.read_text(encoding="utf-8"), passphrase) != kek:
                raise KeyBackupError("The saved file did not give back the same key.")
        except KeyBackupError as exc:
            out.unlink(missing_ok=True)
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"Backup written to {out} and read back successfully."))
        self.stdout.write(f"Key fingerprint: {fingerprint(kek)}")
        self.stdout.write(
            "Store the file and the passphrase in different places, away from this computer. "
            "Without both, the backup cannot be opened."
        )
