"""
Safety checks for endpoint URLs that a user supplies for S3-compatible
providers.

The server makes requests to whatever endpoint it is given, so an
unchecked URL would let a user aim it at services only the server can
reach (localhost, the internal network, a cloud metadata address). That
is server-side request forgery, so outside development an endpoint must
be https and resolve only to public internet addresses.
"""

import ipaddress
import socket
from urllib.parse import urlparse

from django.conf import settings

from .adapters.base import ProviderConfigurationError


def validate_endpoint_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ProviderConfigurationError(
            "The endpoint must be a full URL such as https://s3.example.com."
        )
    if settings.ALLOW_PRIVATE_S3_ENDPOINTS:
        return
    if parsed.scheme != "https":
        raise ProviderConfigurationError("The endpoint must use https.")

    try:
        addresses = {
            info[4][0] for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443)
        }
    except socket.gaierror as exc:
        raise ProviderConfigurationError(
            f"The endpoint host could not be resolved: {parsed.hostname}"
        ) from exc

    # Every address must be public: a host that also resolves to a private
    # address is refused, since the request could end up there.
    for address in addresses:
        if not ipaddress.ip_address(address.split("%")[0]).is_global:
            raise ProviderConfigurationError("The endpoint must be a public internet address.")
