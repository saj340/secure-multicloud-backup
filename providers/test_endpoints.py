import socket
from unittest import mock

from django.test import SimpleTestCase, override_settings

from .adapters.base import ProviderConfigurationError
from .endpoints import validate_endpoint_url

GETADDRINFO = "providers.endpoints.socket.getaddrinfo"


def resolves_to(*addresses):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses]


@override_settings(ALLOW_PRIVATE_S3_ENDPOINTS=False)
class StrictEndpointTests(SimpleTestCase):
    def test_public_https_endpoints_are_accepted(self):
        validate_endpoint_url("https://93.184.216.34")
        validate_endpoint_url("https://93.184.216.34:9000")
        with mock.patch(GETADDRINFO, return_value=resolves_to("93.184.216.34")):
            validate_endpoint_url("https://s3.us-west-004.backblazeb2.com")

    def test_malformed_and_non_http_urls_are_rejected(self):
        for url in ("not a url", "https://", "ftp://93.184.216.34", "s3://bucket", ""):
            with self.subTest(url=url):
                with self.assertRaises(ProviderConfigurationError):
                    validate_endpoint_url(url)

    def test_plain_http_is_rejected(self):
        with self.assertRaisesMessage(ProviderConfigurationError, "https"):
            validate_endpoint_url("http://93.184.216.34")

    def test_private_loopback_and_metadata_addresses_are_rejected(self):
        for url in (
            "https://127.0.0.1",
            "https://localhost",
            "https://[::1]",
            "https://10.0.0.5",
            "https://192.168.1.10",
            "https://172.16.0.1",
            "https://169.254.169.254",
        ):
            with self.subTest(url=url):
                with self.assertRaisesMessage(ProviderConfigurationError, "public"):
                    validate_endpoint_url(url)

    def test_a_hostname_that_resolves_to_a_private_address_is_rejected(self):
        with mock.patch(GETADDRINFO, return_value=resolves_to("10.1.2.3")):
            with self.assertRaisesMessage(ProviderConfigurationError, "public"):
                validate_endpoint_url("https://looks-innocent.example.com")

    def test_one_private_address_among_public_ones_is_enough_to_reject(self):
        with mock.patch(GETADDRINFO, return_value=resolves_to("93.184.216.34", "127.0.0.1")):
            with self.assertRaises(ProviderConfigurationError):
                validate_endpoint_url("https://mixed.example.com")

    def test_an_unresolvable_hostname_is_rejected(self):
        with mock.patch(GETADDRINFO, side_effect=socket.gaierror("no such host")):
            with self.assertRaisesMessage(ProviderConfigurationError, "could not be resolved"):
                validate_endpoint_url("https://does-not-exist.example.com")


@override_settings(ALLOW_PRIVATE_S3_ENDPOINTS=True)
class DevelopmentEndpointTests(SimpleTestCase):
    def test_local_http_endpoints_are_allowed_in_development(self):
        validate_endpoint_url("http://127.0.0.1:9000")
        validate_endpoint_url("http://localhost:9000")

    def test_malformed_urls_are_still_rejected(self):
        for url in ("not a url", "ftp://127.0.0.1", "http://"):
            with self.subTest(url=url):
                with self.assertRaises(ProviderConfigurationError):
                    validate_endpoint_url(url)
