"""
The real Django app served by uvicorn over TLS, used by the real client:
nothing here is stubbed.
"""

import socket
import ssl
import tempfile
import threading
import time
from pathlib import Path

import requests
import uvicorn
from django.test import TransactionTestCase, override_settings

from config.devcerts import generate_dev_certs

from .api_client import ApiClient, ApiError
from .backup_service import restore_version, run_backup

EMAIL, PASSWORD = "tls@example.com", "CorrectHorseBatteryStaple9"


@override_settings(ALLOW_LOCAL_FOLDER_PROVIDERS=True)
class TlsEndToEndTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.certs = generate_dev_certs(Path(cls._tmp.name) / "certs")
        cls.stranger = generate_dev_certs(Path(cls._tmp.name) / "stranger")

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        cls.server = uvicorn.Server(
            uvicorn.Config(
                "config.asgi:application",
                host="127.0.0.1",
                port=cls.port,
                ssl_certfile=str(cls.certs["cert"]),
                ssl_keyfile=str(cls.certs["key"]),
                log_level="error",
            )
        )
        thread = threading.Thread(target=cls.server.run, daemon=True)
        thread.start()
        cls.addClassCleanup(cls._stop_server, thread)

        deadline = time.monotonic() + 20
        while not cls.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("The HTTPS test server did not start.")
            time.sleep(0.05)

    @classmethod
    def _stop_server(cls, thread):
        cls.server.should_exit = True
        thread.join(10)

    def api(self, ca="certs"):
        authority = self.certs if ca == "certs" else self.stranger
        return ApiClient(f"https://127.0.0.1:{self.port}/api", ca_cert=str(authority["ca"]))

    def test_backup_and_restore_work_end_to_end_over_https(self):
        api = self.api()
        api.register(EMAIL, "Tls User", PASSWORD, PASSWORD)
        api.login(EMAIL, PASSWORD)

        tmp = Path(self._tmp.name)
        for index in range(2):
            folder = tmp / f"provider{index}"
            folder.mkdir()
            api.connect_provider("local_folder", f"provider{index}", {"path": str(folder)})

        document = tmp / "report.txt"
        document.write_text("Confidential quarterly report. " * 200)
        result = run_backup(api, "docs", [document])
        self.assertEqual(result.status, "completed")

        version = api.get_job(result.job_id)["files"][0]["versions"][0]
        restored = restore_version(api, version["id"], tmp / "restored.txt")
        self.assertEqual(restored.read_bytes(), document.read_bytes())

    def test_the_server_refuses_plain_http_on_its_tls_port(self):
        with self.assertRaises(requests.exceptions.RequestException):
            requests.get(f"http://127.0.0.1:{self.port}/api/accounts/me/", timeout=5)

    def test_the_connection_negotiates_modern_tls(self):
        context = ssl.create_default_context(cafile=str(self.certs["ca"]))
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname="localhost") as tls:
                self.assertIn(tls.version(), ("TLSv1.2", "TLSv1.3"))

    def test_a_client_that_trusts_a_different_ca_is_refused(self):
        with self.assertRaises(ApiError) as caught:
            self.api(ca="stranger").login(EMAIL, PASSWORD)
        self.assertIn("certificate", caught.exception.message)
