"""
Development certificates, so the API can be served over HTTPS locally.

    python -m config.devcerts

creates certs/dev-ca.pem (public: give it to the client), and
certs/server-cert.pem + certs/server-key.pem (the server's; keep the key
private). The client is told to trust only dev-ca.pem, so nothing has to
be installed into the operating system's trusted certificates.

The CA's private key is never written to disk: it signs the one server
certificate and is discarded, so nobody can later mint certificates the
client would trust. Regenerate everything to rotate.

Not for production: a real deployment uses a certificate from a public CA
(for example Let's Encrypt) for its real domain name.
"""

import datetime
import ipaddress
import os
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DEFAULT_DIRECTORY = Path(__file__).resolve().parent.parent / "certs"


def _name(common_name: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Secure Multi-Cloud Backup (development)"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )


def _key_usage(**enabled) -> x509.KeyUsage:
    flags = dict(
        digital_signature=False,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=False,
        crl_sign=False,
        encipher_only=False,
        decipher_only=False,
    )
    flags.update(enabled)
    return x509.KeyUsage(**flags)


def generate_dev_certs(
    directory, hostnames=("localhost",), addresses=("127.0.0.1", "::1")
) -> dict[str, Path]:
    """Write a fresh CA certificate and a server certificate/key. Returns their paths."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    started = now - datetime.timedelta(minutes=5)  # tolerate small clock differences

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = _name("Development CA")
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(started)
        .not_valid_after(now + datetime.timedelta(days=730))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_key_usage(digital_signature=True, key_cert_sign=True, crl_sign=True), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    server_key = ec.generate_private_key(ec.SECP256R1())
    names = [x509.DNSName(host) for host in hostnames]
    names += [x509.IPAddress(ipaddress.ip_address(address)) for address in addresses]
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(_name(hostnames[0]))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(started)
        .not_valid_after(now + datetime.timedelta(days=397))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_key_usage(digital_signature=True), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())  # the CA key is discarded after this
    )

    paths = {
        "ca": directory / "dev-ca.pem",
        "cert": directory / "server-cert.pem",
        "key": directory / "server-key.pem",
    }
    paths["ca"].write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    paths["cert"].write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    paths["key"].write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    os.chmod(paths["key"], 0o600)
    return paths


def main():
    paths = generate_dev_certs(DEFAULT_DIRECTORY)
    print(f"Created development certificates in {DEFAULT_DIRECTORY}\n")
    print(f"  {paths['ca'].name:<17} public: the client trusts this")
    print(f"  {paths['cert'].name:<17} the server's certificate")
    print(f"  {paths['key'].name:<17} the server's PRIVATE key: never share or commit it\n")
    print("Start the server:  python -m config.securerun")
    print("Start the client:  python -m client.main")


if __name__ == "__main__":
    sys.exit(main())
