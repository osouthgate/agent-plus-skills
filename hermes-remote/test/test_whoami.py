"""Unit tests for hermes-remote whoami. Stdlib unittest only.

whoami is env-derived (no admin login, no HTTP calls), so tests don't need
to mock the transport.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch


def _load_module():
    here = Path(__file__).resolve()
    bin_path = here.parent.parent / "bin" / "hermes-remote"
    loader = SourceFileLoader("hermes_remote", str(bin_path))
    spec = importlib.util.spec_from_loader("hermes_remote", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


hr = _load_module()


_HERMES_KEYS = (
    "HERMES_URL", "HERMES_VPS_IP", "HERMES_HOST",
    "HERMES_ADMIN_USER", "HERMES_PASSWORD", "HERMES_PASSWORD_CMD",
    "COOLIFY_URL", "COOLIFY_API_KEY", "HERMES_APP_UUID",
)


def _clear_env():
    for k in _HERMES_KEYS:
        os.environ.pop(k, None)


class TestWhoamiHappyPath(unittest.TestCase):
    def setUp(self):
        _clear_env()

    def tearDown(self):
        _clear_env()

    def test_url_form_with_password(self):
        os.environ["HERMES_URL"] = "https://hermes.example.com"
        os.environ["HERMES_PASSWORD"] = "secret"
        os.environ["HERMES_ADMIN_USER"] = "ops@example.com"
        out = hr.whoami()
        self.assertTrue(out["ok"])
        self.assertEqual(out["base_url"], "https://hermes.example.com")
        self.assertEqual(out["env_user"], "ops@example.com")
        self.assertTrue(out["configured"])

    def test_vps_ip_host_form(self):
        os.environ["HERMES_VPS_IP"] = "1.2.3.4"
        os.environ["HERMES_HOST"] = "hermes.example.com"
        out = hr.whoami()
        self.assertTrue(out["ok"])
        self.assertIn("1.2.3.4", out["base_url"])
        self.assertIn("hermes.example.com", out["base_url"])
        # No password source set.
        self.assertFalse(out["configured"])

    def test_no_secret_leakage(self):
        os.environ["HERMES_URL"] = "https://hermes.example.com"
        os.environ["HERMES_PASSWORD"] = "supersecretpassword12345"
        out = hr.whoami()
        blob = json.dumps(out)
        self.assertNotIn("supersecretpassword12345", blob)


class TestWhoamiUnconfigured(unittest.TestCase):
    def setUp(self):
        _clear_env()

    def tearDown(self):
        _clear_env()

    def test_no_env_returns_unconfigured(self):
        out = hr.whoami()
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "unconfigured")
        self.assertEqual(out["error"], "unconfigured")
        self.assertIn("HERMES_URL", out["hint"])


class TestWhoamiEnvelope(unittest.TestCase):
    def setUp(self):
        _clear_env()

    def tearDown(self):
        _clear_env()

    def test_main_envelope_shape_unconfigured(self):
        # rc=0 on soft-unconfigured per framework contract.
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            with patch("sys.argv", ["hermes-remote", "whoami", "--json"]):
                rc = hr.main()
        self.assertIn(rc, (None, 0))
        out = json.loads(buf.getvalue())
        self.assertEqual(out["tool"]["name"], "hermes-remote")
        self.assertIn("version", out["tool"])
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "unconfigured")


if __name__ == "__main__":
    unittest.main()
