#!/usr/bin/env python3
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
from pathlib import Path

from aiq_lean_tools import lake_report as MODULE


class ParseTest(unittest.TestCase):
    def test_lake_forwarded_diagnostic(self) -> None:
        diagnostic = MODULE.parse_diagnostic_header(
            "error: DavisKahan/Test.lean:60:17: Invalid field `foo`"
        )
        self.assertIsNotNone(diagnostic)
        assert diagnostic is not None
        self.assertEqual(diagnostic.severity, "error")
        self.assertEqual(diagnostic.file, "DavisKahan/Test.lean")
        self.assertEqual(diagnostic.line, 60)
        self.assertEqual(diagnostic.column, 17)
        self.assertEqual(diagnostic.message, "Invalid field `foo`")

    def test_direct_lean_diagnostic(self) -> None:
        diagnostic = MODULE.parse_diagnostic_header(
            "DavisKahan/Test.lean:9:4: warning: declaration uses 'sorry'"
        )
        self.assertIsNotNone(diagnostic)
        assert diagnostic is not None
        self.assertEqual(diagnostic.severity, "warning")
        self.assertEqual(diagnostic.file, "DavisKahan/Test.lean")
        self.assertEqual(diagnostic.line, 9)
        self.assertEqual(diagnostic.column, 4)

    def test_wrapper_noise_is_not_a_diagnostic(self) -> None:
        result = MODULE.parse_output(
            [
                "error: DavisKahan/Test.lean:3:2: bad term",
                "context line",
                "error: Lean exited with code 1",
                "Some required targets logged failures:",
                "- DavisKahan.Test",
                "error: build failed",
            ]
        )
        self.assertEqual(len(result.diagnostics), 1)
        self.assertEqual(result.diagnostics[0].body, ["context line"])
        self.assertEqual(len(result.synthetic_lines), 4)

    def test_parse_lake_progress_counter(self) -> None:
        self.assertEqual(MODULE.parse_lake_progress("[9117/9173] Building Foo"), (9117, 9173))
        self.assertEqual(MODULE.parse_lake_progress("9117/9173 Building Foo"), (9117, 9173))
        self.assertEqual(MODULE.parse_lake_progress("⏳ [12/30] Foo"), (12, 30))
        self.assertIsNone(MODULE.parse_lake_progress("31/30 impossible"))

    def test_noninteractive_progress_is_throttled(self) -> None:
        stream = io.StringIO()
        display = MODULE.LakeProgressDisplay(
            enabled=True,
            target_text="DavisKahan.All",
            stream=stream,
            color_enabled=False,
            interactive=False,
        )
        for done in range(101):
            display.update(done, 100)
        lines = stream.getvalue().splitlines()
        self.assertLessEqual(len(lines), 22)
        self.assertIn("LAKE 0/100 DavisKahan.All", lines[0])
        self.assertIn("LAKE 100/100 DavisKahan.All", lines[-1])

    def test_exact_deduplication(self) -> None:
        result = MODULE.parse_output(
            [
                "error: DavisKahan/Test.lean:3:2: bad term",
                "same body",
                "error: DavisKahan/Test.lean:3:2: bad term",
                "same body",
            ]
        )
        unique = MODULE.deduplicate(result.diagnostics)
        self.assertEqual(len(unique), 1)
        self.assertEqual(unique[0].repeats, 2)

    def test_one_based_tab_aware_column(self) -> None:
        self.assertEqual(MODULE.display_column("\tfoo", 2, 4), 4)
        self.assertEqual(MODULE.display_column("abcd", 1, 2), 0)
        self.assertEqual(MODULE.display_column("abcd", 4, 2), 3)

    def test_windows_style_path(self) -> None:
        diagnostic = MODULE.parse_diagnostic_header(
            r"error: C:\\repo\\DavisKahan\\Test.lean:12:7: bad term"
        )
        self.assertIsNotNone(diagnostic)
        assert diagnostic is not None
        self.assertEqual(diagnostic.line, 12)
        self.assertEqual(diagnostic.column, 7)
        self.assertTrue(diagnostic.file.endswith("Test.lean"))

    def test_deduplication_merges_origins(self) -> None:
        first = MODULE.Diagnostic(
            severity="error", message="same", file="Test.lean", line=1, column=1,
            origins=["Target.One"],
        )
        second = MODULE.Diagnostic(
            severity="error", message="same", file="Test.lean", line=1, column=1,
            origins=["Target.Two"],
        )
        unique = MODULE.deduplicate([first, second])
        self.assertEqual(len(unique), 1)
        self.assertEqual(unique[0].repeats, 2)
        self.assertEqual(unique[0].origins, ["Target.One", "Target.Two"])

    def test_multi_target_sequential_progress_and_global_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "lakefile.toml").write_text("name = \"test\"\n")

            def fake_run(command, actual_root, progress_callback=None):
                self.assertEqual(actual_root, root)
                target = command[-1]
                if progress_callback is not None:
                    progress_callback(1, 2)
                    progress_callback(2, 2)
                lines = [
                    "error: Test.lean:1:1: shared failure",
                    "same body",
                    "error: Lean exited with code 1",
                    "error: build failed",
                ]
                self.assertIn(target, {"Target.One", "Target.Two"})
                return 1, lines

            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(MODULE, "run_build", side_effect=fake_run) as patched:
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    rc = MODULE.main([
                        "--root", str(root), "--color=never",
                        "Target.One", "Target.Two",
                    ])

            self.assertEqual(rc, 1)
            self.assertEqual(patched.call_count, 2)
            progress = stderr.getvalue()
            self.assertIn("[1/2] BUILD Target.One", progress)
            self.assertIn("[2/2] BUILD Target.Two", progress)
            report = stdout.getvalue()
            self.assertIn("mode: sequential target builds", report)
            self.assertIn("repeated 2 times", report)
            self.assertIn("while building: Target.One, Target.Two", report)

    def test_single_invocation_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "lakefile.toml").write_text("name = \"test\"\n")
            with mock.patch.object(MODULE, "run_build", return_value=(0, [])) as patched:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    rc = MODULE.main([
                        "--root", str(root), "--color=never", "--single-invocation",
                        "Target.One", "Target.Two",
                    ])
            self.assertEqual(rc, 0)
            self.assertEqual(patched.call_count, 1)
            command = patched.call_args.args[0]
            self.assertEqual(command[-2:], ["Target.One", "Target.Two"])

    def test_default_text_progress_uses_nonquiet_lake(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "lakefile.toml").write_text("name = \"test\"\n")
            with mock.patch.object(MODULE, "run_build", return_value=(0, [])) as patched:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    rc = MODULE.main([
                        "--root", str(root), "--color=never", "Target.One",
                    ])
            self.assertEqual(rc, 0)
            command = patched.call_args.args[0]
            self.assertNotIn("-q", command)
            self.assertNotIn("--quiet", command)

    def test_no_progress_keeps_quiet_lake(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "lakefile.toml").write_text("name = \"test\"\n")
            with mock.patch.object(MODULE, "run_build", return_value=(0, [])) as patched:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    rc = MODULE.main([
                        "--root", str(root), "--color=never", "--no-progress",
                        "Target.One",
                    ])
            self.assertEqual(rc, 0)
            command = patched.call_args.args[0]
            self.assertIn("-q", command)

    def test_run_build_streams_carriage_return_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            seen = []
            code = (
                "import sys; "
                "sys.stdout.write('[1/3] one\\r[2/3] two\\r[3/3] done\\n'); "
                "sys.stdout.flush()"
            )
            rc, lines = MODULE.run_build(
                [sys.executable, "-c", code],
                root,
                lambda done, total: seen.append((done, total)),
            )
            self.assertEqual(rc, 0)
            self.assertEqual(seen, [(1, 3), (2, 3), (3, 3)])
            self.assertEqual(lines, ["[1/3] one", "[2/3] two", "[3/3] done"])


if __name__ == "__main__":
    unittest.main()
