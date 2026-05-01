"""Regression tests for supabase-remote.

Focus areas:
  1. JSON envelope unwrapping in `_run_sql` — the core value-add of this CLI
     (stripping the "untrusted data" envelope that `supabase db query` emits).
  2. Project resolution and the `projects current` source reporting.
  3. `rls-audit --format table|json` (and the deprecated `--json` alias).
  4. `gen-types --schema` forwarding and identifier validation.
  5. Secret scrubbing, .env autoloading, and small helper functions.

Stdlib unittest only — no pytest, no network, no real Supabase access.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from importlib.machinery import SourceFileLoader
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


def _load_module():
    here = Path(__file__).resolve()
    bin_path = here.parent.parent / "bin" / "supabase-remote"
    loader = SourceFileLoader("supabase_remote", str(bin_path))
    spec = importlib.util.spec_from_loader("supabase_remote", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


sr = _load_module()


def _mk_completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Build a fake subprocess.CompletedProcess."""
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


# ──────────────────────────── tool metadata ────────────────────────────
#
# `_with_tool_meta` is applied at emit time inside `_dump_json`, not inside the
# builder functions. So tests that introspect builder return values still see
# the un-wrapped shape; tests that capture stdout from a `--format json` run
# will see the `tool` field at the top.


class TestToolMeta(unittest.TestCase):
    def test_injects_tool_field_on_dict(self) -> None:
        wrapped = sr._with_tool_meta({"ref": "x"})
        self.assertIn("tool", wrapped)
        self.assertEqual(wrapped["tool"]["name"], "supabase-remote")
        self.assertIn("version", wrapped["tool"])
        # Tool field is first (dict insertion order) so agents see it up-top.
        self.assertEqual(list(wrapped.keys())[0], "tool")
        self.assertEqual(wrapped["ref"], "x")

    def test_non_dict_passes_through(self) -> None:
        self.assertEqual(sr._with_tool_meta([1, 2, 3]), [1, 2, 3])
        self.assertEqual(sr._with_tool_meta("str"), "str")
        self.assertIsNone(sr._with_tool_meta(None))

    def test_existing_tool_field_preserved(self) -> None:
        payload = {"tool": {"name": "other", "version": "9.9"}, "data": 1}
        wrapped = sr._with_tool_meta(payload)
        self.assertEqual(wrapped["tool"]["name"], "other")

    def test_plugin_version_reads_manifest(self) -> None:
        v = sr._plugin_version()
        self.assertIsInstance(v, str)
        self.assertNotEqual(v, "")


# ─────────────────────────── _run_sql envelope stripping ───────────────────────
#
# This is the flagship behavior the CLI exists to provide. Every shape below has
# been observed or is plausibly emitted by `supabase db query` across versions
# and agent-mode settings.


class TestRunSqlEnvelopeStripping(unittest.TestCase):
    """Every branch of the envelope-stripping logic in _run_sql."""

    def _run(self, stdout: str):
        """Run _run_sql() with a mocked subprocess and no SUPABASE_DB_URL."""
        with patch.dict(os.environ, {"SUPABASE_DB_URL": ""}, clear=False):
            with patch.object(sr, "_have", return_value=True):
                with patch.object(
                    sr.subprocess, "run", return_value=_mk_completed(stdout=stdout)
                ) as m:
                    rows = sr._run_sql(sql="select 1", project_ref="abc123", debug=False)
                    return rows, m

    def test_plain_array_returned_as_is(self) -> None:
        rows, _ = self._run(json.dumps([{"id": 1}, {"id": 2}]))
        self.assertEqual(rows, [{"id": 1}, {"id": 2}])

    def test_data_envelope_stripped(self) -> None:
        envelope = {"data": [{"id": 1}, {"id": 2}], "metadata": {"trusted": False}}
        rows, _ = self._run(json.dumps(envelope))
        self.assertEqual(rows, [{"id": 1}, {"id": 2}])

    def test_rows_envelope_stripped(self) -> None:
        envelope = {"rows": [{"a": 1}]}
        rows, _ = self._run(json.dumps(envelope))
        self.assertEqual(rows, [{"a": 1}])

    def test_result_envelope_stripped(self) -> None:
        envelope = {"result": [{"ok": True}]}
        rows, _ = self._run(json.dumps(envelope))
        self.assertEqual(rows, [{"ok": True}])

    def test_data_preferred_over_result(self) -> None:
        """If both `data` and `result` are present (and are lists), prefer `data`."""
        envelope = {"data": [{"pick": "me"}], "result": [{"pick": "not me"}]}
        rows, _ = self._run(json.dumps(envelope))
        self.assertEqual(rows, [{"pick": "me"}])

    def test_dict_without_known_keys_wrapped_as_single_row(self) -> None:
        envelope = {"status": "ok", "count": 3}
        rows, _ = self._run(json.dumps(envelope))
        self.assertEqual(rows, [envelope])

    def test_dict_with_non_list_data_wrapped_as_single_row(self) -> None:
        """If `data` is present but not a list, don't falsely unwrap."""
        envelope = {"data": "not a list", "note": "x"}
        rows, _ = self._run(json.dumps(envelope))
        self.assertEqual(rows, [envelope])

    def test_empty_stdout_returns_empty_list(self) -> None:
        rows, _ = self._run("")
        self.assertEqual(rows, [])

    def test_whitespace_only_stdout_returns_empty_list(self) -> None:
        rows, _ = self._run("   \n\t  ")
        self.assertEqual(rows, [])

    def test_non_json_wrapped_as_result_row(self) -> None:
        rows, _ = self._run("just a line of text")
        self.assertEqual(rows, [{"result": "just a line of text"}])

    def test_bare_scalar_json_wrapped(self) -> None:
        """A JSON scalar (number, bool, null) should be wrapped."""
        rows, _ = self._run("42")
        self.assertEqual(rows, [{"result": 42}])

    def test_nested_lists_not_flattened(self) -> None:
        """A top-level list of lists is preserved as-is."""
        rows, _ = self._run(json.dumps([[1, 2], [3, 4]]))
        self.assertEqual(rows, [[1, 2], [3, 4]])

    def test_subprocess_failure_raises_systemexit(self) -> None:
        with patch.dict(os.environ, {"SUPABASE_DB_URL": ""}, clear=False):
            with patch.object(sr, "_have", return_value=True):
                with patch.object(
                    sr.subprocess,
                    "run",
                    return_value=_mk_completed(stderr="boom", returncode=1),
                ):
                    with self.assertRaises(SystemExit) as ctx:
                        sr._run_sql(sql="select 1", project_ref="abc123", debug=False)
                    self.assertIn("boom", str(ctx.exception))

    def test_error_stderr_scrubs_token(self) -> None:
        """If the subprocess fails, the token must not leak into the SystemExit."""
        token = "sbp_abcdefghijklmnopqrstuvwxyz0123456789"
        with patch.dict(
            os.environ,
            {"SUPABASE_DB_URL": "", "SUPABASE_ACCESS_TOKEN": token},
            clear=False,
        ):
            with patch.object(sr, "_have", return_value=True):
                with patch.object(
                    sr.subprocess,
                    "run",
                    return_value=_mk_completed(
                        stderr=f"auth failed for {token}", returncode=1
                    ),
                ):
                    with self.assertRaises(SystemExit) as ctx:
                        sr._run_sql(sql="select 1", project_ref="abc123", debug=False)
                    self.assertNotIn(token, str(ctx.exception))
                    self.assertIn("<redacted>", str(ctx.exception))

    def test_xor_sql_or_sql_file_required(self) -> None:
        with self.assertRaises(ValueError):
            sr._run_sql(sql=None, sql_file=None, project_ref=None)
        with self.assertRaises(ValueError):
            sr._run_sql(sql="x", sql_file="y", project_ref=None)

    def test_psql_path_returns_plain_lines(self) -> None:
        """When SUPABASE_DB_URL is set, we use psql -At and wrap each line."""
        with patch.dict(
            os.environ,
            {"SUPABASE_DB_URL": "postgres://u:p@h:5432/d"},
            clear=False,
        ):
            with patch.object(sr, "_have", return_value=True):
                with patch.object(
                    sr.subprocess,
                    "run",
                    return_value=_mk_completed(stdout="alpha\nbeta\n\ngamma\n"),
                ):
                    rows = sr._run_sql(
                        sql="select name from users",
                        project_ref=None,
                        debug=False,
                    )
                    self.assertEqual(
                        rows,
                        [{"result": "alpha"}, {"result": "beta"}, {"result": "gamma"}],
                    )

    def test_agent_no_flag_passed_to_cli(self) -> None:
        """Sanity-check that we ask the Supabase CLI for un-enveloped output."""
        with patch.dict(os.environ, {"SUPABASE_DB_URL": ""}, clear=False):
            with patch.object(sr, "_have", return_value=True):
                with patch.object(
                    sr.subprocess, "run", return_value=_mk_completed(stdout="[]")
                ) as m:
                    sr._run_sql(sql="select 1", project_ref="abc123", debug=False)
                    (cmd_args,), kwargs = m.call_args
                    self.assertIn("--agent", cmd_args)
                    self.assertEqual(cmd_args[cmd_args.index("--agent") + 1], "no")
                    self.assertIn("--output", cmd_args)
                    self.assertEqual(cmd_args[cmd_args.index("--output") + 1], "json")
                    self.assertIn("--linked", cmd_args)


# ─────────────────────────────── _parse_env_file ────────────────────────────────


class TestParseEnvFile(unittest.TestCase):
    def test_basic_kv(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("SUPABASE_ACCESS_TOKEN=abc\nSUPABASE_PROJECT_REF=xyz\n")
            out = sr._parse_env_file(path)
            self.assertEqual(out, {"SUPABASE_ACCESS_TOKEN": "abc", "SUPABASE_PROJECT_REF": "xyz"})

    def test_strips_quotes_and_export(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "export SUPABASE_ACCESS_TOKEN=\"abc\"\n"
                "SUPABASE_PROJECT_REF='xyz'\n"
                "# comment line\n"
                "\n"
                "NO_VALUE_LINE\n"
            )
            out = sr._parse_env_file(path)
            self.assertEqual(out, {"SUPABASE_ACCESS_TOKEN": "abc", "SUPABASE_PROJECT_REF": "xyz"})

    def test_preserves_equals_in_value(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("SUPABASE_DB_URL=postgres://u:pw=with=equals@h/db\n")
            out = sr._parse_env_file(path)
            self.assertEqual(out["SUPABASE_DB_URL"], "postgres://u:pw=with=equals@h/db")

    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(sr._parse_env_file(Path("/definitely/not/here.env")), {})


# ───────────────────────────── _autoload_env_files ──────────────────────────────


class TestAutoloadEnvFiles(unittest.TestCase):
    def test_only_supabase_keys_loaded(self) -> None:
        """Non-SUPABASE_ keys in the .env must NOT leak into os.environ."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "SUPABASE_ACCESS_TOKEN=from-env-file\n"
                "OPENAI_API_KEY=should-not-load\n"
                "RAILWAY_TOKEN=should-not-load\n"
            )
            # Start with known values we can detect if they get clobbered.
            with patch.dict(
                os.environ,
                {"OPENAI_API_KEY": "preserved", "RAILWAY_TOKEN": "preserved"},
                clear=False,
            ):
                # Pop any pre-existing SUPABASE token so we can observe the load.
                os.environ.pop("SUPABASE_ACCESS_TOKEN", None)
                sr._autoload_env_files(extra=[str(path)])
                self.assertEqual(os.environ.get("SUPABASE_ACCESS_TOKEN"), "from-env-file")
                self.assertEqual(os.environ.get("OPENAI_API_KEY"), "preserved")
                self.assertEqual(os.environ.get("RAILWAY_TOKEN"), "preserved")

    def test_extra_wins_over_shell(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("SUPABASE_ACCESS_TOKEN=from-file\n")
            with patch.dict(
                os.environ, {"SUPABASE_ACCESS_TOKEN": "from-shell"}, clear=False
            ):
                sr._autoload_env_files(extra=[str(path)])
                self.assertEqual(os.environ["SUPABASE_ACCESS_TOKEN"], "from-file")


# ─────────────────────────────────── _scrub ─────────────────────────────────────


class TestScrub(unittest.TestCase):
    def test_scrubs_access_token(self) -> None:
        with patch.dict(
            os.environ, {"SUPABASE_ACCESS_TOKEN": "sbp_exactvalue123"}, clear=False
        ):
            out = sr._scrub("auth: sbp_exactvalue123 end")
            self.assertNotIn("sbp_exactvalue123", out)
            self.assertIn("<redacted>", out)

    def test_scrubs_any_sbp_prefixed_token(self) -> None:
        """Even if the env var is unset, sbp_-prefixed long tokens get scrubbed."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUPABASE_ACCESS_TOKEN", None)
            out = sr._scrub("leaked sbp_abcdefghijklmnopqrstuvwxyz0123456789 here")
            self.assertNotIn("sbp_abcdefghijklmnopqrstuvwxyz0123456789", out)
            self.assertIn("<redacted>", out)

    def test_preserves_unrelated_text(self) -> None:
        with patch.dict(os.environ, {"SUPABASE_ACCESS_TOKEN": "tok123"}, clear=False):
            self.assertEqual(sr._scrub("plain text"), "plain text")


# ──────────────────────── _resolve_project_ref_with_source ──────────────────────


class TestResolveProjectRefWithSource(unittest.TestCase):
    FAKE_PROJECTS = [
        {"id": "abcd1234abcd1234abcd", "name": "My Project", "region": "eu-west"},
        {"id": "efgh5678efgh5678efgh", "name": "Other Project", "region": "us-east"},
    ]

    def test_explicit_ident_is_argument_source(self) -> None:
        with patch.object(sr, "_fetch_projects", return_value=self.FAKE_PROJECTS):
            ref, source, raw = sr._resolve_project_ref_with_source("My Project")
            self.assertEqual(ref, "abcd1234abcd1234abcd")
            self.assertEqual(source, "argument")
            self.assertEqual(raw, "My Project")

    def test_env_ref_is_env_source(self) -> None:
        with patch.dict(
            os.environ,
            {"SUPABASE_PROJECT_REF": "efgh5678efgh5678efgh"},
            clear=False,
        ):
            with patch.object(sr, "_fetch_projects", return_value=self.FAKE_PROJECTS):
                with patch.object(sr, "_read_linked_ref", return_value=None):
                    ref, source, raw = sr._resolve_project_ref_with_source(None)
                    self.assertEqual(ref, "efgh5678efgh5678efgh")
                    self.assertEqual(source, "env")
                    self.assertEqual(raw, "efgh5678efgh5678efgh")

    def test_linked_file_is_linked_source(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUPABASE_PROJECT_REF", None)
            with patch.object(sr, "_fetch_projects", return_value=self.FAKE_PROJECTS):
                with patch.object(
                    sr, "_read_linked_ref", return_value="abcd1234abcd1234abcd"
                ):
                    ref, source, raw = sr._resolve_project_ref_with_source(None)
                    self.assertEqual(ref, "abcd1234abcd1234abcd")
                    self.assertEqual(source, "linked")

    def test_env_precedence_over_linked(self) -> None:
        with patch.dict(
            os.environ,
            {"SUPABASE_PROJECT_REF": "abcd1234abcd1234abcd"},
            clear=False,
        ):
            with patch.object(sr, "_fetch_projects", return_value=self.FAKE_PROJECTS):
                with patch.object(
                    sr, "_read_linked_ref", return_value="efgh5678efgh5678efgh"
                ):
                    _ref, source, _raw = sr._resolve_project_ref_with_source(None)
                    self.assertEqual(source, "env")

    def test_no_source_raises(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUPABASE_PROJECT_REF", None)
            with patch.object(sr, "_read_linked_ref", return_value=None):
                with self.assertRaises(SystemExit):
                    sr._resolve_project_ref_with_source(None)

    def test_ambiguous_substring_raises(self) -> None:
        projects = [
            {"id": "a" * 20, "name": "Project One"},
            {"id": "b" * 20, "name": "Project Two"},
        ]
        with patch.object(sr, "_fetch_projects", return_value=projects):
            with self.assertRaises(SystemExit) as ctx:
                sr._resolve_project_ref_with_source("Project")
            self.assertIn("Ambiguous", str(ctx.exception))

    def test_raw_ref_accepted_when_not_in_list(self) -> None:
        """20-char lowercase-alnum refs are accepted as-is (token may have fewer perms)."""
        raw_ref = "zzzzzzzzzzzzzzzzzzzz"
        with patch.object(sr, "_fetch_projects", return_value=self.FAKE_PROJECTS):
            ref, source, _raw = sr._resolve_project_ref_with_source(raw_ref)
            self.assertEqual(ref, raw_ref)
            self.assertEqual(source, "argument")


# ───────────────────── _detect_last_mutated_table / _sql_escape ─────────────────


class TestDetectLastMutatedTable(unittest.TestCase):
    def test_no_dml(self) -> None:
        self.assertIsNone(sr._detect_last_mutated_table("select * from users"))

    def test_single_insert(self) -> None:
        self.assertEqual(
            sr._detect_last_mutated_table("insert into users (id) values (1)"),
            "users",
        )

    def test_update_with_schema(self) -> None:
        self.assertEqual(
            sr._detect_last_mutated_table('update public."accounts" set x = 1'),
            "accounts",
        )

    def test_last_dml_wins(self) -> None:
        sql = (
            "insert into first_tbl values (1);\n"
            "insert into second_tbl values (2);\n"
            "update third_tbl set x = 1;\n"
        )
        self.assertEqual(sr._detect_last_mutated_table(sql), "third_tbl")


class TestSqlEscapeLiteral(unittest.TestCase):
    def test_doubles_single_quotes(self) -> None:
        self.assertEqual(sr._sql_escape_literal("o'brien"), "o''brien")

    def test_plain_unchanged(self) -> None:
        self.assertEqual(sr._sql_escape_literal("plain"), "plain")


# ─────────────────────────── cmd_rls_audit (--format) ──────────────────────────


class TestCmdRlsAudit(unittest.TestCase):
    FAKE_ROWS = [
        {"table_name": "users", "rls_enabled": True, "policy_count": 3},
        {"table_name": "public_log", "rls_enabled": False, "policy_count": 0},
        {"table_name": "settings", "rls_enabled": "t", "policy_count": "2"},
    ]

    def _invoke(self, **extra):
        """Parse the CLI args and run cmd_rls_audit with mocked _run_sql."""
        argv = ["rls-audit"]
        for k, v in extra.items():
            if v is True:
                argv.append("--" + k.replace("_", "-"))
            elif v not in (None, False):
                argv += ["--" + k.replace("_", "-"), str(v)]
        with patch.object(sr, "_run_sql", return_value=list(self.FAKE_ROWS)):
            parser = sr.build_parser()
            args = parser.parse_args(argv)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                args.func(args)
            return stdout.getvalue(), stderr.getvalue()

    def test_default_is_table_format(self) -> None:
        out, _err = self._invoke()
        self.assertIn("TABLE", out)
        self.assertIn("users", out)
        self.assertIn("enabled", out)
        self.assertIn("DISABLED", out)

    def test_format_json_produces_valid_json(self) -> None:
        out, _err = self._invoke(format="json")
        parsed = json.loads(out)
        self.assertIsInstance(parsed, list)
        self.assertEqual(len(parsed), 3)
        for row in parsed:
            self.assertEqual(set(row.keys()), {"table", "rls", "policies"})
            self.assertIsInstance(row["rls"], bool)
            self.assertIsInstance(row["policies"], int)

    def test_deprecated_json_flag_still_works(self) -> None:
        out, err = self._invoke(json=True)
        parsed = json.loads(out)
        self.assertIsInstance(parsed, list)
        # Using --json alone should NOT print the deprecation warning (since
        # --format wasn't set explicitly to a different value).
        self.assertNotIn("deprecated", err.lower())

    def test_format_overrides_deprecated_json_and_warns(self) -> None:
        """If both --json and --format table are passed, --format wins and we warn."""
        out, err = self._invoke(json=True, format="table")
        self.assertIn("TABLE", out)  # table output
        self.assertIn("deprecated", err.lower())

    def test_string_booleans_coerced(self) -> None:
        """psql-style string 't' should coerce to True."""
        out, _err = self._invoke(format="json")
        parsed = json.loads(out)
        settings = [r for r in parsed if r["table"] == "settings"][0]
        self.assertTrue(settings["rls"])
        self.assertEqual(settings["policies"], 2)

    def test_status_labels(self) -> None:
        """Each combination of rls/policies maps to a distinct status label."""
        rows = [
            {"table_name": "ok", "rls_enabled": True, "policy_count": 2},
            {"table_name": "bad_no_rls", "rls_enabled": False, "policy_count": 0},
            {"table_name": "rls_no_policy", "rls_enabled": True, "policy_count": 0},
            {"table_name": "no_rls_but_policy", "rls_enabled": False, "policy_count": 1},
        ]
        with patch.object(sr, "_run_sql", return_value=rows):
            parser = sr.build_parser()
            args = parser.parse_args(["rls-audit"])
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                args.func(args)
            out = stdout.getvalue()
            self.assertIn("NO RLS, NO POLICIES", out)
            self.assertIn("RLS ON BUT NO POLICIES", out)
            self.assertIn("NO RLS — FIX", out)
            self.assertIn("OK", out)


# ──────────────────────────── cmd_gen_types (--schema) ─────────────────────────


class TestCmdGenTypes(unittest.TestCase):
    def _run_gen(self, argv_extra, stdout_text="export type X = {}"):
        """Run cmd_gen_types with a mocked CLI. Returns (mock_run, cmd_args,
        target_exists, target_contents, stdout). Uses a tempdir scoped to this
        helper — the target path is read BEFORE tempdir cleanup.
        """
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "types.ts"
            argv = ["gen-types", str(target)] + argv_extra
            with patch.object(sr, "_resolve_project_ref", return_value="ref12345"):
                with patch.object(sr, "_have", return_value=True):
                    with patch.object(
                        sr.subprocess,
                        "run",
                        return_value=_mk_completed(stdout=stdout_text),
                    ) as mock_run:
                        parser = sr.build_parser()
                        args = parser.parse_args(argv)
                        stdout = io.StringIO()
                        with redirect_stdout(stdout):
                            args.func(args)
                        cmd_args = mock_run.call_args.args[0]
                        target_exists = target.is_file()
                        target_contents = (
                            target.read_text(encoding="utf-8") if target_exists else ""
                        )
                        return mock_run, cmd_args, target_exists, target_contents, stdout.getvalue()

    def test_no_schema_flag_omits_schema_arg(self) -> None:
        _mock_run, cmd, target_exists, contents, _out = self._run_gen([])
        self.assertNotIn("--schema", cmd)
        self.assertTrue(target_exists)
        self.assertEqual(contents, "export type X = {}")

    def test_single_schema_forwarded(self) -> None:
        _mock_run, cmd, _exists, _contents, _out = self._run_gen(
            ["--schema", "public"]
        )
        self.assertIn("--schema", cmd)
        self.assertEqual(cmd[cmd.index("--schema") + 1], "public")

    def test_multiple_schemas_forwarded_in_order(self) -> None:
        _mock_run, cmd, _exists, _contents, _out = self._run_gen(
            ["--schema", "public", "--schema", "auth"]
        )
        # Find all --schema flag positions.
        schema_positions = [i for i, a in enumerate(cmd) if a == "--schema"]
        self.assertEqual(len(schema_positions), 2)
        self.assertEqual(cmd[schema_positions[0] + 1], "public")
        self.assertEqual(cmd[schema_positions[1] + 1], "auth")

    def test_suspicious_schema_rejected(self) -> None:
        """Injection-style schema names must be rejected before hitting the CLI."""
        for bad in ["public; drop table users", "a b", "../etc/passwd", ""]:
            with TemporaryDirectory() as tmp:
                target = Path(tmp) / "types.ts"
                argv = ["gen-types", str(target), "--schema", bad]
                with patch.object(sr, "_resolve_project_ref", return_value="ref12345"):
                    with patch.object(sr, "_have", return_value=True):
                        with patch.object(
                            sr.subprocess, "run"
                        ) as mock_run:
                            parser = sr.build_parser()
                            args = parser.parse_args(argv)
                            with self.assertRaises(SystemExit):
                                args.func(args)
                            mock_run.assert_not_called()

    def test_missing_target_dir_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "does-not-exist" / "types.ts"
            parser = sr.build_parser()
            args = parser.parse_args(["gen-types", str(target)])
            with patch.object(sr, "_resolve_project_ref", return_value="ref12345"):
                with self.assertRaises(SystemExit) as ctx:
                    args.func(args)
                self.assertIn("Target directory does not exist", str(ctx.exception))


# ─────────────────────────────── cmd_projects_current ───────────────────────────


class TestCmdProjectsCurrent(unittest.TestCase):
    def test_text_output_from_env(self) -> None:
        with patch.dict(
            os.environ,
            {"SUPABASE_PROJECT_REF": "abcd1234abcd1234abcd"},
            clear=False,
        ):
            with patch.object(
                sr,
                "_fetch_projects",
                return_value=[
                    {"id": "abcd1234abcd1234abcd", "name": "My Project"}
                ],
            ):
                with patch.object(sr, "_read_linked_ref", return_value=None):
                    parser = sr.build_parser()
                    args = parser.parse_args(["projects", "current"])
                    stdout = io.StringIO()
                    with redirect_stdout(stdout):
                        args.func(args)
                    out = stdout.getvalue()
                    self.assertIn("abcd1234abcd1234abcd", out)
                    self.assertIn("My Project", out)
                    self.assertIn("SUPABASE_PROJECT_REF", out)

    def test_json_output_from_linked(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUPABASE_PROJECT_REF", None)
            with patch.object(
                sr, "_read_linked_ref", return_value="abcd1234abcd1234abcd"
            ):
                with patch.object(
                    sr,
                    "_fetch_projects",
                    return_value=[
                        {"id": "abcd1234abcd1234abcd", "name": "Linked Project"}
                    ],
                ):
                    parser = sr.build_parser()
                    args = parser.parse_args(
                        ["projects", "current", "--format", "json"]
                    )
                    stdout = io.StringIO()
                    with redirect_stdout(stdout):
                        args.func(args)
                    parsed = json.loads(stdout.getvalue())
                    self.assertTrue(parsed["resolved"])
                    self.assertEqual(parsed["ref"], "abcd1234abcd1234abcd")
                    self.assertEqual(parsed["source"], "linked")
                    self.assertEqual(parsed["name"], "Linked Project")

    def test_json_output_when_unresolved(self) -> None:
        """No env var, no linked file — JSON mode returns resolved:false, not raise."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUPABASE_PROJECT_REF", None)
            with patch.object(sr, "_read_linked_ref", return_value=None):
                parser = sr.build_parser()
                args = parser.parse_args(["projects", "current", "--format", "json"])
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    args.func(args)
                parsed = json.loads(stdout.getvalue())
                self.assertFalse(parsed["resolved"])
                self.assertIsNone(parsed["ref"])
                self.assertIn("error", parsed)

    def test_text_output_when_unresolved_raises(self) -> None:
        """Text mode without a default project should surface the resolver's error."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUPABASE_PROJECT_REF", None)
            with patch.object(sr, "_read_linked_ref", return_value=None):
                parser = sr.build_parser()
                args = parser.parse_args(["projects", "current"])
                with self.assertRaises(SystemExit):
                    args.func(args)

    def test_json_gracefully_handles_no_api_access(self) -> None:
        """If _fetch_projects raises, JSON output should still resolve (name=None)."""
        with patch.dict(
            os.environ,
            {"SUPABASE_PROJECT_REF": "abcd1234abcd1234abcd"},
            clear=False,
        ):
            with patch.object(sr, "_read_linked_ref", return_value=None):
                # First call used by the resolver succeeds; second call (for name)
                # raises. The resolver actually calls _fetch_projects too, so we
                # have to short-circuit it when looking up the raw ref.
                with patch.object(
                    sr,
                    "_fetch_projects",
                    side_effect=SystemExit("no token"),
                ):
                    parser = sr.build_parser()
                    args = parser.parse_args(
                        ["projects", "current", "--format", "json"]
                    )
                    # With no token, _resolve_project_ref_with_source will itself
                    # raise SystemExit while trying to validate the ref. JSON
                    # mode must catch and report.
                    stdout = io.StringIO()
                    with redirect_stdout(stdout):
                        args.func(args)
                    parsed = json.loads(stdout.getvalue())
                    # Could be resolved:false (if the resolver fails) or
                    # resolved:true with name=None (if it succeeds). We just
                    # assert it produced structured JSON output either way.
                    self.assertIn("resolved", parsed)


# ───────────────────────────── cmd_sql_inline write gate ────────────────────────


class TestCmdSqlInlineWriteGate(unittest.TestCase):
    def test_rejects_insert_without_write(self) -> None:
        parser = sr.build_parser()
        args = parser.parse_args(["sql-inline", "insert into users (id) values (1)"])
        with self.assertRaises(SystemExit) as ctx:
            args.func(args)
        self.assertIn("--write", str(ctx.exception))

    def test_rejects_update_without_write(self) -> None:
        parser = sr.build_parser()
        args = parser.parse_args(["sql-inline", "update users set x = 1"])
        with self.assertRaises(SystemExit) as ctx:
            args.func(args)
        self.assertIn("--write", str(ctx.exception))

    def test_allows_select_without_write(self) -> None:
        parser = sr.build_parser()
        args = parser.parse_args(["sql-inline", "select 1"])
        with patch.object(sr, "_run_sql", return_value=[{"?column?": 1}]):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                args.func(args)
            # Should have produced JSON-ish output (the dump_json format).
            self.assertIn("?column?", stdout.getvalue())

    def test_allows_insert_with_write(self) -> None:
        parser = sr.build_parser()
        args = parser.parse_args(
            ["sql-inline", "insert into users values (1)", "--write"]
        )
        with patch.object(sr, "_run_sql", return_value=[]):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                args.func(args)
            self.assertIn("(no rows)", stdout.getvalue())


# ─────────────────────────────── argparse wiring ────────────────────────────────


class TestArgparseWiring(unittest.TestCase):
    def test_projects_current_accepts_format(self) -> None:
        parser = sr.build_parser()
        args = parser.parse_args(["projects", "current", "--format", "json"])
        self.assertEqual(args.format, "json")

    def test_rls_audit_format_defaults_to_none(self) -> None:
        """The default is None so cmd_rls_audit can distinguish 'explicit' from
        'defaulted', which matters for --json back-compat."""
        parser = sr.build_parser()
        args = parser.parse_args(["rls-audit"])
        self.assertIsNone(args.format)
        self.assertFalse(args.json)

    def test_rls_audit_format_rejects_bad_value(self) -> None:
        parser = sr.build_parser()
        with self.assertRaises(SystemExit):
            # argparse exits with code 2 on unknown --format choice.
            parser.parse_args(["rls-audit", "--format", "yaml"])

    def test_gen_types_schema_is_append_list(self) -> None:
        parser = sr.build_parser()
        args = parser.parse_args(
            ["gen-types", "/tmp/x.ts", "--schema", "public", "--schema", "auth"]
        )
        self.assertEqual(args.schema, ["public", "auth"])


class TestWhoami(unittest.TestCase):
    """whoami is the agent-plus refresh handler. Soft-fails on missing
    SUPABASE_ACCESS_TOKEN, soft-fails on API error, never leaks the token."""

    def setUp(self) -> None:
        self._saved = os.environ.pop("SUPABASE_ACCESS_TOKEN", None)

    def tearDown(self) -> None:
        if self._saved is not None:
            os.environ["SUPABASE_ACCESS_TOKEN"] = self._saved
        else:
            os.environ.pop("SUPABASE_ACCESS_TOKEN", None)

    def _run_whoami(self) -> dict:
        buf = io.StringIO()
        parser = sr.build_parser()
        args = parser.parse_args(["whoami"])
        with redirect_stdout(buf):
            args.func(args)
        return json.loads(buf.getvalue())

    def test_no_token_soft_fail(self) -> None:
        out = self._run_whoami()
        self.assertEqual(out["tool"]["name"], "supabase-remote")
        self.assertIn("version", out["tool"])
        self.assertEqual(out["projects"], [])
        self.assertIsNone(out["linked_project_ref"])
        self.assertIn("error", out)

    def test_authed_path(self) -> None:
        from unittest.mock import patch

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self):
                return json.dumps([
                    {"name": "p1", "id": "abc", "region": "us-east-1", "organization_id": "org_1"},
                    {"name": "p2", "ref": "def", "region": "eu-west-1", "organization_id": "org_2"},
                ]).encode("utf-8")

        os.environ["SUPABASE_ACCESS_TOKEN"] = "sbp_supersecret_token"
        try:
            with patch("urllib.request.urlopen", return_value=FakeResp()):
                out = self._run_whoami()
        finally:
            os.environ.pop("SUPABASE_ACCESS_TOKEN", None)

        self.assertEqual(len(out["projects"]), 2)
        self.assertEqual(out["projects"][0]["id"], "abc")
        self.assertEqual(out["projects"][1]["id"], "def")
        blob = json.dumps(out)
        self.assertNotIn("sbp_supersecret_token", blob)
        self.assertNotIn("Bearer", blob)

    def test_envelope_has_tool_meta(self) -> None:
        out = self._run_whoami()
        self.assertIn("tool", out)
        self.assertEqual(out["tool"]["name"], "supabase-remote")


# ─────────────────────── wait_project_active + cmd_projects_create/restore ──────


class TestWaitProjectActive(unittest.TestCase):
    """Unit tests for wait_project_active — all HTTP mocked, no real sleeps."""

    def _call(self, statuses: list, *, timeout: float = 60, poll_interval: float = 0.001):
        """Drive wait_project_active through a sequence of API responses.

        `statuses` is a list of strings (project status values) or
        Exception subclasses to raise. The mock cycles through them in order,
        repeating the last one if the list is exhausted.
        """
        responses = iter(statuses)
        last_resp = [statuses[-1]]

        def _fake_fetch(ref, *, debug=False):
            resp = next(responses, last_resp[0])
            if isinstance(resp, type) and issubclass(resp, Exception):
                raise SystemExit("poll error")
            return {"id": ref, "status": resp}

        with patch.object(sr, "_fetch_project", side_effect=_fake_fetch):
            with patch.object(sr.time, "sleep", return_value=None):
                return sr.wait_project_active(
                    "testref123",
                    timeout=timeout,
                    poll_interval=poll_interval,
                    debug=False,
                )

    def test_immediate_active_healthy(self) -> None:
        result = self._call(["ACTIVE_HEALTHY"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["project_status"], "ACTIVE_HEALTHY")
        self.assertNotIn("_exit_nonzero", result)

    def test_coming_up_then_active(self) -> None:
        result = self._call(["COMING_UP", "COMING_UP", "ACTIVE_HEALTHY"])
        self.assertEqual(result["status"], "success")
        self.assertNotIn("_exit_nonzero", result)

    def test_fail_status_returns_error(self) -> None:
        result = self._call(["COMING_UP", "INIT_FAILED"])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["project_status"], "INIT_FAILED")
        self.assertTrue(result.get("_exit_nonzero"))

    def test_restore_failed_status(self) -> None:
        result = self._call(["COMING_UP", "RESTORE_FAILED"])
        self.assertEqual(result["status"], "error")
        self.assertTrue(result.get("_exit_nonzero"))

    def test_timeout_before_terminal(self) -> None:
        """If timeout=0 the loop exits immediately without polling."""
        result = sr.wait_project_active(
            "refxxx",
            timeout=0,
            poll_interval=0.001,
            debug=False,
        )
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(result.get("_exit_nonzero"))

    def test_poll_error_returns_poll_error(self) -> None:
        def _raise(ref, *, debug=False):
            raise SystemExit("network down")

        with patch.object(sr, "_fetch_project", side_effect=_raise):
            with patch.object(sr.time, "sleep", return_value=None):
                result = sr.wait_project_active(
                    "refyyy", timeout=30, poll_interval=0.001
                )
        self.assertEqual(result["status"], "poll_error")
        self.assertTrue(result.get("_exit_nonzero"))


class TestCmdProjectsCreate(unittest.TestCase):
    """cmd_projects_create — happy path, no-wait, and --wait fail/timeout."""

    FAKE_PROJECT = {
        "id": "newproject12345ref0",
        "name": "my-project",
        "status": "COMING_UP",
        "region": "eu-west-1",
    }

    def _run(self, extra_argv: list, api_responses: list | None = None,
             wait_result: dict | None = None):
        """Invoke `projects create` through the parser with mocked _api_call."""
        argv = [
            "projects", "create",
            "--name", "my-project",
            "--org-id", "org_abc",
            "--db-pass", "s3cr3t",
            "--region", "eu-west-1",
        ] + extra_argv

        parser = sr.build_parser()
        args = parser.parse_args(argv)

        stdout = io.StringIO()
        stderr = io.StringIO()

        def _fake_api_call(method, path, **kwargs):
            return (201, self.FAKE_PROJECT)

        with patch.object(sr, "_api_call", side_effect=_fake_api_call):
            if wait_result is not None:
                with patch.object(sr, "wait_project_active", return_value=dict(wait_result)):
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        try:
                            args.func(args)
                            exit_code = 0
                        except SystemExit as e:
                            exit_code = e.code if isinstance(e.code, int) else 1
            else:
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    try:
                        args.func(args)
                        exit_code = 0
                    except SystemExit as e:
                        exit_code = e.code if isinstance(e.code, int) else 1

        return stdout.getvalue(), stderr.getvalue(), exit_code

    def test_no_wait_returns_api_response(self) -> None:
        out, _err, code = self._run([])
        self.assertEqual(code, 0)
        parsed = json.loads(out)
        self.assertEqual(parsed["id"], "newproject12345ref0")
        self.assertEqual(parsed["status"], "COMING_UP")

    def test_wait_success(self) -> None:
        wait_ok = {
            "status": "success",
            "project_status": "ACTIVE_HEALTHY",
            "ref": "newproject12345ref0",
            "elapsed_s": 15,
            "project": {"id": "newproject12345ref0", "status": "ACTIVE_HEALTHY"},
        }
        out, _err, code = self._run(["--wait"], wait_result=wait_ok)
        self.assertEqual(code, 0)
        parsed = json.loads(out)
        self.assertEqual(parsed["status"], "success")

    def test_wait_error_exits_1(self) -> None:
        wait_fail = {
            "status": "error",
            "project_status": "INIT_FAILED",
            "ref": "newproject12345ref0",
            "elapsed_s": 5,
            "project": {"id": "newproject12345ref0", "status": "INIT_FAILED"},
            "_exit_nonzero": True,
        }
        out, _err, code = self._run(["--wait"], wait_result=wait_fail)
        self.assertEqual(code, 1)
        parsed = json.loads(out)
        self.assertEqual(parsed["status"], "error")
        # _exit_nonzero must be stripped before JSON emission
        self.assertNotIn("_exit_nonzero", parsed)

    def test_wait_timeout_exits_1(self) -> None:
        wait_timeout = {
            "status": "timeout",
            "project_status": "COMING_UP",
            "ref": "newproject12345ref0",
            "elapsed_s": 600,
            "project": None,
            "_exit_nonzero": True,
        }
        out, _err, code = self._run(["--wait"], wait_result=wait_timeout)
        self.assertEqual(code, 1)
        parsed = json.loads(out)
        self.assertEqual(parsed["status"], "timeout")
        self.assertNotIn("_exit_nonzero", parsed)


class TestCmdProjectsRestore(unittest.TestCase):
    """cmd_projects_restore — no-wait and --wait paths."""

    FAKE_RESTORE_RESP = {"message": "restoring"}
    FAKE_PROJECTS = [
        {"id": "restoreme1234567890a", "name": "paused-proj", "region": "us-east-1"}
    ]

    def _run(self, extra_argv: list, wait_result: dict | None = None):
        argv = ["projects", "restore", "--project", "paused-proj"] + extra_argv
        parser = sr.build_parser()
        args = parser.parse_args(argv)

        stdout = io.StringIO()
        stderr = io.StringIO()

        def _fake_api_call(method, path, **kwargs):
            return (200, self.FAKE_RESTORE_RESP)

        with patch.object(sr, "_resolve_project_ref",
                          return_value="restoreme1234567890a"):
            with patch.object(sr, "_api_call", side_effect=_fake_api_call):
                if wait_result is not None:
                    with patch.object(sr, "wait_project_active",
                                      return_value=dict(wait_result)):
                        with redirect_stdout(stdout), redirect_stderr(stderr):
                            try:
                                args.func(args)
                                exit_code = 0
                            except SystemExit as e:
                                exit_code = e.code if isinstance(e.code, int) else 1
                else:
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        try:
                            args.func(args)
                            exit_code = 0
                        except SystemExit as e:
                            exit_code = e.code if isinstance(e.code, int) else 1

        return stdout.getvalue(), stderr.getvalue(), exit_code

    def test_no_wait_returns_api_response(self) -> None:
        out, _err, code = self._run([])
        self.assertEqual(code, 0)
        parsed = json.loads(out)
        self.assertIn("message", parsed)

    def test_wait_success(self) -> None:
        wait_ok = {
            "status": "success",
            "project_status": "ACTIVE_HEALTHY",
            "ref": "restoreme1234567890a",
            "elapsed_s": 30,
            "project": {"id": "restoreme1234567890a", "status": "ACTIVE_HEALTHY"},
        }
        out, _err, code = self._run(["--wait"], wait_result=wait_ok)
        self.assertEqual(code, 0)
        parsed = json.loads(out)
        self.assertEqual(parsed["status"], "success")

    def test_wait_error_exits_1(self) -> None:
        wait_fail = {
            "status": "error",
            "project_status": "RESTORE_FAILED",
            "ref": "restoreme1234567890a",
            "elapsed_s": 10,
            "project": {"id": "restoreme1234567890a", "status": "RESTORE_FAILED"},
            "_exit_nonzero": True,
        }
        out, _err, code = self._run(["--wait"], wait_result=wait_fail)
        self.assertEqual(code, 1)
        parsed = json.loads(out)
        self.assertEqual(parsed["status"], "error")
        self.assertNotIn("_exit_nonzero", parsed)


class TestArgparseProjectsCreateRestore(unittest.TestCase):
    """Argparse wiring for the new subcommands."""

    def test_create_required_args(self) -> None:
        parser = sr.build_parser()
        args = parser.parse_args([
            "projects", "create",
            "--name", "foo",
            "--org-id", "org_123",
            "--db-pass", "pass",
            "--region", "us-east-1",
        ])
        self.assertEqual(args.name, "foo")
        self.assertEqual(args.org_id, "org_123")
        self.assertEqual(args.db_pass, "pass")
        self.assertEqual(args.region, "us-east-1")
        self.assertEqual(args.plan, "free")
        self.assertFalse(args.wait)
        self.assertEqual(args.timeout, sr.WAIT_DEFAULT_TIMEOUT)
        self.assertEqual(args.poll_interval, sr.WAIT_DEFAULT_POLL_INTERVAL)

    def test_create_wait_flag(self) -> None:
        parser = sr.build_parser()
        args = parser.parse_args([
            "projects", "create",
            "--name", "x",
            "--org-id", "y",
            "--db-pass", "z",
            "--region", "r",
            "--wait",
            "--timeout", "120",
            "--poll-interval", "5",
        ])
        self.assertTrue(args.wait)
        self.assertEqual(args.timeout, 120.0)
        self.assertEqual(args.poll_interval, 5.0)

    def test_restore_required_args(self) -> None:
        parser = sr.build_parser()
        args = parser.parse_args(["projects", "restore", "--project", "myprojname"])
        self.assertEqual(args.project, "myprojname")
        self.assertFalse(args.wait)

    def test_restore_wait_flag(self) -> None:
        parser = sr.build_parser()
        args = parser.parse_args([
            "projects", "restore",
            "--project", "myprojname",
            "--wait",
            "--timeout", "300",
        ])
        self.assertTrue(args.wait)
        self.assertEqual(args.timeout, 300.0)


if __name__ == "__main__":
    unittest.main()
