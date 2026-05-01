"""Tests for _is_int, resolve_image, wait_action, and --wait argparse wiring."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import MagicMock, patch


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


# ---------------------------------------------------------------------------
# _is_int
# ---------------------------------------------------------------------------

class TestIsInt(unittest.TestCase):
    def test_numeric_string(self):
        self.assertTrue(hr._is_int("12345"))

    def test_single_digit(self):
        self.assertTrue(hr._is_int("0"))

    def test_empty_string(self):
        self.assertFalse(hr._is_int(""))

    def test_non_numeric(self):
        self.assertFalse(hr._is_int("abc"))

    def test_alphanumeric(self):
        self.assertFalse(hr._is_int("12abc"))

    def test_negative_string(self):
        # isdigit() returns False for '-1'
        self.assertFalse(hr._is_int("-1"))

    def test_float_string(self):
        self.assertFalse(hr._is_int("1.5"))


# ---------------------------------------------------------------------------
# resolve_image
# ---------------------------------------------------------------------------

class TestResolveImage(unittest.TestCase):
    def setUp(self):
        os.environ["HCLOUD_TOKEN"] = "test-token"
        self.client = hr.HCloudClient()

    def tearDown(self):
        os.environ.pop("HCLOUD_TOKEN", None)

    def test_numeric_id_found(self):
        img = {"id": 42, "name": "ubuntu-20.04"}
        self.client.call = MagicMock(return_value=(200, {"image": img}))
        result = self.client.resolve_image("42")
        self.client.call.assert_called_once_with("GET", "/images/42")
        self.assertEqual(result["id"], 42)

    def test_numeric_id_not_found(self):
        self.client.call = MagicMock(return_value=(404, {"error": {"code": "not_found"}}))
        with self.assertRaises(SystemExit):
            self.client.resolve_image("9999")

    def test_name_match(self):
        img = {"id": 7, "name": "debian-11"}
        # First call: name filter returns one result
        self.client.call = MagicMock(return_value=(200, {"images": [img]}))
        result = self.client.resolve_image("debian-11")
        self.assertEqual(result["name"], "debian-11")
        self.client.call.assert_called_once_with(
            "GET", "/images", query={"name": "debian-11"}
        )

    def test_name_miss_raises_with_known_names(self):
        known_img = {"id": 7, "name": "debian-11", "description": "Debian 11"}
        # First call: name filter returns empty; second call: full list
        self.client.call = MagicMock(side_effect=[
            (200, {"images": []}),          # ?name= filter -- miss
            (200, {"images": [known_img]}), # fallback full list
        ])
        with self.assertRaises(SystemExit) as ctx:
            self.client.resolve_image("nonexistent")
        self.assertIn("debian-11", str(ctx.exception))


# ---------------------------------------------------------------------------
# wait_action
# ---------------------------------------------------------------------------

class TestWaitAction(unittest.TestCase):
    def setUp(self):
        os.environ["HCLOUD_TOKEN"] = "test-token"
        self.client = hr.HCloudClient()

    def tearDown(self):
        os.environ.pop("HCLOUD_TOKEN", None)

    def test_success_path(self):
        action = {"id": 1, "status": "success", "progress": 100}
        self.client.call = MagicMock(return_value=(200, {"action": action}))
        result = self.client.wait_action(1, timeout=30, poll_interval=1)
        self.assertEqual(result["status"], "success")
        self.assertNotIn("_exit_nonzero", result)
        self.assertIn("elapsed_s", result)

    def test_error_path(self):
        action = {
            "id": 2,
            "status": "error",
            "progress": 0,
            "error": {"code": "server_error", "message": "something broke"},
        }
        self.client.call = MagicMock(return_value=(200, {"action": action}))
        result = self.client.wait_action(2, timeout=30, poll_interval=1)
        self.assertEqual(result["status"], "error")
        self.assertTrue(result.get("_exit_nonzero"))
        self.assertEqual(result["error_code"], "server_error")
        self.assertEqual(result["error_message"], "something broke")

    def test_timeout_path(self):
        # First call returns 'running', then timeout triggers immediately
        # by manipulating time.monotonic
        action_running = {"id": 3, "status": "running", "progress": 50}
        self.client.call = MagicMock(return_value=(200, {"action": action_running}))

        # Patch time so that after one sleep the elapsed time exceeds timeout
        call_count = [0]
        base = time.monotonic()
        def fake_monotonic():
            # Returns: 0, 0, 60 (triggers timeout check after first poll)
            call_count[0] += 1
            if call_count[0] <= 2:
                return base
            return base + 60  # past timeout=30

        with patch("time.monotonic", side_effect=fake_monotonic):
            with patch("time.sleep"):
                result = self.client.wait_action(3, timeout=30, poll_interval=2)

        self.assertEqual(result["status"], "timeout")
        self.assertTrue(result.get("_exit_nonzero"))
        self.assertIn("last_state", result)

    def test_running_then_success(self):
        running_action = {"id": 4, "status": "running", "progress": 50}
        success_action = {"id": 4, "status": "success", "progress": 100}
        self.client.call = MagicMock(side_effect=[
            (200, {"action": running_action}),
            (200, {"action": success_action}),
        ])
        with patch("time.sleep"):
            result = self.client.wait_action(4, timeout=60, poll_interval=2)
        self.assertEqual(result["status"], "success")
        self.assertNotIn("_exit_nonzero", result)
        self.assertEqual(self.client.call.call_count, 2)


# ---------------------------------------------------------------------------
# argparse --wait wiring
# ---------------------------------------------------------------------------

class TestArgparseWait(unittest.TestCase):
    def test_reboot_accepts_wait(self):
        p = hr.build_parser()
        args = p.parse_args(["server", "reboot", "--yes", "--wait", "my-server"])
        self.assertTrue(args.wait)
        self.assertEqual(args.timeout, hr.WAIT_DEFAULT_TIMEOUT)
        self.assertEqual(args.poll_interval, hr.WAIT_DEFAULT_POLL_INTERVAL)

    def test_reboot_wait_custom_timeout(self):
        p = hr.build_parser()
        args = p.parse_args(
            ["server", "reboot", "--yes", "--wait", "--timeout", "30", "my-server"]
        )
        self.assertTrue(args.wait)
        self.assertEqual(args.timeout, 30)

    def test_reboot_no_wait_by_default(self):
        p = hr.build_parser()
        args = p.parse_args(["server", "reboot", "--yes", "my-server"])
        self.assertFalse(args.wait)

    def test_snapshot_create_accepts_wait(self):
        p = hr.build_parser()
        args = p.parse_args(
            ["snapshot", "create", "--wait", "--timeout", "120", "--poll-interval", "3", "my-server"]
        )
        self.assertTrue(args.wait)
        self.assertEqual(args.timeout, 120)
        self.assertEqual(args.poll_interval, 3)

    def test_snapshot_create_no_wait_by_default(self):
        p = hr.build_parser()
        args = p.parse_args(["snapshot", "create", "my-server"])
        self.assertFalse(args.wait)


if __name__ == "__main__":
    unittest.main()
