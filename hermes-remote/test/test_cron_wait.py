"""Tests for cron trigger --wait (wait_cron_run helper).

All tests are stdlib unittest only — no network calls. The HermesClient.call
method is monkey-patched to simulate Hermes API responses.
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
from unittest.mock import MagicMock, patch


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


def _make_job(job_id: str = "job-1", completed: int = 0, state: str = "idle") -> dict:
    return {
        "id": job_id,
        "name": "my-job",
        "state": state,
        "schedule_display": "every 1h",
        "repeat": {"completed": completed},
    }


def _make_client(call_side_effect) -> hr.HermesClient:
    """Create a HermesClient with `.call` replaced by a mock."""
    client = object.__new__(hr.HermesClient)
    client.call = MagicMock(side_effect=call_side_effect)
    return client


class TestWaitCronRunHappyPath(unittest.TestCase):
    """wait_cron_run returns success when completed count increments."""

    def test_success_immediate(self):
        """First poll already shows incremented count — instant success."""
        calls = [
            # First poll: completed=1 (baseline was 0)
            (200, [_make_job(completed=1)]),
        ]
        client = _make_client(lambda *a, **kw: calls.pop(0))
        result = client.wait_cron_run(
            "job-1", baseline_completed=0, timeout=30, poll_interval=1
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["job_id"], "job-1")
        self.assertEqual(result["completed_runs"], 1)
        self.assertIn("job", result)
        self.assertIn("elapsed_s", result)
        self.assertNotIn("_exit_nonzero", result)

    def test_success_after_several_polls(self):
        """count starts at 0, increments to 1 on the third poll."""
        poll_responses = [
            (200, [_make_job(completed=0)]),
            (200, [_make_job(completed=0)]),
            (200, [_make_job(completed=1)]),
        ]
        client = _make_client(lambda *a, **kw: poll_responses.pop(0))
        with patch.object(hr.time, "sleep", return_value=None):
            result = client.wait_cron_run(
                "job-1", baseline_completed=0, timeout=30, poll_interval=1
            )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["completed_runs"], 1)

    def test_success_with_nonzero_baseline(self):
        """Baseline of 5; completion detected at 6."""
        poll_responses = [
            (200, [_make_job(completed=5)]),
            (200, [_make_job(completed=6)]),
        ]
        client = _make_client(lambda *a, **kw: poll_responses.pop(0))
        with patch.object(hr.time, "sleep", return_value=None):
            result = client.wait_cron_run(
                "job-1", baseline_completed=5, timeout=30, poll_interval=1
            )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["completed_runs"], 6)


class TestWaitCronRunTimeout(unittest.TestCase):
    """wait_cron_run returns timeout sentinel when the clock expires."""

    def test_timeout_before_completion(self):
        """Time advances past timeout before completed count increments."""
        # Simulate time advancing: patch time.monotonic to return an
        # increasing sequence — first call (start) returns 0, each subsequent
        # call returns start + elapsed.
        monotonic_values = [0.0, 0.0, 700.0]  # start=0, check=0, check=700 > timeout=600
        monotonic_iter = iter(monotonic_values)

        poll_responses = [
            (200, [_make_job(completed=0)]),
            (200, [_make_job(completed=0)]),
        ]

        def _call(*a, **kw):
            return poll_responses.pop(0) if poll_responses else (200, [_make_job(completed=0)])

        client = _make_client(_call)
        with patch.object(hr.time, "monotonic", side_effect=monotonic_iter):
            with patch.object(hr.time, "sleep", return_value=None):
                result = client.wait_cron_run(
                    "job-1", baseline_completed=0, timeout=600, poll_interval=1
                )
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(result.get("_exit_nonzero"))
        self.assertEqual(result["job_id"], "job-1")
        self.assertIn("elapsed_s", result)


class TestWaitCronRunErrors(unittest.TestCase):
    """wait_cron_run surfaces poll errors correctly."""

    def test_http_error_on_poll(self):
        """Non-200 from /api/cron/jobs returns poll_error."""
        client = _make_client(lambda *a, **kw: (503, b"Service Unavailable"))
        result = client.wait_cron_run(
            "job-1", baseline_completed=0, timeout=30, poll_interval=1
        )
        self.assertEqual(result["status"], "poll_error")
        self.assertTrue(result.get("_exit_nonzero"))
        self.assertIn("503", result["error"])

    def test_exception_on_poll(self):
        """Network exception during poll returns poll_error."""
        def _raise(*a, **kw):
            raise OSError("connection reset")

        client = _make_client(_raise)
        result = client.wait_cron_run(
            "job-1", baseline_completed=0, timeout=30, poll_interval=1
        )
        self.assertEqual(result["status"], "poll_error")
        self.assertTrue(result.get("_exit_nonzero"))
        self.assertIn("connection reset", result["error"])

    def test_job_disappeared(self):
        """Job absent from list during poll returns poll_error."""
        # Returns a list with a different job ID.
        other_job = _make_job(job_id="other-job", completed=0)
        client = _make_client(lambda *a, **kw: (200, [other_job]))
        result = client.wait_cron_run(
            "job-1", baseline_completed=0, timeout=30, poll_interval=1
        )
        self.assertEqual(result["status"], "poll_error")
        self.assertTrue(result.get("_exit_nonzero"))
        self.assertIn("disappeared", result["error"])


class TestWaitCronRunMinPollInterval(unittest.TestCase):
    """poll_interval is floored at WAIT_MIN_POLL_INTERVAL."""

    def test_poll_interval_floor(self):
        """poll_interval=0 is coerced to WAIT_MIN_POLL_INTERVAL (2 s)."""
        sleep_calls: list[float] = []

        poll_responses = [
            (200, [_make_job(completed=0)]),
            (200, [_make_job(completed=1)]),
        ]

        def _call(*a, **kw):
            return poll_responses.pop(0)

        def _fake_sleep(secs: float) -> None:
            sleep_calls.append(secs)

        client = _make_client(_call)
        with patch.object(hr.time, "sleep", side_effect=_fake_sleep):
            result = client.wait_cron_run(
                "job-1", baseline_completed=0, timeout=30, poll_interval=0
            )
        self.assertEqual(result["status"], "success")
        # Each sleep call should be >= WAIT_MIN_POLL_INTERVAL
        for s in sleep_calls:
            self.assertGreaterEqual(s, hr.WAIT_MIN_POLL_INTERVAL)


class TestCmdCronTriggerWait(unittest.TestCase):
    """Integration: cmd_cron_trigger with --wait flag via main()."""

    def _env(self):
        return {
            "HERMES_URL": "https://hermes.example.com",
            "HERMES_PASSWORD": "secret",
            "HERMES_ADMIN_USER": "admin@example.com",
        }

    def test_wait_success_exits_zero(self):
        """cmd_cron_trigger --wait prints JSON and exits 0 on success."""
        jobs = [_make_job(job_id="job-1", completed=0)]
        jobs_after = [_make_job(job_id="job-1", completed=1)]

        call_seq = [
            # pre-trigger jobs list (to capture baseline)
            (200, [_make_job(job_id="job-1", completed=0)]),
            # the POST trigger
            (200, {"ok": True}),
            # poll iteration: completed=1
            (200, [_make_job(job_id="job-1", completed=1)]),
        ]

        buf = io.StringIO()
        with patch.dict(os.environ, self._env(), clear=False):
            with patch.object(hr.HermesClient, "__init__", return_value=None):
                with patch.object(
                    hr.HermesClient, "call",
                    side_effect=lambda *a, **kw: call_seq.pop(0)
                ):
                    # Stub login internals so HermesClient.__init__ doesn't fire network.
                    client = object.__new__(hr.HermesClient)
                    client.call = MagicMock(side_effect=lambda *a, **kw: call_seq.pop(0))

                    args = hr.build_parser().parse_args(
                        ["cron", "trigger", "job-1", "--wait", "--timeout", "30", "--poll-interval", "1"]
                    )
                    with patch("sys.stdout", buf):
                        with patch.object(hr.time, "sleep", return_value=None):
                            hr.cmd_cron_trigger(client, args)

        out = json.loads(buf.getvalue())
        self.assertEqual(out["status"], "success")
        self.assertNotIn("_exit_nonzero", out)

    def test_wait_timeout_exits_nonzero(self):
        """cmd_cron_trigger --wait exits 1 and prints JSON on timeout."""
        # Monotonic: start=0, first elapsed check=0, second elapsed check=700 (> 30 s timeout)
        mono_vals = [0.0, 0.0, 700.0]
        mono_iter = iter(mono_vals)

        call_seq = [
            # pre-trigger jobs list
            (200, [_make_job(job_id="job-1", completed=0)]),
            # POST trigger
            (200, {"ok": True}),
            # poll — still 0
            (200, [_make_job(job_id="job-1", completed=0)]),
        ]

        buf = io.StringIO()

        with patch.object(hr.time, "monotonic", side_effect=mono_iter):
            with patch.object(hr.time, "sleep", return_value=None):
                client = object.__new__(hr.HermesClient)
                client.call = MagicMock(side_effect=lambda *a, **kw: call_seq.pop(0))

                args = hr.build_parser().parse_args(
                    ["cron", "trigger", "job-1", "--wait", "--timeout", "30"]
                )
                with patch("sys.stdout", buf):
                    with self.assertRaises(SystemExit) as cm:
                        hr.cmd_cron_trigger(client, args)

        self.assertEqual(cm.exception.code, 1)
        out = json.loads(buf.getvalue())
        self.assertEqual(out["status"], "timeout")
        self.assertTrue(out.get("_exit_nonzero"))

    def test_no_wait_fires_and_returns(self):
        """Without --wait, trigger just prints status and returns immediately."""
        call_seq = [
            (200, {"ok": True}),
        ]
        client = object.__new__(hr.HermesClient)
        client.call = MagicMock(side_effect=lambda *a, **kw: call_seq.pop(0))

        args = hr.build_parser().parse_args(["cron", "trigger", "job-1"])

        captured = io.StringIO()
        with patch("sys.stdout", captured):
            hr.cmd_cron_trigger(client, args)

        self.assertIn("triggered job-1", captured.getvalue())
        # call was made exactly once (the POST trigger only — no pre-check)
        self.assertEqual(client.call.call_count, 1)


if __name__ == "__main__":
    unittest.main()
