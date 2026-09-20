import os
import subprocess
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from django.core.management.utils import get_random_secret_key
from django.test import SimpleTestCase

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class DeploymentChecklistTests(SimpleTestCase):
    """Runs Django's own `check --deploy` against the production configuration."""

    def run_check(self, **overrides):
        environment = {
            **os.environ,
            "DEBUG": "False",
            "SECRET_KEY": get_random_secret_key(),
            "MASTER_KEK": Fernet.generate_key().decode(),
            "ALLOWED_HOSTS": "backup.example.com",
            **overrides,
        }
        return subprocess.run(
            [sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )

    def test_the_production_configuration_passes_the_checklist_with_no_warnings(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_checklist_does_complain_about_debug_mode(self):
        # Guards the test above: proves the check can fail, so a pass means something.
        result = self.run_check(DEBUG="True")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DEBUG", result.stdout + result.stderr)
