import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import stat
import subprocess
import tempfile
import threading
import unittest

from opret_sikkerhed import Sikkerhedsfejl, _validate_name, add_device, init_security, rotate_device


OPENSSL_SKIP = (
    "Integrationstest springes over på Windows eller uden openssl; kør dem i Debian WSL."
    if os.name == "nt" or shutil.which("openssl") is None
    else None
)


@unittest.skipIf(OPENSSL_SKIP is not None, OPENSSL_SKIP or "")
class SikkerhedsIntegrationstest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output = self.root / "runtime"
        self.issuer = self.root / "issuer"
        init_security(self.output, self.issuer, ["127.0.0.1", "elevhub.test"])

    def tearDown(self):
        self.temp.cleanup()

    def test_certifikat_san_og_ca_verifikation(self):
        text = subprocess.run(
            ["openssl", "x509", "-in", str(self.output / "server.crt"), "-noout", "-text"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertIn("IP Address:127.0.0.1", text)
        self.assertIn("DNS:elevhub.test", text)
        verified = subprocess.run(
            ["openssl", "verify", "-CAfile", str(self.output / "ca.crt"), str(self.output / "server.crt")],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)

    def test_certifikat_accepteres_af_strict_python_tls(self):
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(self.output / "server.crt", self.output / "server.key")
        client_context = ssl.create_default_context(cafile=self.output / "ca.crt")
        self.assertEqual(client_context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(client_context.check_hostname)
        left, right = socket.socketpair()
        server = server_context.wrap_socket(left, server_side=True, do_handshake_on_connect=False)
        client = client_context.wrap_socket(right, server_hostname="elevhub.test", do_handshake_on_connect=False)
        errors = []

        def accept_handshake():
            try:
                server.do_handshake()
            except Exception as exc:  # surfaced in the assertion below
                errors.append(exc)

        thread = threading.Thread(target=accept_handshake)
        thread.start()
        try:
            client.do_handshake()
            self.assertFalse(errors)
        finally:
            thread.join(timeout=5)
            client.close()
            server.close()

    def test_credentials_hashes_uniqueness_og_format(self):
        add_device(self.output, "vaerksted-01")
        add_device(self.output, "vaerksted-02")
        first = json.loads((self.output / "enheder" / "vaerksted-01.json").read_text(encoding="utf-8"))
        second = json.loads((self.output / "enheder" / "vaerksted-02.json").read_text(encoding="utf-8"))
        self.assertNotEqual(first["token"], first["ui_token"])
        self.assertNotEqual(first["token"], second["token"])
        registry = json.loads((self.output / "enheder.json").read_text(encoding="utf-8"))
        for name, client in (("vaerksted-01", first), ("vaerksted-02", second)):
            self.assertEqual(client["computer"], name)
            self.assertEqual(registry["enheder"][name]["token_sha256"], hashlib.sha256(client["token"].encode()).hexdigest())
            self.assertEqual(registry["enheder"][name]["ui_token_sha256"], hashlib.sha256(client["ui_token"].encode()).hexdigest())

    def test_reject_overwrite_and_invalid_names(self):
        with self.assertRaises(Sikkerhedsfejl):
            init_security(self.output, self.root / "new-issuer", ["127.0.0.1"])
        add_device(self.output, "vaerksted-01")
        with self.assertRaises(Sikkerhedsfejl):
            add_device(self.output, "vaerksted-01")
        for invalid in ("../x", "Vaerksted", "to ord", "-start", "slut-", "a_b"):
            with self.assertRaises(Sikkerhedsfejl):
                _validate_name(invalid)
        _validate_name("a" * 32)
        with self.assertRaises(Sikkerhedsfejl):
            _validate_name("a" * 33)

    def test_secret_permissions_and_rotation(self):
        add_device(self.output, "vaerksted-01")
        client_path = self.output / "enheder" / "vaerksted-01.json"
        before = client_path.read_bytes()
        ca_hash = (self.issuer / "ca.key").read_bytes()
        server_hash = (self.output / "server.crt").read_bytes()
        self.assertEqual(stat.S_IMODE(client_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.output / "server.key").stat().st_mode), 0o600)
        with self.assertRaises(Sikkerhedsfejl):
            rotate_device(self.output, "vaerksted-01")
        rotate_device(self.output, "vaerksted-01", confirm=True)
        self.assertNotEqual(before, client_path.read_bytes())
        self.assertEqual(ca_hash, (self.issuer / "ca.key").read_bytes())
        self.assertEqual(server_hash, (self.output / "server.crt").read_bytes())


class SikkerhedsValideringstest(unittest.TestCase):
    def test_san_og_platform_input_afvises(self):
        # This test does not invoke openssl and therefore also runs on Windows.
        from opret_sikkerhed import _validate_sans

        with self.assertRaises(Sikkerhedsfejl):
            _validate_sans(["*"])
        with self.assertRaises(Sikkerhedsfejl):
            _validate_sans(["10.0.0.1", "10.0.0.1"])


if __name__ == "__main__":
    unittest.main()
