from django.test import override_settings
from django.test.runner import DiscoverRunner

FAST_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


class FastPasswordTestRunner(DiscoverRunner):
    """
    Django's default password hashing is deliberately slow (roughly half a second
    per hash), and nearly every test creates a user or logs in. During tests only,
    use a fast hasher. Nothing here touches the real settings, and a test in
    config/test_hardening.py checks they keep Django's strong default.
    """

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._fast_hashers = override_settings(PASSWORD_HASHERS=FAST_HASHERS)
        self._fast_hashers.enable()

    def teardown_test_environment(self, **kwargs):
        self._fast_hashers.disable()
        super().teardown_test_environment(**kwargs)
