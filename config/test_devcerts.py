import datetime
import ipaddress
import ssl
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID
from django.test import SimpleTestCase

from .devcerts import generate_dev_certs


class DevCertTests(SimpleTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name)
        self.paths = generate_dev_certs(self.directory)

    def load(self, name):
        return x509.load_pem_x509_certificate(self.paths[name].read_bytes())

    def test_exactly_three_files_are_written_and_only_the_server_key_is_private(self):
        self.assertEqual(
            sorted(p.name for p in self.directory.iterdir()),
            ["dev-ca.pem", "server-cert.pem", "server-key.pem"],
        )
        for name, path in self.paths.items():
            with self.subTest(file=path.name):
                holds_a_key = b"PRIVATE KEY" in path.read_bytes()
                self.assertEqual(holds_a_key, name == "key")

    def test_the_ca_is_a_ca_and_the_server_certificate_is_not(self):
        ca_constraints = self.load("ca").extensions.get_extension_for_class(x509.BasicConstraints)
        server_constraints = self.load("cert").extensions.get_extension_for_class(x509.BasicConstraints)
        self.assertTrue(ca_constraints.value.ca)
        self.assertFalse(server_constraints.value.ca)

    def test_the_server_certificate_is_signed_by_the_ca(self):
        self.load("cert").verify_directly_issued_by(self.load("ca"))

    def test_the_server_certificate_names_localhost_and_the_loopback_addresses(self):
        alt_names = self.load("cert").extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        self.assertEqual(alt_names.get_values_for_type(x509.DNSName), ["localhost"])
        self.assertEqual(
            set(alt_names.get_values_for_type(x509.IPAddress)),
            {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")},
        )

    def test_the_server_certificate_is_only_good_for_serving_tls(self):
        usage = self.load("cert").extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        self.assertEqual(list(usage), [ExtendedKeyUsageOID.SERVER_AUTH])

    def test_validity_periods_are_bounded_and_already_started(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        server = self.load("cert")
        self.assertLessEqual(server.not_valid_before_utc, now)
        self.assertLessEqual(server.not_valid_after_utc - server.not_valid_before_utc, datetime.timedelta(days=400))
        self.assertGreater(server.not_valid_after_utc, now)

    def test_the_certificate_and_key_belong_together(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.paths["cert"]), str(self.paths["key"]))  # raises on a mismatch

    def test_each_run_creates_a_new_ca(self):
        other = generate_dev_certs(self.directory / "second")
        self.assertNotEqual(self.paths["ca"].read_bytes(), other["ca"].read_bytes())
