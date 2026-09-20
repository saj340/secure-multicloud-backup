import http.server
import json
import os
import ssl
import tempfile
import threading
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from config.devcerts import generate_dev_certs

from . import config
from .api_client import ApiClient, ApiError


class RecordingHandler(http.server.BaseHTTPRequestHandler):
    """A stand-in server that remembers every request body it receives."""

    bodies = []

    def _reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply(200, {"id": 1, "email": "tls@example.com"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        type(self).bodies.append(self.rfile.read(length))
        self._reply(401, {"detail": "Invalid email or password."})

    def log_message(self, *args):
        pass


class HttpsServer:
    def __init__(self, cert, key):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert), str(key))
        self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(5)


def scrub_client_trust_settings(test):
    """Make the test independent of the developer's environment and certs/ folder."""
    environment = mock.patch.dict(os.environ)
    environment.start()
    test.addCleanup(environment.stop)
    os.environ.pop("BACKUP_CA_CERT", None)
    os.environ.pop("BACKUP_API_URL", None)


class ClientCertificateTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.trusted = generate_dev_certs(Path(cls._tmp.name) / "trusted")
        cls.stranger = generate_dev_certs(Path(cls._tmp.name) / "stranger")

        cls.server = HttpsServer(cls.trusted["cert"], cls.trusted["key"])
        cls.server.start()
        cls.addClassCleanup(cls.server.stop)
        cls.url = f"https://127.0.0.1:{cls.server.port}/api"

    def setUp(self):
        scrub_client_trust_settings(self)
        # No development CA to fall back on: the client must use the system's trusted CAs.
        patcher = mock.patch.object(config, "DEV_CA", Path(self._tmp.name) / "missing.pem")
        patcher.start()
        self.addCleanup(patcher.stop)
        RecordingHandler.bodies.clear()

    def test_a_certificate_from_the_trusted_ca_is_accepted(self):
        api = ApiClient(self.url, ca_cert=str(self.trusted["ca"]))
        self.assertEqual(api.me()["email"], "tls@example.com")

    def test_a_server_whose_ca_the_client_does_not_know_is_refused(self):
        with self.assertRaises(ApiError) as caught:
            ApiClient(self.url).me()  # relies on the system's CAs, which don't include ours
        self.assertIn("certificate", caught.exception.message)

    def test_a_server_signed_by_a_different_ca_is_refused(self):
        api = ApiClient(self.url, ca_cert=str(self.stranger["ca"]))
        with self.assertRaises(ApiError) as caught:
            api.me()
        self.assertIn("certificate", caught.exception.message)

    def test_the_password_is_only_sent_once_the_certificate_has_verified(self):
        secret = "hunter2-very-secret"

        trusting = ApiClient(self.url, ca_cert=str(self.trusted["ca"]))
        with self.assertRaises(ApiError):
            trusting.login("a@example.com", secret)  # the stand-in rejects the login
        self.assertEqual(len(RecordingHandler.bodies), 1)
        self.assertIn(secret.encode(), RecordingHandler.bodies[0])

        RecordingHandler.bodies.clear()
        for untrusting in (ApiClient(self.url), ApiClient(self.url, ca_cert=str(self.stranger["ca"]))):
            with self.assertRaises(ApiError) as caught:
                untrusting.login("a@example.com", secret)
            self.assertIn("certificate", caught.exception.message)
        self.assertEqual(RecordingHandler.bodies, [])  # the server never saw it


class TransportRuleTests(SimpleTestCase):
    def test_https_is_always_allowed(self):
        for url in ("https://backup.example.com/api", "https://127.0.0.1:8443/api"):
            with self.subTest(url=url):
                config.require_safe_transport(url)

    def test_plain_http_is_allowed_only_for_the_local_machine(self):
        for url in (
            "http://127.0.0.1:8000/api",
            "http://localhost:8000/api",
            "http://[::1]:8000/api",
            "http://127.0.0.5/api",
        ):
            with self.subTest(url=url):
                config.require_safe_transport(url)

    def test_plain_http_anywhere_else_is_refused(self):
        for url in (
            "http://backup.example.com/api",
            "http://192.168.1.5:8000/api",
            "http://10.0.0.1/api",
            "http://8.8.8.8/api",
        ):
            with self.subTest(url=url):
                with self.assertRaisesMessage(ValueError, "https"):
                    config.require_safe_transport(url)

    def test_other_schemes_and_malformed_addresses_are_refused(self):
        for url in ("ftp://127.0.0.1/api", "file:///etc/passwd", "127.0.0.1:8000/api", ""):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    config.require_safe_transport(url)

    def test_the_client_itself_refuses_an_unsafe_address(self):
        with self.assertRaises(ValueError):
            ApiClient("http://backup.example.com/api")


class TrustedCaSelectionTests(SimpleTestCase):
    def setUp(self):
        scrub_client_trust_settings(self)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dev_ca = Path(self._tmp.name) / "dev-ca.pem"
        self.dev_ca.write_text("placeholder")
        patcher = mock.patch.object(config, "DEV_CA", self.dev_ca)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_default_address_is_https_on_the_local_machine(self):
        self.assertEqual(config.api_url(), "https://127.0.0.1:8443/api")
        os.environ["BACKUP_API_URL"] = "https://backup.example.com/api"
        self.assertEqual(config.api_url(), "https://backup.example.com/api")

    def test_the_development_ca_is_trusted_for_https_on_this_machine(self):
        self.assertEqual(config.ca_cert_for("https://127.0.0.1:8443/api"), str(self.dev_ca))
        self.assertEqual(ApiClient("https://localhost:8443/api")._verify, str(self.dev_ca))

    def test_the_development_ca_is_never_trusted_for_a_real_server_elsewhere(self):
        self.assertIsNone(config.ca_cert_for("https://backup.example.com/api"))
        self.assertIs(ApiClient("https://backup.example.com/api")._verify, True)

    def test_it_is_not_used_for_plain_http_or_when_the_file_is_missing(self):
        self.assertIsNone(config.ca_cert_for("http://127.0.0.1:8000/api"))
        self.dev_ca.unlink()
        self.assertIsNone(config.ca_cert_for("https://127.0.0.1:8443/api"))

    def test_an_explicitly_configured_ca_always_wins(self):
        os.environ["BACKUP_CA_CERT"] = "/somewhere/company-ca.pem"
        for url in ("https://backup.example.com/api", "https://127.0.0.1:8443/api"):
            with self.subTest(url=url):
                self.assertEqual(config.ca_cert_for(url), "/somewhere/company-ca.pem")
