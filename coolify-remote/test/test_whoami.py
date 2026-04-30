"""Unit tests for coolify-remote whoami. Stdlib unittest only."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch


def _load_module():
    here = Path(__file__).resolve()
    bin_path = here.parent.parent / "bin" / "coolify-remote"
    loader = SourceFileLoader("coolify_remote", str(bin_path))
    spec = importlib.util.spec_from_loader("coolify_remote", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


cr = _load_module()


def _clear_env():
    for k in ("COOLIFY_URL", "COOLIFY_API_KEY"):
        os.environ.pop(k, None)


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = json.dumps(payload).encode("utf-8")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def read(self):
        return self._payload


class TestWhoamiHappyPath(unittest.TestCase):
    def setUp(self):
        _clear_env()
        os.environ["COOLIFY_URL"] = "https://coolify.example.com"
        os.environ["COOLIFY_API_KEY"] = "test-key"

    def tearDown(self):
        _clear_env()

    def test_returns_identity_dict(self):
        servers = [
            {"name": "vps-prod", "is_reachable": True, "is_usable": True, "uuid": "u1"},
            {"name": "vps-dev", "is_reachable": False, "uuid": "u2"},
        ]
        with patch("urllib.request.urlopen", return_value=_FakeResp(servers)):
            out = cr.whoami()
        self.assertTrue(out["ok"])
        self.assertEqual(out["base_url"], "https://coolify.example.com")
        self.assertEqual(out["default_server"], "vps-prod")
        self.assertEqual(out["server_count"], 2)


class TestWhoamiUnconfigured(unittest.TestCase):
    def setUp(self):
        _clear_env()

    def tearDown(self):
        _clear_env()

    def test_no_env_returns_unconfigured(self):
        out = cr.whoami()
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "unconfigured")
        self.assertEqual(out["error"], "unconfigured")
        self.assertIn("COOLIFY_URL", out["hint"])
        self.assertEqual(out["configured_keys"], [])


class TestWhoamiEnvelope(unittest.TestCase):
    def setUp(self):
        _clear_env()

    def tearDown(self):
        _clear_env()

    def test_main_envelope_shape_unconfigured(self):
        # End-to-end: invoke main(['whoami']) and capture stdout. rc=0 on
        # soft-unconfigured to match the framework's _run_refresh_handler
        # contract — rc!=0 is reserved for genuine errors. Envelope still
        # carries `status: "unconfigured"` and `ok: false`.
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            # Patch sys.argv to feed the parser without --env-file noise.
            with patch("sys.argv", ["coolify-remote", "whoami", "--json"]):
                rc = cr.main()
        # main() returns None on the soft path (no SystemExit); some wrappers
        # historically returned 0 explicitly — accept either.
        self.assertIn(rc, (None, 0))
        out = json.loads(buf.getvalue())
        self.assertEqual(out["tool"]["name"], "coolify-remote")
        self.assertIn("version", out["tool"])
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "unconfigured")


if __name__ == "__main__":
    unittest.main()
