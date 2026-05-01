"""Unit tests for github-remote. Stdlib unittest only — no pytest, no GitHub account."""

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
    bin_path = here.parent.parent / "bin" / "github-remote"
    loader = SourceFileLoader("github_remote", str(bin_path))
    spec = importlib.util.spec_from_loader("github_remote", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


gr = _load_module()


# ──────────────────────────── _scrub ────────────────────────────


class TestScrub(unittest.TestCase):
    def test_strips_top_level_secrets(self) -> None:
        obj = {
            "id": "x",
            "token": "ghp_abc",
            "password": "hunter2",
            "authorization": "Bearer xyz",
            "client_secret": "sec",
            "private_key": "BEGIN KEY",
            "webhook_url_with_secret": "https://example.com?s=abc",
        }
        out = gr._scrub(obj)
        self.assertEqual(out["id"], "x")
        for k in ("token", "password", "authorization", "client_secret", "private_key", "webhook_url_with_secret"):
            self.assertEqual(out[k], "[REDACTED]", f"{k} not redacted")

    def test_strips_nested_secrets(self) -> None:
        obj = {
            "meta": {"sha": "abc123", "token": "ghp_xxx"},
            "auth": {"password": "super-secret", "enabled": True},
        }
        out = gr._scrub(obj)
        self.assertEqual(out["meta"]["sha"], "abc123")
        self.assertEqual(out["meta"]["token"], "[REDACTED]")
        self.assertEqual(out["auth"]["password"], "[REDACTED]")
        self.assertTrue(out["auth"]["enabled"])

    def test_case_insensitive_key_match(self) -> None:
        obj = {"Token": "x", "PASSWORD": "y", "Client_Secret": "z"}
        out = gr._scrub(obj)
        self.assertEqual(out["Token"], "[REDACTED]")
        self.assertEqual(out["PASSWORD"], "[REDACTED]")
        self.assertEqual(out["Client_Secret"], "[REDACTED]")

    def test_lists_walked(self) -> None:
        obj = {"items": [{"token": "a"}, {"token": "b"}, {"safe": "ok"}]}
        out = gr._scrub(obj)
        self.assertEqual(out["items"][0]["token"], "[REDACTED]")
        self.assertEqual(out["items"][1]["token"], "[REDACTED]")
        self.assertEqual(out["items"][2]["safe"], "ok")

    def test_primitives_pass_through(self) -> None:
        self.assertEqual(gr._scrub("hello"), "hello")
        self.assertEqual(gr._scrub(42), 42)
        self.assertEqual(gr._scrub(None), None)


# ──────────────────────────── _scrub_text (regex patterns) ────────────────────────────


class TestScrubText(unittest.TestCase):
    def test_ghp_classic(self) -> None:
        s = "token is ghp_" + "A" * 36 + " visible"
        self.assertNotIn("ghp_" + "A" * 36, gr._scrub_text(s))
        self.assertIn("[REDACTED]", gr._scrub_text(s))

    def test_github_pat_fine_grained(self) -> None:
        s = "pat: github_pat_" + "B" * 82
        self.assertNotIn("github_pat_" + "B" * 82, gr._scrub_text(s))

    def test_oauth_prefixes(self) -> None:
        for prefix in ("gho_", "ghu_", "ghs_", "ghr_"):
            s = f"key {prefix}{'Z' * 36} here"
            self.assertNotIn(f"{prefix}{'Z' * 36}", gr._scrub_text(s))

    def test_aws_key(self) -> None:
        s = "aws AKIAIOSFODNN7EXAMPLE thing"
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", gr._scrub_text(s))

    def test_bearer_token(self) -> None:
        s = "Authorization: Bearer abcdef1234567890abcdef1234"
        self.assertNotIn("abcdef1234567890abcdef1234", gr._scrub_text(s))

    def test_empty_passthrough(self) -> None:
        self.assertEqual(gr._scrub_text(""), "")

    def test_benign_text_passthrough(self) -> None:
        s = "hello world, no secrets here, build passed"
        self.assertEqual(gr._scrub_text(s), s)


# ──────────────────────────── canary no-leak ────────────────────────────


class TestCanaryNoLeak(unittest.TestCase):
    """A known canary secret substring must never appear in any emitted output."""

    CANARY = "CANARY_SECRET_DO_NOT_LEAK_4f2b9a"

    def _capture_emit(self, obj) -> str:
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            gr.emit_json(obj, pretty=True)
        return buf.getvalue()

    def test_canary_in_token_key(self) -> None:
        obj = {"user": "alice", "token": self.CANARY}
        out = self._capture_emit(obj)
        self.assertNotIn(self.CANARY, out)
        self.assertIn("alice", out)

    def test_canary_in_password_key(self) -> None:
        obj = {"auth": {"password": self.CANARY, "enabled": True}}
        self.assertNotIn(self.CANARY, self._capture_emit(obj))

    def test_canary_in_nested_client_secret(self) -> None:
        obj = {"a": [{"b": {"client_secret": self.CANARY}}]}
        self.assertNotIn(self.CANARY, self._capture_emit(obj))

    def test_canary_in_free_text_regex_patterns(self) -> None:
        # Pattern-matched secrets in free-text logs should be scrubbed.
        fake_log = f"build failed, token leaked: ghp_{'A' * 36} and {self.CANARY}"
        # The CANARY itself isn't a matching pattern, but the ghp_ must go.
        scrubbed = gr._scrub_text(fake_log)
        self.assertNotIn(f"ghp_{'A' * 36}", scrubbed)

    def test_canary_in_run_log_lines_structure(self) -> None:
        # Simulated run_logs output shape: jobs -> lines. _scrub preserves lines
        # (not a secret key), so canary-via-regex-pattern is the vector here.
        leaked_line = f"Error: Bearer {'X' * 30}"
        obj = {"run": 1, "jobs": [{"id": 2, "name": "j", "lines": [gr._scrub_text(leaked_line)]}]}
        out = self._capture_emit(obj)
        self.assertNotIn("X" * 30, out)


# ──────────────────────────── auth precedence ────────────────────────────


class TestAuthPrecedence(unittest.TestCase):
    def test_env_var_wins(self) -> None:
        cfg = {"GITHUB_TOKEN": "from_env"}
        with patch.object(gr, "_gh_auth_token", return_value="from_gh"):
            self.assertEqual(gr.require_token(cfg), "from_env")

    def test_gh_fallback_when_env_missing(self) -> None:
        with patch.object(gr, "_gh_auth_token", return_value="from_gh"):
            self.assertEqual(gr.require_token({}), "from_gh")

    def test_both_missing_dies(self) -> None:
        with patch.object(gr, "_gh_auth_token", return_value=None):
            with self.assertRaises(SystemExit):
                gr.require_token({})

    def test_empty_env_token_falls_back(self) -> None:
        with patch.object(gr, "_gh_auth_token", return_value="from_gh"):
            self.assertEqual(gr.require_token({"GITHUB_TOKEN": "   "}), "from_gh")


# ──────────────────────────── repo resolution ────────────────────────────


class TestRepoResolve(unittest.TestCase):
    def test_flag_wins(self) -> None:
        cfg = {"GITHUB_REPO": "env/repo"}
        with patch.object(gr, "_git_remote_repo", return_value="git/remote"):
            self.assertEqual(gr.resolve_repo("flag/repo", cfg), ("flag", "repo"))

    def test_env_over_git(self) -> None:
        with patch.object(gr, "_git_remote_repo", return_value="git/remote"):
            self.assertEqual(gr.resolve_repo(None, {"GITHUB_REPO": "env/repo"}), ("env", "repo"))

    def test_git_remote_fallback(self) -> None:
        with patch.object(gr, "_git_remote_repo", return_value="git/remote"):
            self.assertEqual(gr.resolve_repo(None, {}), ("git", "remote"))

    def test_all_missing_dies(self) -> None:
        with patch.object(gr, "_git_remote_repo", return_value=None):
            with self.assertRaises(SystemExit):
                gr.resolve_repo(None, {})

    def test_invalid_format_dies(self) -> None:
        with self.assertRaises(SystemExit):
            gr.resolve_repo("noslash", {})

    def test_git_url_parser_ssh(self) -> None:
        # internal regex test — simulate the parse step
        m = gr._REPO_URL_RE.search("git@github.com:foo/bar.git")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "foo")  # type: ignore[union-attr]
        self.assertEqual(m.group(2), "bar")  # type: ignore[union-attr]

    def test_git_url_parser_https(self) -> None:
        m = gr._REPO_URL_RE.search("https://github.com/foo/bar.git")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "foo")  # type: ignore[union-attr]
        self.assertEqual(m.group(2), "bar")  # type: ignore[union-attr]


# ──────────────────────────── name resolution ambiguity ────────────────────────────


class TestPRResolveAmbiguity(unittest.TestCase):
    """Ambiguity must exit non-zero and NEVER auto-pick."""

    def _fake_prs(self):
        return [
            {"number": 1, "title": "fix auth bug", "state": "open",
             "head": {"ref": "fix/auth", "sha": "aaa"}, "base": {"ref": "main"},
             "user": {"login": "a"}, "html_url": "u1"},
            {"number": 2, "title": "fix auth again", "state": "open",
             "head": {"ref": "fix/auth-2", "sha": "bbb"}, "base": {"ref": "main"},
             "user": {"login": "b"}, "html_url": "u2"},
            {"number": 3, "title": "unrelated", "state": "open",
             "head": {"ref": "feat/x", "sha": "ccc"}, "base": {"ref": "main"},
             "user": {"login": "c"}, "html_url": "u3"},
        ]

    def test_exact_branch_unique_returns(self) -> None:
        with patch.object(gr, "_api", return_value=self._fake_prs()):
            result = gr.pr_resolve("o", "r", "fix/auth", {"GITHUB_TOKEN": "t"})
            self.assertEqual(result["number"], 1)

    def test_substring_ambiguous_exits_with_candidates(self) -> None:
        with patch.object(gr, "_api", return_value=self._fake_prs()):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                with self.assertRaises(SystemExit) as ctx:
                    gr.pr_resolve("o", "r", "fix auth", {"GITHUB_TOKEN": "t"})
            self.assertNotEqual(ctx.exception.code, 0)
            emitted = json.loads(buf.getvalue())
            self.assertEqual(emitted["error"], "ambiguous")
            self.assertEqual(len(emitted["matches"]), 2)

    def test_substring_unique_returns(self) -> None:
        with patch.object(gr, "_api", return_value=self._fake_prs()):
            result = gr.pr_resolve("o", "r", "unrelated", {"GITHUB_TOKEN": "t"})
            self.assertEqual(result["number"], 3)

    def test_no_match_exits_not_found(self) -> None:
        with patch.object(gr, "_api", return_value=self._fake_prs()):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                with self.assertRaises(SystemExit):
                    gr.pr_resolve("o", "r", "no-such-thing-anywhere", {"GITHUB_TOKEN": "t"})
            emitted = json.loads(buf.getvalue())
            self.assertEqual(emitted["error"], "not_found")

    def test_empty_query_dies(self) -> None:
        with self.assertRaises(SystemExit):
            gr.pr_resolve("o", "r", "", {"GITHUB_TOKEN": "t"})


class TestIssueResolveAmbiguity(unittest.TestCase):
    def _fake_issues(self):
        return [
            {"number": 10, "title": "flaky test", "state": "open",
             "user": {"login": "a"}, "assignees": [], "labels": [], "html_url": "u"},
            {"number": 11, "title": "flaky test again", "state": "open",
             "user": {"login": "b"}, "assignees": [], "labels": [], "html_url": "u"},
        ]

    def test_ambiguous_exits(self) -> None:
        with patch.object(gr, "_api", return_value=self._fake_issues()):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                with self.assertRaises(SystemExit):
                    gr.issue_resolve("o", "r", "flaky", {"GITHUB_TOKEN": "t"})
            self.assertEqual(json.loads(buf.getvalue())["error"], "ambiguous")

    def test_excludes_prs_from_issue_resolve(self) -> None:
        mixed = [
            {"number": 10, "title": "real issue", "state": "open",
             "user": {"login": "a"}, "assignees": [], "labels": [], "html_url": "u"},
            {"number": 11, "title": "real issue PR", "state": "open", "pull_request": {"url": "x"},
             "user": {"login": "b"}, "assignees": [], "labels": [], "html_url": "u"},
        ]
        with patch.object(gr, "_api", return_value=mixed):
            # substring 'real issue' matches both titles, but PR is filtered out → unique.
            result = gr.issue_resolve("o", "r", "real issue", {"GITHUB_TOKEN": "t"})
            self.assertEqual(result["number"], 10)


# ──────────────────────────── pr comment validation ────────────────────────────


class TestPRComment(unittest.TestCase):
    def test_empty_body_dies(self) -> None:
        with self.assertRaises(SystemExit):
            gr.pr_comment("o", "r", "1", "", {"GITHUB_TOKEN": "t"})

    def test_whitespace_body_dies(self) -> None:
        with self.assertRaises(SystemExit):
            gr.pr_comment("o", "r", "1", "   \n\t  ", {"GITHUB_TOKEN": "t"})


# ──────────────────────────── overview caps ────────────────────────────


class TestOverviewCaps(unittest.TestCase):
    def test_caps_constants(self) -> None:
        self.assertEqual(gr.OVERVIEW_REVIEWS_CAP, 10)
        self.assertEqual(gr.OVERVIEW_FAILING_JOBS_CAP, 20)
        self.assertEqual(gr.OVERVIEW_RUNS_CAP, 5)


# ──────────────────────────── is_int helper ────────────────────────────


class TestIsInt(unittest.TestCase):
    def test_numeric(self) -> None:
        self.assertTrue(gr._is_int("498"))
        self.assertTrue(gr._is_int("0"))

    def test_non_numeric(self) -> None:
        self.assertFalse(gr._is_int("feat/foo"))
        self.assertFalse(gr._is_int(""))
        self.assertFalse(gr._is_int("12a"))


# ──────────────────────────── since-parser (N/A here, but keep place) ────────────────────────────
# github-remote has no --since parser; run logs use --tail on line count.
# Left intentionally absent.


# ──────────────────────────── tool metadata ────────────────────────────


class TestToolMeta(unittest.TestCase):
    def test_injects_tool_field_on_dict(self) -> None:
        wrapped = gr._with_tool_meta({"pr": 1})
        self.assertIn("tool", wrapped)
        self.assertEqual(wrapped["tool"]["name"], "github-remote")
        self.assertIn("version", wrapped["tool"])
        # Tool field is first (dict insertion order) so agents see it up-top.
        self.assertEqual(list(wrapped.keys())[0], "tool")
        self.assertEqual(wrapped["pr"], 1)

    def test_non_dict_passes_through(self) -> None:
        self.assertEqual(gr._with_tool_meta([1, 2, 3]), [1, 2, 3])
        self.assertEqual(gr._with_tool_meta("str"), "str")
        self.assertIsNone(gr._with_tool_meta(None))

    def test_existing_tool_field_preserved(self) -> None:
        payload = {"tool": {"name": "other", "version": "9.9"}, "data": 1}
        wrapped = gr._with_tool_meta(payload)
        self.assertEqual(wrapped["tool"]["name"], "other")

    def test_plugin_version_reads_manifest(self) -> None:
        v = gr._plugin_version()
        self.assertIsInstance(v, str)
        self.assertNotEqual(v, "")


# ──────────────────────────── emit strips sentinel ────────────────────────────


class TestEmitSentinel(unittest.TestCase):
    def test_exit_nonzero_sentinel_stripped(self) -> None:
        obj = {"status": "timeout", "_exit_nonzero": True, "elapsed_s": 100}
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            gr.emit_json(obj)
        parsed = json.loads(buf.getvalue())
        self.assertNotIn("_exit_nonzero", parsed)
        self.assertEqual(parsed["status"], "timeout")


class TestWriteOutputFile(unittest.TestCase):
    """The --output envelope shape is part of the public contract."""

    def _write(self, payload: dict) -> tuple[dict, Path]:
        import tempfile
        td = Path(tempfile.mkdtemp())
        out = td / "nested" / "payload.json"
        summary = gr._write_output_file(payload, str(out))
        return summary, out

    def test_writes_file_and_returns_envelope(self) -> None:
        payload = {"tool": {"name": "x"}, "run_id": 42,
                   "lines": ["a", "b", "c"]}
        summary, path = self._write(payload)
        self.assertTrue(path.exists())
        self.assertEqual(json.loads(path.read_text("utf-8")), payload)
        self.assertEqual(summary["payloadPath"], str(path.resolve()))
        self.assertGreater(summary["bytes"], 0)
        self.assertEqual(set(summary["payloadKeys"]), {"run_id", "lines"})
        self.assertNotIn("tool", summary["payloadKeys"])

    def test_log_payload_gets_head_and_tail_preview(self) -> None:
        payload = {"tool": {}, "lines": [f"line {i}" for i in range(100)]}
        summary, _ = self._write(payload)
        self.assertEqual(summary["preview"]["totalLines"], 100)
        self.assertEqual(summary["preview"]["head"][0], "line 0")
        self.assertEqual(summary["preview"]["tail"][-1], "line 99")

    def test_short_log_payload_omits_tail(self) -> None:
        summary, _ = self._write({"tool": {}, "lines": ["a", "b", "c"]})
        self.assertEqual(summary["preview"]["tail"], [])

    def test_non_log_payload_has_no_preview(self) -> None:
        summary, _ = self._write({"tool": {}, "prs": [{"number": 1}]})
        self.assertNotIn("preview", summary)

    def test_creates_parent_directories(self) -> None:
        _, path = self._write({"tool": {}, "k": "v"})
        self.assertTrue(path.parent.is_dir())

    def test_output_flag_parses(self) -> None:
        parser = gr.build_parser()
        args = parser.parse_args(["--output", "/tmp/x.json",
                                  "overview", "main"])
        self.assertEqual(args.output, "/tmp/x.json")

    def test_list_payload_writes_raw_list_to_disk(self) -> None:
        # `pr list` emits a raw list; --output must not silently drop it.
        payload = [{"number": 1}, {"number": 2}, {"number": 3}]
        summary, path = self._write(payload)
        self.assertTrue(path.exists())
        self.assertEqual(json.loads(path.read_text("utf-8")), payload)
        self.assertEqual(summary["payloadType"], "list")
        self.assertEqual(summary["payloadLength"], 3)
        self.assertNotIn("payloadKeys", summary)
        self.assertNotIn("payloadShape", summary)
        self.assertEqual(summary["preview"]["totalItems"], 3)

    def test_empty_list_payload_has_no_preview(self) -> None:
        summary, _ = self._write([])
        self.assertEqual(summary["payloadLength"], 0)
        self.assertNotIn("preview", summary)

    def test_payload_shape_depth1_shallow(self) -> None:
        import tempfile
        td = Path(tempfile.mkdtemp())
        out = td / "p.json"
        payload = {
            "tool": {},
            "branch": "main",
            "mergeable": True,
            "head": {"sha": "abc"},
            "checks": [{"name": "ci"}],
        }
        summary = gr._write_output_file(payload, str(out), shape_depth=1)
        shape = summary["payloadShape"]
        self.assertEqual(shape["branch"], {"type": "string", "length": 4})
        self.assertEqual(shape["head"], {"type": "dict", "keys": 1})
        self.assertEqual(shape["checks"], {"type": "list", "length": 1})

    def test_payload_shape_default_depth_recurses(self) -> None:
        payload = {
            "tool": {},
            "checks": [
                {"name": "ci", "conclusion": "failure",
                 "annotations": [{"path": "x.ts", "line": 42}]}
            ],
        }
        summary, _ = self._write(payload)  # default depth=3
        checks = summary["payloadShape"]["checks"]
        self.assertEqual(checks["type"], "list")
        self.assertIn("sample", checks)
        # Agent sees checks[0].annotations.length without a second read.
        self.assertEqual(checks["sample"]["shape"]["annotations"],
                         {"type": "list", "length": 1})

    def test_shape_depth_flag_parses(self) -> None:
        parser = gr.build_parser()
        args = parser.parse_args(["--shape-depth", "1", "overview", "main"])
        self.assertEqual(args.shape_depth, 1)
        args = parser.parse_args(["overview", "main"])
        self.assertEqual(args.shape_depth, 3)


# ──────────────────────────── whoami ────────────────────────────


class TestWhoami(unittest.TestCase):
    """The whoami subcommand is the agent-plus refresh handler. Contract:
    exit 0 in both authed and unauthed cases (soft failure), JSON envelope
    with tool.name + tool.version, identity_keys at top level, no secrets."""

    def test_no_token_returns_null_login_no_raise(self) -> None:
        # Both env and `gh auth token` empty — soft failure path.
        with patch.object(gr, "_gh_auth_token", return_value=None):
            result = gr.whoami({})
        self.assertIsNone(result["login"])
        self.assertIsNone(result["default_org"])
        self.assertEqual(result["scopes"], [])
        self.assertIn("error", result)
        self.assertIn("not authenticated", result["error"])

    def test_authed_path_emits_login_and_scopes(self) -> None:
        # Mock urlopen to return a fake /user payload + scope header.
        class FakeResp:
            headers = {"X-OAuth-Scopes": "repo, workflow, read:org"}

            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return json.dumps({"login": "alice", "company": "@acme"}).encode("utf-8")

        with patch.object(gr, "_gh_auth_token", return_value="ghp_fake"), \
             patch("urllib.request.urlopen", return_value=FakeResp()):
            result = gr.whoami({})

        self.assertEqual(result["login"], "alice")
        self.assertEqual(result["default_org"], "acme")
        self.assertEqual(set(result["scopes"]), {"repo", "workflow", "read:org"})

    def test_envelope_shape_via_main(self) -> None:
        # End-to-end: invoke main(['whoami']) and capture stdout.
        with patch.object(gr, "_gh_auth_token", return_value=None):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = gr.main(["whoami"])
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        self.assertEqual(out["tool"]["name"], "github-remote")
        self.assertIn("version", out["tool"])
        self.assertIn("login", out)
        self.assertIn("default_org", out)
        self.assertIn("scopes", out)

    def test_no_secret_leakage(self) -> None:
        # Token reaches the API call but never appears in the output.
        class FakeResp:
            headers = {"X-OAuth-Scopes": "repo"}

            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return json.dumps({"login": "bob"}).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=FakeResp()):
            result = gr.whoami({"GITHUB_TOKEN": "ghp_supersecret_value"})
        blob = json.dumps(result)
        self.assertNotIn("ghp_supersecret_value", blob)
        self.assertNotIn("Bearer", blob)


# ──────────────────────────── ci errors ────────────────────────────


def _make_check_run(
    *, cr_id: int, name: str, conclusion: str, annotations_count: int,
    run_id: int = 999, node_id: str = None,
) -> dict:
    return {
        "id": cr_id,
        "node_id": node_id or f"NODE_{cr_id}",
        "name": name,
        "conclusion": conclusion,
        "html_url": f"https://github.com/o/r/runs/{cr_id}",
        "details_url": f"https://github.com/o/r/actions/runs/{run_id}/job/{cr_id}",
        "output": {"annotations_count": annotations_count, "title": None, "summary": None},
    }


def _rest_annotation(level: str = "failure", path: str = "src/foo.ts",
                     line: int = 42, title: str = "TS2304",
                     message: str = "Cannot find name 'foo'") -> dict:
    return {
        "annotation_level": level,
        "path": path,
        "start_line": line,
        "end_line": line,
        "title": title,
        "message": message,
        "raw_details": None,
    }


class CIBaseMock(unittest.TestCase):
    """Shared `_api` router for ci errors tests. Override `api_responses` per test."""

    def setUp(self) -> None:
        self.api_responses: dict[tuple[str, str], any] = {}
        self.api_calls: list[tuple] = []

    def _fake_api(self, path, *, method="GET", body=None, query=None,
                  cfg=None, timeout=30, raw_text=False, follow_redirects=True):
        self.api_calls.append((method, path, query, body))
        # Try (method, path) then ("GET", path).
        key = (method, path)
        if key in self.api_responses:
            v = self.api_responses[key]
            return v(query, body) if callable(v) else v
        # Fallback default empties to avoid cascading KeyErrors.
        if "/check-runs" in path and not path.endswith("/annotations"):
            return {"check_runs": []}
        if path.endswith("/annotations"):
            return []
        if "/actions/runs/" in path and "/jobs" in path:
            return {"jobs": []}
        if "/actions/runs" in path and method == "GET":
            return {"workflow_runs": []}
        if path == "/graphql":
            return {"data": {}}
        if path.startswith("/repos/") and "/pulls/" in path:
            # R11 fix companion: do NOT default-succeed for unmocked PR
            # paths. After R11 the resolver always tries PR first regardless
            # of integer magnitude, so this fallback was masking the
            # heuristic-dependence assertion in test_resolves_run_id.
            # Tests that need PR success must mock the path explicitly.
            return {}
        return {}

    def _patch_api(self):
        return patch.object(gr, "_api", side_effect=self._fake_api)


class TestCIErrorsResolution(CIBaseMock):
    def test_resolves_branch(self) -> None:
        self.api_responses = {
            ("GET", "/repos/o/r/actions/runs"): {
                "workflow_runs": [{"id": 555, "head_sha": "abc" * 14 + "abcd"}]
            },
            ("GET", "/repos/o/r/commits/" + ("abc" * 14 + "abcd") + "/check-runs"): {
                "check_runs": [],
            },
        }
        with self._patch_api():
            out = gr.ci_errors("o", "r", "main",
                               include_levels={"failure"}, include_logs=False,
                               limit=200, prefer_rest=False, cfg={"GITHUB_TOKEN": "t"})
        self.assertEqual(out["resolved"]["kind"], "branch")
        self.assertEqual(out["resolved"]["value"], "main")
        self.assertEqual(out["resolved"]["run_ids"], [555])

    def test_resolves_run_id(self) -> None:
        sha = "f" * 40
        self.api_responses = {
            ("GET", "/repos/o/r/actions/runs/2000000"): {"id": 2000000, "head_sha": sha},
            ("GET", f"/repos/o/r/commits/{sha}/check-runs"): {"check_runs": []},
        }
        with self._patch_api():
            out = gr.ci_errors("o", "r", "2000000",
                               include_levels={"failure"}, include_logs=False,
                               limit=200, prefer_rest=False, cfg={"GITHUB_TOKEN": "t"})
        self.assertEqual(out["resolved"]["kind"], "run")
        self.assertEqual(out["resolved"]["value"], 2000000)

    def test_resolves_pr_number(self) -> None:
        sha = "a" * 40
        self.api_responses = {
            ("GET", "/repos/o/r/pulls/498"): {"head": {"sha": sha}},
            ("GET", "/repos/o/r/actions/runs"): {"workflow_runs": [{"id": 7}]},
            ("GET", f"/repos/o/r/commits/{sha}/check-runs"): {"check_runs": []},
        }
        with self._patch_api():
            out = gr.ci_errors("o", "r", "498",
                               include_levels={"failure"}, include_logs=False,
                               limit=200, prefer_rest=False, cfg={"GITHUB_TOKEN": "t"})
        self.assertEqual(out["resolved"]["kind"], "pr")
        self.assertEqual(out["resolved"]["value"], 498)
        self.assertEqual(out["resolved"]["run_ids"], [7])

    def test_resolves_large_pr_number_above_old_threshold(self) -> None:
        """R11 fix: PR numbers > 100000 (e.g. microsoft/vscode at >170000)
        must still resolve as PRs. The old < 100000 heuristic produced
        false-negatives. New behavior: try PR first regardless of size."""
        sha = "b" * 40
        large_pr = 175432
        self.api_responses = {
            ("GET", f"/repos/o/r/pulls/{large_pr}"): {"head": {"sha": sha}},
            ("GET", "/repos/o/r/actions/runs"): {"workflow_runs": [{"id": 99}]},
            ("GET", f"/repos/o/r/commits/{sha}/check-runs"): {"check_runs": []},
        }
        with self._patch_api():
            out = gr.ci_errors("o", "r", str(large_pr),
                               include_levels={"failure"}, include_logs=False,
                               limit=200, prefer_rest=False, cfg={"GITHUB_TOKEN": "t"})
        self.assertEqual(out["resolved"]["kind"], "pr",
                         "large PR number must still resolve as PR (was bug)")
        self.assertEqual(out["resolved"]["value"], large_pr)

    def test_resolves_commit_sha(self) -> None:
        sha = "1234567890" * 4  # 40 hex
        self.api_responses = {
            ("GET", "/repos/o/r/actions/runs"): {"workflow_runs": [{"id": 9}]},
            ("GET", f"/repos/o/r/commits/{sha}/check-runs"): {"check_runs": []},
        }
        with self._patch_api():
            out = gr.ci_errors("o", "r", sha,
                               include_levels={"failure"}, include_logs=False,
                               limit=200, prefer_rest=False, cfg={"GITHUB_TOKEN": "t"})
        self.assertEqual(out["resolved"]["kind"], "commit")
        self.assertEqual(out["resolved"]["value"], sha)


class TestCIErrorsPathSelection(CIBaseMock):
    def test_graphql_path_when_low_volume(self) -> None:
        sha = "b" * 40
        cr1 = _make_check_run(cr_id=1, name="lint", conclusion="failure",
                              annotations_count=3, run_id=100)
        graphql_resp = {
            "data": {
                "cr0": {
                    "id": "NODE_1",
                    "annotations": {
                        "nodes": [
                            {"annotationLevel": "FAILURE", "path": "a.ts",
                             "location": {"start": {"line": 1}, "end": {"line": 1}},
                             "title": "T", "message": "M", "rawDetails": None},
                            {"annotationLevel": "FAILURE", "path": "b.ts",
                             "location": {"start": {"line": 2}, "end": {"line": 2}},
                             "title": "T2", "message": "M2", "rawDetails": None},
                        ]
                    },
                }
            }
        }
        self.api_responses = {
            ("GET", "/repos/o/r/actions/runs"): {"workflow_runs": [{"id": 100, "head_sha": sha}]},
            ("GET", f"/repos/o/r/commits/{sha}/check-runs"): {"check_runs": [cr1]},
            ("POST", "/graphql"): graphql_resp,
        }
        with self._patch_api():
            out = gr.ci_errors("o", "r", "main",
                               include_levels={"failure"}, include_logs=False,
                               limit=200, prefer_rest=False, cfg={"GITHUB_TOKEN": "t"})
        self.assertEqual(out["summary"]["source_used"], "graphql")
        self.assertEqual(len(out["annotations"]), 2)
        # GraphQL endpoint hit exactly once.
        gql_calls = [c for c in self.api_calls if c[1] == "/graphql"]
        self.assertEqual(len(gql_calls), 1)

    def test_rest_path_when_high_volume(self) -> None:
        sha = "c" * 40
        # 2 check-runs with 25 annotations each = 50 total → over threshold (40).
        cr1 = _make_check_run(cr_id=1, name="test", conclusion="failure",
                              annotations_count=25, run_id=200)
        cr2 = _make_check_run(cr_id=2, name="build", conclusion="failure",
                              annotations_count=25, run_id=200)
        self.api_responses = {
            ("GET", "/repos/o/r/actions/runs"): {"workflow_runs": [{"id": 200, "head_sha": sha}]},
            ("GET", f"/repos/o/r/commits/{sha}/check-runs"): {"check_runs": [cr1, cr2]},
            ("GET", "/repos/o/r/check-runs/1/annotations"): [_rest_annotation()],
            ("GET", "/repos/o/r/check-runs/2/annotations"): [_rest_annotation(path="x.ts")],
        }
        with self._patch_api():
            out = gr.ci_errors("o", "r", "main",
                               include_levels={"failure"}, include_logs=False,
                               limit=200, prefer_rest=False, cfg={"GITHUB_TOKEN": "t"})
        self.assertEqual(out["summary"]["source_used"], "rest")
        # No GraphQL call.
        self.assertEqual([c for c in self.api_calls if c[1] == "/graphql"], [])

    def test_prefer_rest_forces_rest(self) -> None:
        sha = "d" * 40
        cr1 = _make_check_run(cr_id=1, name="lint", conclusion="failure",
                              annotations_count=2, run_id=300)
        self.api_responses = {
            ("GET", "/repos/o/r/actions/runs"): {"workflow_runs": [{"id": 300, "head_sha": sha}]},
            ("GET", f"/repos/o/r/commits/{sha}/check-runs"): {"check_runs": [cr1]},
            ("GET", "/repos/o/r/check-runs/1/annotations"): [_rest_annotation()],
        }
        with self._patch_api():
            out = gr.ci_errors("o", "r", "main",
                               include_levels={"failure"}, include_logs=False,
                               limit=200, prefer_rest=True, cfg={"GITHUB_TOKEN": "t"})
        self.assertEqual(out["summary"]["source_used"], "rest")
        self.assertEqual([c for c in self.api_calls if c[1] == "/graphql"], [])


class TestCIErrorsFilters(CIBaseMock):
    def _setup_warning_notice(self):
        sha = "e" * 40
        cr1 = _make_check_run(cr_id=1, name="lint", conclusion="failure",
                              annotations_count=3, run_id=400)
        graphql_resp = {
            "data": {
                "cr0": {
                    "id": "NODE_1",
                    "annotations": {
                        "nodes": [
                            {"annotationLevel": "FAILURE", "path": "a.ts",
                             "location": {"start": {"line": 1}, "end": {"line": 1}},
                             "title": "F", "message": "fail", "rawDetails": None},
                            {"annotationLevel": "WARNING", "path": "b.ts",
                             "location": {"start": {"line": 2}, "end": {"line": 2}},
                             "title": "W", "message": "warn", "rawDetails": None},
                            {"annotationLevel": "NOTICE", "path": "c.ts",
                             "location": {"start": {"line": 3}, "end": {"line": 3}},
                             "title": "N", "message": "note", "rawDetails": None},
                        ]
                    },
                }
            }
        }
        self.api_responses = {
            ("GET", "/repos/o/r/actions/runs"): {"workflow_runs": [{"id": 400, "head_sha": sha}]},
            ("GET", f"/repos/o/r/commits/{sha}/check-runs"): {"check_runs": [cr1]},
            ("POST", "/graphql"): graphql_resp,
        }

    def test_default_failure_only(self) -> None:
        self._setup_warning_notice()
        with self._patch_api():
            out = gr.ci_errors("o", "r", "main",
                               include_levels={"failure"}, include_logs=False,
                               limit=200, prefer_rest=False, cfg={"GITHUB_TOKEN": "t"})
        self.assertEqual(len(out["annotations"]), 1)
        self.assertEqual(out["annotations"][0]["level"], "failure")

    def test_include_warning_notice(self) -> None:
        self._setup_warning_notice()
        with self._patch_api():
            out = gr.ci_errors("o", "r", "main",
                               include_levels={"failure", "warning", "notice"},
                               include_logs=False,
                               limit=200, prefer_rest=False, cfg={"GITHUB_TOKEN": "t"})
        levels = sorted(a["level"] for a in out["annotations"])
        self.assertEqual(levels, ["failure", "notice", "warning"])

    def test_limit_caps_count(self) -> None:
        sha = "1" * 40
        cr1 = _make_check_run(cr_id=1, name="lint", conclusion="failure",
                              annotations_count=10, run_id=500)
        nodes = [
            {"annotationLevel": "FAILURE", "path": f"a{i}.ts",
             "location": {"start": {"line": i}, "end": {"line": i}},
             "title": "T", "message": "M", "rawDetails": None}
            for i in range(10)
        ]
        self.api_responses = {
            ("GET", "/repos/o/r/actions/runs"): {"workflow_runs": [{"id": 500, "head_sha": sha}]},
            ("GET", f"/repos/o/r/commits/{sha}/check-runs"): {"check_runs": [cr1]},
            ("POST", "/graphql"): {"data": {"cr0": {"id": "NODE_1",
                                                     "annotations": {"nodes": nodes}}}},
        }
        with self._patch_api():
            out = gr.ci_errors("o", "r", "main",
                               include_levels={"failure"}, include_logs=False,
                               limit=3, prefer_rest=False, cfg={"GITHUB_TOKEN": "t"})
        self.assertEqual(len(out["annotations"]), 3)
        self.assertTrue(out["summary"]["capped"])


class TestCIErrorsLogFallback(CIBaseMock):
    def test_include_logs_for_jobs_with_zero_annotations(self) -> None:
        sha = "2" * 40
        # Failing check-run with ZERO annotations.
        cr1 = _make_check_run(cr_id=10, name="build", conclusion="failure",
                              annotations_count=0, run_id=600)
        self.api_responses = {
            ("GET", "/repos/o/r/actions/runs"): {"workflow_runs": [{"id": 600, "head_sha": sha}]},
            ("GET", f"/repos/o/r/commits/{sha}/check-runs"): {"check_runs": [cr1]},
            ("GET", "/repos/o/r/actions/runs/600/jobs"): {
                "jobs": [{"id": 9001, "name": "build", "conclusion": "failure"}]
            },
        }
        with patch.object(gr, "_fetch_job_logs",
                          return_value="ok line 1\nError: ENOENT no such file\nbye"), \
             self._patch_api():
            out = gr.ci_errors("o", "r", "main",
                               include_levels={"failure"}, include_logs=True,
                               limit=200, prefer_rest=False, cfg={"GITHUB_TOKEN": "t"})
        self.assertEqual(len(out["annotations"]), 0)
        self.assertGreater(len(out["log_fallback"]), 0)
        self.assertEqual(out["log_fallback"][0]["source"], "log")
        self.assertIn("ENOENT", out["log_fallback"][0]["line"])
        self.assertEqual(out["summary"]["jobs_with_only_logs"], 1)

    def test_include_logs_off_by_default(self) -> None:
        sha = "3" * 40
        cr1 = _make_check_run(cr_id=10, name="build", conclusion="failure",
                              annotations_count=0, run_id=700)
        self.api_responses = {
            ("GET", "/repos/o/r/actions/runs"): {"workflow_runs": [{"id": 700, "head_sha": sha}]},
            ("GET", f"/repos/o/r/commits/{sha}/check-runs"): {"check_runs": [cr1]},
        }
        with self._patch_api():
            out = gr.ci_errors("o", "r", "main",
                               include_levels={"failure"}, include_logs=False,
                               limit=200, prefer_rest=False, cfg={"GITHUB_TOKEN": "t"})
        self.assertEqual(out["log_fallback"], [])


class TestCIErrorsEnvelope(CIBaseMock):
    def test_empty_result_returns_valid_envelope(self) -> None:
        sha = "4" * 40
        self.api_responses = {
            ("GET", "/repos/o/r/actions/runs"): {"workflow_runs": [{"id": 1, "head_sha": sha}]},
            ("GET", f"/repos/o/r/commits/{sha}/check-runs"): {"check_runs": []},
        }
        with self._patch_api():
            out = gr.ci_errors("o", "r", "main",
                               include_levels={"failure"}, include_logs=False,
                               limit=200, prefer_rest=False, cfg={"GITHUB_TOKEN": "t"})
        self.assertEqual(out["annotations"], [])
        self.assertEqual(out["log_fallback"], [])
        self.assertEqual(out["summary"]["total_annotations"], 0)
        self.assertEqual(out["summary"]["source_used"], "none")
        self.assertEqual(out["summary"]["by_level"], {"failure": 0, "warning": 0, "notice": 0})

    def test_envelope_top_level_keys(self) -> None:
        sha = "5" * 40
        cr1 = _make_check_run(cr_id=1, name="lint", conclusion="failure",
                              annotations_count=1, run_id=800)
        self.api_responses = {
            ("GET", "/repos/o/r/actions/runs"): {"workflow_runs": [{"id": 800, "head_sha": sha}]},
            ("GET", f"/repos/o/r/commits/{sha}/check-runs"): {"check_runs": [cr1]},
            ("POST", "/graphql"): {"data": {"cr0": {"id": "NODE_1",
                                                     "annotations": {"nodes": [
                {"annotationLevel": "FAILURE", "path": "a.ts",
                 "location": {"start": {"line": 1}, "end": {"line": 1}},
                 "title": "T", "message": "M", "rawDetails": None},
            ]}}}},
        }
        with self._patch_api():
            out = gr.ci_errors("o", "r", "main",
                               include_levels={"failure"}, include_logs=False,
                               limit=200, prefer_rest=False, cfg={"GITHUB_TOKEN": "t"})
        for key in ("ref", "resolved", "summary", "annotations", "log_fallback"):
            self.assertIn(key, out)
        ann = out["annotations"][0]
        for key in ("run_id", "job_id", "job_name", "job_url", "level",
                    "path", "start_line", "end_line", "title", "message",
                    "raw_details", "source"):
            self.assertIn(key, ann)
        self.assertEqual(ann["source"], "annotation")
        # run_id parsed from details_url.
        self.assertEqual(ann["run_id"], 800)


class TestCIErrorsCLI(unittest.TestCase):
    def test_cli_writes_output_envelope(self) -> None:
        import tempfile
        td = Path(tempfile.mkdtemp())
        out_path = td / "ci.json"
        sha = "6" * 40
        cr1 = _make_check_run(cr_id=1, name="lint", conclusion="failure",
                              annotations_count=1, run_id=900)
        api_responses = {
            ("GET", "/repos/o/r/actions/runs"): {"workflow_runs": [{"id": 900, "head_sha": sha}]},
            ("GET", f"/repos/o/r/commits/{sha}/check-runs"): {"check_runs": [cr1]},
            ("POST", "/graphql"): {"data": {"cr0": {"id": "NODE_1",
                                                     "annotations": {"nodes": [
                {"annotationLevel": "FAILURE", "path": "a.ts",
                 "location": {"start": {"line": 1}, "end": {"line": 1}},
                 "title": "T", "message": "M", "rawDetails": None},
            ]}}}},
        }

        def fake_api(path, *, method="GET", body=None, query=None,
                     cfg=None, timeout=30, raw_text=False, follow_redirects=True):
            return api_responses.get((method, path), {})

        with patch.object(gr, "resolve_repo", return_value=("o", "r")), \
             patch.object(gr, "_api", side_effect=fake_api):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = gr.main(["--output", str(out_path), "ci", "errors", "main"])
        self.assertEqual(rc, 0)
        self.assertTrue(out_path.exists())
        envelope = json.loads(buf.getvalue())
        self.assertIn("payloadPath", envelope)
        self.assertIn("payloadKeys", envelope)
        self.assertIn("annotations", envelope["payloadKeys"])
        self.assertIn("summary", envelope["payloadKeys"])
        # Full payload on disk.
        full = json.loads(out_path.read_text("utf-8"))
        self.assertEqual(len(full["annotations"]), 1)

    def test_cli_jsonl_format(self) -> None:
        sha = "7" * 40
        cr1 = _make_check_run(cr_id=1, name="lint", conclusion="failure",
                              annotations_count=2, run_id=1000)
        api_responses = {
            ("GET", "/repos/o/r/actions/runs"): {"workflow_runs": [{"id": 1000, "head_sha": sha}]},
            ("GET", f"/repos/o/r/commits/{sha}/check-runs"): {"check_runs": [cr1]},
            ("POST", "/graphql"): {"data": {"cr0": {"id": "NODE_1",
                                                     "annotations": {"nodes": [
                {"annotationLevel": "FAILURE", "path": "a.ts",
                 "location": {"start": {"line": 1}, "end": {"line": 1}},
                 "title": "T", "message": "M", "rawDetails": None},
                {"annotationLevel": "FAILURE", "path": "b.ts",
                 "location": {"start": {"line": 2}, "end": {"line": 2}},
                 "title": "T2", "message": "M2", "rawDetails": None},
            ]}}}},
        }

        def fake_api(path, *, method="GET", body=None, query=None,
                     cfg=None, timeout=30, raw_text=False, follow_redirects=True):
            return api_responses.get((method, path), {})

        with patch.object(gr, "resolve_repo", return_value=("o", "r")), \
             patch.object(gr, "_api", side_effect=fake_api):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = gr.main(["ci", "errors", "main", "--format", "jsonl"])
        self.assertEqual(rc, 0)
        # Two non-empty JSONL lines, each a parseable annotation.
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)
        for ln in lines:
            parsed = json.loads(ln)
            self.assertIn("path", parsed)
            self.assertEqual(parsed["source"], "annotation")

    def test_cli_auth_failure_returns_error_envelope(self) -> None:
        # gh exits non-zero AND env has no token → require_token dies.
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(gr, "_gh_auth_token", return_value=None), \
             patch.object(gr, "resolve_repo", return_value=("o", "r")):
            err = io.StringIO()
            with patch("sys.stderr", err):
                with self.assertRaises(SystemExit) as ctx:
                    gr.main(["ci", "errors", "main"])
            self.assertNotEqual(ctx.exception.code, 0)
            # Stderr carries the documented missing-config message.
            self.assertIn("GITHUB_TOKEN", err.getvalue())

    def test_cli_invalid_include_dies(self) -> None:
        with patch.object(gr, "resolve_repo", return_value=("o", "r")), \
             patch.object(gr, "_api", return_value={}):
            err = io.StringIO()
            with patch("sys.stderr", err):
                with self.assertRaises(SystemExit):
                    gr.main(["ci", "errors", "main", "--include", "failure,bogus"])
            self.assertIn("bogus", err.getvalue())


if __name__ == "__main__":
    unittest.main()
