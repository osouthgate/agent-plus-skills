"""Tests for _looks_like_uuid, _resolve_trace, _resolve_session, _resolve_project.

Coverage:
  - _looks_like_uuid: 32/36 char hex with alpha guard (rejects pure-numeric)
  - _resolve_trace: UUID direct hit, UUID 404 falls back to name, name match,
    name miss (raises with known names listed)
  - _resolve_session: exact ID hit, exact ID 404 falls back to prefix scan,
    prefix match, miss (raises with known IDs listed)
  - _resolve_project: exact ID match, name match (case-insensitive), miss
  - argparse: three new subcommands wired up
  - Q3 absence: ingestion is fire-and-forget -- trace-ping does NOT have --wait
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Module loader
# ---------------------------------------------------------------------------

def _load_module():
    here = Path(__file__).resolve()
    bin_path = here.parent.parent / "bin" / "langfuse-remote"
    loader = SourceFileLoader("langfuse_remote", str(bin_path))
    spec = importlib.util.spec_from_loader("langfuse_remote", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


lr = _load_module()


# ---------------------------------------------------------------------------
# _looks_like_uuid
# ---------------------------------------------------------------------------

class TestLooksLikeUuid(unittest.TestCase):
    def test_36_char_uuid(self):
        self.assertTrue(lr._looks_like_uuid("01234567-89ab-cdef-0123-456789abcdef"))

    def test_32_char_hex(self):
        self.assertTrue(lr._looks_like_uuid("0123456789abcdef0123456789abcdef"))

    def test_pure_numeric_32_rejected(self):
        # 32 digits with no hex letters: not a UUID, treated as name input.
        self.assertFalse(lr._looks_like_uuid("12345678901234567890123456789012"))

    def test_wrong_length(self):
        self.assertFalse(lr._looks_like_uuid("abc"))
        self.assertFalse(lr._looks_like_uuid(""))

    def test_invalid_char(self):
        self.assertFalse(lr._looks_like_uuid("01234567-89ab-cdef-0123-456789abcdez"))


# ---------------------------------------------------------------------------
# _resolve_trace
# ---------------------------------------------------------------------------

# A UUID-shaped string (32 hex chars) that the resolver will try direct lookup for.
_TRACE_UUID = "aabbccdd11223344aabbccdd11223344"
_TRACE_OBJ = {"id": _TRACE_UUID, "name": "my-trace", "timestamp": "2026-01-01T00:00:00Z"}


class TestResolveTrace(unittest.TestCase):
    def _inst(self):
        return {
            "base_url": "https://fake.langfuse.test",
            "public_key": "pk-test",
            "secret_key": "sk-test",
            "name": "test",
        }

    def test_uuid_direct_hit(self):
        """Direct GET /traces/{id} succeeds -- no fallback needed."""
        inst = self._inst()
        with patch.object(lr, "api_get_optional", return_value=(200, _TRACE_OBJ)) as mock:
            result = lr._resolve_trace(inst, _TRACE_UUID)
        self.assertEqual(result["id"], _TRACE_UUID)
        mock.assert_called_once_with(inst, f"/api/public/traces/{_TRACE_UUID}")

    def test_uuid_404_falls_back_to_name_search(self):
        """If the direct ID lookup returns 404, fall back to ?name= filter."""
        inst = self._inst()
        name_results = {"data": [_TRACE_OBJ], "meta": {}}
        # First call: direct ID -> 404; subsequent calls: name filter -> hit
        with patch.object(lr, "api_get_optional", return_value=(404, "not found")):
            with patch.object(lr, "api_get", return_value=name_results):
                result = lr._resolve_trace(inst, _TRACE_UUID)
        self.assertEqual(result["id"], _TRACE_UUID)

    def test_name_match(self):
        """Non-UUID input bypasses direct lookup and uses ?name= filter."""
        inst = self._inst()
        name_results = {"data": [_TRACE_OBJ], "meta": {}}
        # api_get_optional should NOT be called for non-UUID inputs.
        with patch.object(lr, "api_get_optional") as mock_opt:
            with patch.object(lr, "api_get", return_value=name_results):
                result = lr._resolve_trace(inst, "my-trace")
        mock_opt.assert_not_called()
        self.assertEqual(result["name"], "my-trace")

    def test_name_miss_raises_with_known_names(self):
        """On name miss, raises RuntimeError containing known names."""
        inst = self._inst()
        empty = {"data": [], "meta": {}}
        known = {"data": [{"id": "x", "name": "other-trace"}], "meta": {}}
        call_count = [0]
        def fake_api_get(inst, path, query=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return empty   # name filter returns nothing
            return known       # fallback list
        with patch.object(lr, "api_get", side_effect=fake_api_get):
            with self.assertRaises(RuntimeError) as ctx:
                lr._resolve_trace(inst, "nonexistent-trace")
        self.assertIn("other-trace", str(ctx.exception))


# ---------------------------------------------------------------------------
# _resolve_session
# ---------------------------------------------------------------------------

_SESSION_ID = "sess-abc-123"
_SESSION_OBJ = {"id": _SESSION_ID, "createdAt": "2026-01-01T00:00:00Z", "traces": []}


class TestResolveSession(unittest.TestCase):
    def _inst(self):
        return {
            "base_url": "https://fake.langfuse.test",
            "public_key": "pk-test",
            "secret_key": "sk-test",
            "name": "test",
        }

    def test_exact_id_hit(self):
        """Direct GET /sessions/{id} succeeds."""
        inst = self._inst()
        with patch.object(lr, "api_get_optional", return_value=(200, _SESSION_OBJ)) as mock:
            result = lr._resolve_session(inst, _SESSION_ID)
        self.assertEqual(result["id"], _SESSION_ID)
        mock.assert_called_once_with(inst, f"/api/public/sessions/{_SESSION_ID}")

    def test_exact_id_404_then_prefix_match(self):
        """404 on direct lookup, then prefix scan finds the session."""
        inst = self._inst()
        list_body = {"data": [{"id": _SESSION_ID}], "meta": {}}
        # Calls: (1) direct lookup -> 404, (2) full detail after prefix match -> 200
        opt_returns = [(404, "not found"), (200, _SESSION_OBJ)]
        opt_iter = iter(opt_returns)
        with patch.object(lr, "api_get_optional", side_effect=lambda *a, **kw: next(opt_iter)):
            with patch.object(lr, "api_get", return_value=list_body):
                result = lr._resolve_session(inst, "sess-abc")  # prefix
        self.assertEqual(result["id"], _SESSION_ID)

    def test_miss_raises_with_known_ids(self):
        """On prefix miss, raises RuntimeError containing known session IDs."""
        inst = self._inst()
        list_body = {"data": [{"id": "other-session-xyz"}], "meta": {}}
        with patch.object(lr, "api_get_optional", return_value=(404, "not found")):
            with patch.object(lr, "api_get", return_value=list_body):
                with self.assertRaises(RuntimeError) as ctx:
                    lr._resolve_session(inst, "nonexistent-sess")
        self.assertIn("other-session-xyz", str(ctx.exception))


# ---------------------------------------------------------------------------
# _resolve_project
# ---------------------------------------------------------------------------

_PROJ_UUID = "proj-uuid-0001"
_PROJ_OBJ = {"id": _PROJ_UUID, "name": "My Project"}


class TestResolveProject(unittest.TestCase):
    def _inst(self):
        return {
            "base_url": "https://fake.langfuse.test",
            "public_key": "pk-test",
            "secret_key": "sk-test",
            "name": "test",
        }

    def test_id_match(self):
        """Exact UUID match."""
        inst = self._inst()
        body = {"data": [_PROJ_OBJ]}
        with patch.object(lr, "api_get", return_value=body):
            result = lr._resolve_project(inst, _PROJ_UUID)
        self.assertEqual(result["id"], _PROJ_UUID)

    def test_name_match_case_insensitive(self):
        """Name match is case-insensitive."""
        inst = self._inst()
        body = {"data": [_PROJ_OBJ]}
        with patch.object(lr, "api_get", return_value=body):
            result = lr._resolve_project(inst, "my project")  # lowercase
        self.assertEqual(result["name"], "My Project")

    def test_miss_raises_with_known_names(self):
        """On miss, raises RuntimeError listing known project names."""
        inst = self._inst()
        body = {"data": [_PROJ_OBJ]}
        with patch.object(lr, "api_get", return_value=body):
            with self.assertRaises(RuntimeError) as ctx:
                lr._resolve_project(inst, "no-such-project")
        self.assertIn("My Project", str(ctx.exception))


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------

class TestArgparseResolvers(unittest.TestCase):
    def test_resolve_trace_subcommand(self):
        p = lr.build_parser()
        args = p.parse_args(["resolve-trace", "my-trace"])
        self.assertEqual(args.cmd, "resolve-trace")
        self.assertEqual(args.id_or_name, "my-trace")
        self.assertEqual(args.func, lr.cmd_resolve_trace)

    def test_resolve_session_subcommand(self):
        p = lr.build_parser()
        args = p.parse_args(["resolve-session", "sess-abc"])
        self.assertEqual(args.cmd, "resolve-session")
        self.assertEqual(args.id_or_name, "sess-abc")
        self.assertEqual(args.func, lr.cmd_resolve_session)

    def test_resolve_project_subcommand(self):
        p = lr.build_parser()
        args = p.parse_args(["resolve-project", "My Project"])
        self.assertEqual(args.cmd, "resolve-project")
        self.assertEqual(args.id_or_name, "My Project")
        self.assertEqual(args.func, lr.cmd_resolve_project)

    def test_resolve_trace_pretty_flag(self):
        p = lr.build_parser()
        args = p.parse_args(["resolve-trace", "--pretty", "my-trace"])
        self.assertTrue(args.pretty)

    def test_trace_ping_has_no_wait_flag(self):
        """Q3 documented absence: trace-ping must NOT accept --wait."""
        p = lr.build_parser()
        args = p.parse_args(["trace-ping"])
        self.assertFalse(hasattr(args, "wait"))


if __name__ == "__main__":
    unittest.main()
