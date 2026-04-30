"""Unit tests for hcloud-remote whoami. Stdlib unittest only."""

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
    bin_path = here.parent.parent / "bin" / "hcloud-remote"
    loader = SourceFileLoader("hcloud_remote", str(bin_path))
    spec = importlib.util.spec_from_loader("hcloud_remote", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


hr = _load_module()


def _clear_env():
    for k in ("HCLOUD_TOKEN",):
        os.environ.pop(k, None)


class _FakeResp:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def read(self):
        return self._payload


class TestWhoamiHappyPath(unittest.TestCase):
    def setUp(self):
        _clear_env()
        os.environ["HCLOUD_TOKEN"] = "test-token"

    def tearDown(self):
        _clear_env()

    def test_returns_identity_dict(self):
        servers = {"servers": [{"name": "alpha"}, {"name": "beta"}]}
        with patch("urllib.request.urlopen", return_value=_FakeResp(servers)):
            out = hr.whoami()
        self.assertTrue(out["ok"])
        self.assertEqual(out["token_label"], "HCLOUD_TOKEN")
        self.assertEqual(out["server_count"], 2)
        self.assertEqual(out["default_server"], "alpha")

    def test_no_secret_leakage(self):
        servers = {"servers": []}
        os.environ["HCLOUD_TOKEN"] = "supersecrettoken12345"
        with patch("urllib.request.urlopen", return_value=_FakeResp(servers)):
            out = hr.whoami()
        blob = json.dumps(out)
        self.assertNotIn("supersecrettoken12345", blob)


class TestWhoamiUnconfigured(unittest.TestCase):
    def setUp(self):
        _clear_env()

    def tearDown(self):
        _clear_env()

    def test_no_env_returns_unconfigured(self):
        out = hr.whoami()
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "unconfigured")
        self.assertIn("HCLOUD_TOKEN", out["hint"])


class TestWhoamiEnvelope(unittest.TestCase):
    def setUp(self):
        _clear_env()

    def tearDown(self):
        _clear_env()

    def test_main_envelope_shape_unconfigured(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            with self.assertRaises(SystemExit) as cm:
                with patch("sys.argv", ["hcloud-remote", "whoami", "--json"]):
                    hr.main()
        self.assertEqual(cm.exception.code, 1)
        out = json.loads(buf.getvalue())
        self.assertEqual(out["tool"]["name"], "hcloud-remote")
        self.assertIn("version", out["tool"])
        self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()
