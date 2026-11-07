from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from protection.keytools import check_key, fingerprint


class Command(BaseCommand):
    help = "Check that the master key in .env decrypts everything stored in the database."

    def handle(self, *args, **options):
        kek = settings.MASTER_KEK
        result = check_key(kek)
        self.stdout.write(f"Master key {fingerprint(kek)}: {result.summary()}")
        if result.bad:
            raise CommandError(
                f"{result.bad} stored secret(s) can NOT be decrypted with this key. "
                "Either the key in .env is not the one that was used, or the data is damaged."
            )
        if result.checked == 0:
            self.stdout.write(self.style.WARNING(
                "Nothing is stored yet, so there is nothing to check the key against."
            ))
        else:
            self.stdout.write(self.style.SUCCESS("The key works."))
