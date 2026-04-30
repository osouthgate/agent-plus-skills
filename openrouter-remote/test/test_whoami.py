"""Unit tests for openrouter-remote whoami. Stdlib unittest only."""

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
    bin_path = here.parent.parent / "bin" / "openrouter-remote"
    loader = SourceFileLoader("openrouter_remote", str(bin_path))
    spec = importlib.util.spec_from_loader("openrouter_remote", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


orr = _load_module()


def _clear_env():
    for k in ("OPENROUTER_API_KEY", "OPENROUTER_PROVISIONING_KEY"):
        os.environ.pop(k, None)


class TestWhoamiHappyPath(unittest.TestCase):
    def setUp(self):
        _clear_env()
        os.environ["OPENROUTER_API_KEY"] = "sk-or-fake-test-key-supersecretvalue"

    def tearDown(self):
        _clear_env()

    def test_returns_identity_dict(self):
        # /key returns {data: {label, ...}}
        with patch.object(orr, "_http", return_value=(200, {"data": {"label": "hermes-prod"}})):
            out = orr.whoami()
        self.assertTrue(out["ok"])
        self.assertEqual(out["key_label"], "hermes-prod")
        self.assertTrue(out["key_hash_prefix"])
        # Hash prefix is hex, 16 chars.
        self.assertEqual(len(out["key_hash_prefix"]), 16)

    def test_no_secret_leakage(self):
        secret = "sk-or-fake-test-key-supersecretvalue"
        os.environ["OPENROUTER_API_KEY"] = secret
        with patch.object(orr, "_http", return_value=(200, {"data": {"label": "x"}})):
            out = orr.whoami()
        blob = json.dumps(out)
        self.assertNotIn(secret, blob)
        self.assertNotIn("supersecretvalue", blob)


class TestWhoamiUnconfigured(unittest.TestCase):
    def setUp(self):
        _clear_env()

    def tearDown(self):
        _clear_env()

    def test_no_env_returns_unconfigured(self):
        out = orr.whoami()
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "unconfigured")
        self.assertIn("OPENROUTER_API_KEY", out["hint"])


class TestWhoamiEnvelope(unittest.TestCase):
    def setUp(self):
        _clear_env()

    def tearDown(self):
        _clear_env()

    def test_main_envelope_shape_unconfigured(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            with self.assertRaises(SystemExit) as cm:
                with patch("sys.argv", ["openrouter-remote", "whoami", "--json"]):
                    orr.main()
        self.assertEqual(cm.exception.code, 1)
        out = json.loads(buf.getvalue())
        self.assertEqual(out["tool"]["name"], "openrouter-remote")
        self.assertIn("version", out["tool"])
        self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()
