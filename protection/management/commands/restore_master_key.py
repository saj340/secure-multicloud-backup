from getpass import getpass
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from protection.keytools import KeyBackupError, check_key, fingerprint, read_backup


class Command(BaseCommand):
    help = (
        "Recover the master key from a backup file, prove it decrypts the stored data, and "
        "show the line to put in .env. Changes nothing itself."
    )

    def add_arguments(self, parser):
        parser.add_argument("backup", help="The backup file made by backup_master_key.")

    def handle(self, *args, **options):
        path = Path(options["backup"]).expanduser()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise CommandError(f"Could not read {path}: {exc}") from exc

        try:
            kek = read_backup(text, getpass("Passphrase of the backup: "))
            check = check_key(kek)
        except KeyBackupError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f"Recovered master key {fingerprint(kek)}: {check.summary()}")
        if check.bad:
            raise CommandError(
                f"This key can NOT decrypt {check.bad} stored secret(s), so it is not the key "
                "for this database. It was not accepted."
            )
        if check.checked == 0:
            self.stdout.write(self.style.WARNING(
                "Nothing is stored yet, so the key could not be tested against any data."
            ))
        else:
            self.stdout.write(self.style.SUCCESS("The recovered key decrypts everything stored."))

        if kek == settings.MASTER_KEK:
            self.stdout.write("It is the same as the key currently in .env; nothing to change.")
        else:
            self.stdout.write("It is different from the key currently in .env. To use it, put this line in .env:")
            self.stdout.write(f"MASTER_KEK={kek}")
