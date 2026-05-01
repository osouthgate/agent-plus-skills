"""Tests for L3 deploy --wait and structured error envelope.

Covers:
  - _parse_deploy_error: structured fields from Coolify deployment record
  - _wait_for_deployment: happy path, error path, timeout path, cancel path
  - _deploy_and_maybe_wait: end-to-end integration (mock urlopen)

stdlib unittest only.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import MagicMock, call, patch


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_env():
    for k in ("COOLIFY_URL", "COOLIFY_API_KEY"):
        os.environ.pop(k, None)


def _set_env():
    os.environ["COOLIFY_URL"] = "https://coolify.example.com"
    os.environ["COOLIFY_API_KEY"] = "test-key"


class _FakeResp:
    """Minimal urllib response mock."""
    def __init__(self, payload, status: int = 200):
        self._payload = json.dumps(payload).encode("utf-8")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def read(self) -> bytes:
        return self._payload


# ---------------------------------------------------------------------------
# _parse_deploy_error tests
# ---------------------------------------------------------------------------

class TestParseDeployError(unittest.TestCase):
    """Unit tests for the structured error extractor."""

    def test_unknown_phase_when_no_info(self):
        result = cr._parse_deploy_error({})
        self.assertEqual(result["phase"], "unknown")
        self.assertEqual(result["error_code"], "deploy_failed")
        self.assertIsNone(result["raw_error"])
        self.assertIsNone(result["failed_step"])
        self.assertIn("hint", result)

    def test_build_phase_from_status_reason(self):
        rec = {"status_reason": "build failed: Dockerfile missing"}
        result = cr._parse_deploy_error(rec)
        self.assertEqual(result["phase"], "build")
        self.assertEqual(result["raw_error"], "build failed: Dockerfile missing")
        # error_code should be first meaningful token ("build")
        self.assertEqual(result["error_code"], "build")

    def test_push_phase_from_status_reason(self):
        rec = {"status_reason": "registry push denied: unauthorized"}
        result = cr._parse_deploy_error(rec)
        self.assertEqual(result["phase"], "push")

    def test_runtime_phase_from_status_reason(self):
        rec = {"status_reason": "container failed to start: exit code 1"}
        result = cr._parse_deploy_error(rec)
        self.assertEqual(result["phase"], "runtime")

    def test_phase_from_log_markers(self):
        rec = {
            "status_reason": "generic error",
            "logs": [
                {"output": "[build] building image..."},
                {"output": "[push] pushing to registry..."},
                {"output": "Error: connection refused"},
            ],
        }
        result = cr._parse_deploy_error(rec)
        # Last phase marker seen is push
        self.assertEqual(result["phase"], "push")
        # failed_step should be the last non-empty log line
        self.assertEqual(result["failed_step"], "Error: connection refused")

    def test_failed_step_capped_at_200_chars(self):
        long_line = "x" * 300
        rec = {"logs": [{"output": long_line}]}
        result = cr._parse_deploy_error(rec)
        self.assertIsNotNone(result["failed_step"])
        self.assertLessEqual(len(result["failed_step"]), 200)

    def test_hint_is_phase_specific(self):
        build_rec = {"status_reason": "build failed"}
        push_rec = {"status_reason": "push failed registry"}
        runtime_rec = {"status_reason": "container start failed"}
        unknown_rec = {}

        build_result = cr._parse_deploy_error(build_rec)
        push_result = cr._parse_deploy_error(push_rec)
        runtime_result = cr._parse_deploy_error(runtime_rec)
        unknown_result = cr._parse_deploy_error(unknown_rec)

        # All hints should be non-empty strings
        for r in (build_result, push_result, runtime_result, unknown_result):
            self.assertIsInstance(r["hint"], str)
            self.assertGreater(len(r["hint"]), 0)

        # Hints should differ by phase
        self.assertNotEqual(build_result["hint"], push_result["hint"])
        self.assertNotEqual(build_result["hint"], runtime_result["hint"])

    def test_log_entries_as_strings(self):
        """Logs may be plain strings, not dicts."""
        rec = {
            "logs": [
                "[runtime] starting container",
                "FATAL: missing env var DATABASE_URL",
            ]
        }
        result = cr._parse_deploy_error(rec)
        self.assertEqual(result["phase"], "runtime")
        self.assertEqual(result["failed_step"], "FATAL: missing env var DATABASE_URL")


# ---------------------------------------------------------------------------
# _wait_for_deployment tests (mock client.call)
# ---------------------------------------------------------------------------

class TestWaitForDeployment(unittest.TestCase):
    """Tests for the poll-with-streaming-progress wait function."""

    def _make_client(self):
        _set_env()
        return cr.CoolifyClient()

    def setUp(self):
        _set_env()

    def tearDown(self):
        _clear_env()

    def test_happy_path_finishes(self):
        """Poll sequence: in_progress -> finished. Returns status='finished'."""
        client = self._make_client()
        responses = [
            (200, {"status": "in_progress"}),
            (200, {"status": "finished"}),
        ]
        client.call = MagicMock(side_effect=responses)

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf), patch("time.sleep"):
            result = cr._wait_for_deployment(client, "dep-uuid-1", timeout=60)

        self.assertEqual(result["status"], "finished")
        self.assertNotIn("_exit_nonzero", result)
        self.assertIn("elapsed_s", result)
        self.assertIn("deployment", result)

        # Status changes should appear on stderr
        stderr_out = stderr_buf.getvalue()
        self.assertIn("in_progress", stderr_out)
        self.assertIn("finished", stderr_out)

    def test_error_path_returns_structured_envelope(self):
        """A failed deployment returns structured error fields."""
        client = self._make_client()
        failed_dep = {
            "status": "failed",
            "status_reason": "build failed: no such file or directory",
        }
        responses = [
            (200, {"status": "in_progress"}),
            (200, failed_dep),
        ]
        client.call = MagicMock(side_effect=responses)

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf), patch("time.sleep"):
            result = cr._wait_for_deployment(client, "dep-uuid-2", timeout=60)

        self.assertEqual(result["status"], "error")
        self.assertTrue(result.get("_exit_nonzero"))
        self.assertIn("error_code", result)
        self.assertIn("phase", result)
        self.assertIn("failed_step", result)
        self.assertIn("hint", result)
        self.assertIn("raw_error", result)
        self.assertEqual(result["phase"], "build")

    def test_timeout_path(self):
        """If deployment does not finish within timeout, returns status='timeout'."""
        client = self._make_client()
        # Always return in_progress
        client.call = MagicMock(return_value=(200, {"status": "in_progress"}))

        stderr_buf = io.StringIO()
        # Patch time.time to advance quickly past the timeout
        call_count = [0]
        real_time = time.time

        def fake_time():
            call_count[0] += 1
            # First ~3 calls return real time; after 5 calls simulate 65s elapsed
            if call_count[0] > 5:
                return real_time() + 65
            return real_time()

        with patch("sys.stderr", stderr_buf), patch("time.sleep"), patch("time.time", fake_time):
            result = cr._wait_for_deployment(client, "dep-uuid-3", timeout=60)

        self.assertEqual(result["status"], "timeout")
        self.assertTrue(result.get("_exit_nonzero"))
        self.assertIn("last_state", result)
        self.assertIn("deployment_uuid", result)

    def test_cancelled_path(self):
        """A cancelled-by-user deployment returns status='cancelled'."""
        client = self._make_client()
        cancelled_dep = {
            "status": "cancelled-by-user",
            "status_reason": "user requested cancellation",
        }
        client.call = MagicMock(return_value=(200, cancelled_dep))

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf), patch("time.sleep"):
            result = cr._wait_for_deployment(client, "dep-uuid-4", timeout=60)

        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result.get("_exit_nonzero"))

    def test_poll_error_http_retries(self):
        """A transient HTTP error during polling is retried (not fatal)."""
        client = self._make_client()
        responses = [
            (503, None),          # transient error -- should retry
            (200, {"status": "finished"}),
        ]
        client.call = MagicMock(side_effect=responses)

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf), patch("time.sleep"):
            result = cr._wait_for_deployment(client, "dep-uuid-5", timeout=60)

        self.assertEqual(result["status"], "finished")

    def test_stderr_only_prints_on_state_change(self):
        """Status lines on stderr appear only when state changes, not every poll."""
        client = self._make_client()
        responses = [
            (200, {"status": "in_progress"}),
            (200, {"status": "in_progress"}),   # same state -- no extra line
            (200, {"status": "in_progress"}),   # same state -- no extra line
            (200, {"status": "finished"}),
        ]
        client.call = MagicMock(side_effect=responses)

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf), patch("time.sleep"):
            cr._wait_for_deployment(client, "dep-uuid-6", timeout=60)

        lines = [l for l in stderr_buf.getvalue().splitlines() if "in_progress" in l]
        # Should appear exactly once (state change, not every poll)
        self.assertEqual(len(lines), 1)


# ---------------------------------------------------------------------------
# _deploy_and_maybe_wait integration tests
# ---------------------------------------------------------------------------

class TestDeployAndMaybeWait(unittest.TestCase):
    """End-to-end tests for _deploy_and_maybe_wait (mock urlopen)."""

    def setUp(self):
        _set_env()

    def tearDown(self):
        _clear_env()

    def _client(self):
        return cr.CoolifyClient()

    def _trigger_resp(self, dep_uuid: str = "dep-abc") -> dict:
        return {"deployments": [{"resource_uuid": dep_uuid}]}

    def test_no_wait_returns_immediately(self):
        """--no-wait: only one HTTP call (the trigger)."""
        client = self._client()
        client.call = MagicMock(return_value=(200, self._trigger_resp()))
        app = {"uuid": "app-uuid", "name": "myapp"}

        stdout_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf):
            cr._deploy_and_maybe_wait(client, app, wait=False, force=True, timeout=60)

        # Only the trigger call
        self.assertEqual(client.call.call_count, 1)

    def test_wait_happy_path_emits_success_json(self):
        """--wait with successful deploy emits JSON with status='finished'."""
        client = self._client()
        trigger_data = self._trigger_resp("dep-123")
        poll_responses = [
            (200, {"status": "in_progress"}),
            (200, {"status": "finished"}),
        ]
        client.call = MagicMock(
            side_effect=[(200, trigger_data)] + poll_responses
        )
        app = {"uuid": "app-uuid", "name": "myapp"}

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf), patch("time.sleep"):
            cr._deploy_and_maybe_wait(client, app, wait=True, force=True, timeout=60)

        # stdout has a plain-text trigger line followed by a JSON block;
        # find the JSON by locating the first '{'.
        raw = stdout_buf.getvalue()
        json_start = raw.index("{")
        out = json.loads(raw[json_start:])
        self.assertEqual(out["status"], "finished")
        self.assertIn("tool", out)

    def test_wait_error_path_emits_structured_error_and_exits_1(self):
        """--wait with failed deploy emits structured error JSON and exits 1."""
        client = self._client()
        trigger_data = self._trigger_resp("dep-456")
        failed_dep = {
            "status": "failed",
            "status_reason": "build failed: syntax error in Dockerfile",
        }
        client.call = MagicMock(
            side_effect=[(200, trigger_data), (200, failed_dep)]
        )
        app = {"uuid": "app-uuid", "name": "myapp"}

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf), patch("time.sleep"):
            with self.assertRaises(SystemExit) as ctx:
                cr._deploy_and_maybe_wait(client, app, wait=True, force=True, timeout=60)

        self.assertEqual(ctx.exception.code, 1)
        raw = stdout_buf.getvalue()
        json_start = raw.index("{")
        out = json.loads(raw[json_start:])
        self.assertEqual(out["status"], "error")
        self.assertIn("phase", out)
        self.assertIn("hint", out)
        self.assertIn("error_code", out)

    def test_wait_timeout_exits_1(self):
        """--wait with timeout exits 1 and emits status='timeout' JSON."""
        client = self._client()
        trigger_data = self._trigger_resp("dep-789")

        client.call = MagicMock(return_value=(200, {"status": "in_progress"}))
        # Override the trigger call
        client.call.side_effect = [(200, trigger_data)] + [(200, {"status": "in_progress"})] * 200

        app = {"uuid": "app-uuid", "name": "myapp"}

        call_count = [0]
        real_time = time.time

        def fake_time():
            call_count[0] += 1
            if call_count[0] > 5:
                return real_time() + 65
            return real_time()

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf), \
             patch("time.sleep"), patch("time.time", fake_time):
            with self.assertRaises(SystemExit) as ctx:
                cr._deploy_and_maybe_wait(client, app, wait=True, force=True, timeout=60)

        self.assertEqual(ctx.exception.code, 1)
        raw = stdout_buf.getvalue()
        json_start = raw.index("{")
        out = json.loads(raw[json_start:])
        self.assertEqual(out["status"], "timeout")


if __name__ == "__main__":
    unittest.main()
