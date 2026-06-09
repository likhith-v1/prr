from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from core.model import ModelBackendError
from core.schema import Finding
from frontends.cli import cmd_review, cmd_scan


class CliTests(unittest.TestCase):
    def test_model_backend_error_is_user_facing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            path.write_text("x = 1\n", encoding="utf-8")
            output = io.StringIO()

            with (
                patch("frontends.cli.run_static_tools", return_value=[]),
                patch("frontends.cli.review", side_effect=ModelBackendError("no ollama")),
                patch(
                    "frontends.cli.console",
                    Console(file=output, force_terminal=False, color_system=None),
                ),
            ):
                result = cmd_review(argparse.Namespace(file=str(path)))

        rendered = output.getvalue()
        self.assertEqual(result, 2)
        self.assertIn("Model backend failed.", rendered)
        self.assertIn("ollama pull qwen2.5-coder:14b", rendered)
        self.assertNotIn("Traceback", rendered)

    def test_review_rejects_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()

            with patch(
                "frontends.cli.console",
                Console(file=output, force_terminal=False, color_system=None),
            ):
                result = cmd_review(argparse.Namespace(file=tmp, config=None))

        self.assertEqual(result, 1)
        self.assertIn("Not a file", output.getvalue())

    def test_scan_uses_configured_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sample.py"
            path.write_text("x = 1\n", encoding="utf-8")
            config_path = root / "config.yaml"
            config_path.write_text("model: test-model\n", encoding="utf-8")
            output = io.StringIO()
            calls: list[dict[str, object]] = []

            def fake_review(**kwargs: object) -> list[Finding]:
                calls.append(kwargs)
                return []

            with (
                patch("frontends.cli.run_static_tools", return_value=[]),
                patch("frontends.cli.review", side_effect=fake_review),
                patch(
                    "frontends.cli.console",
                    Console(file=output, force_terminal=False, color_system=None),
                ),
            ):
                result = cmd_scan(argparse.Namespace(path=str(root), config=str(config_path)))

        self.assertEqual(result, 0)
        self.assertEqual(calls[0]["model"], "test-model")
        self.assertIn("prr is purring", output.getvalue())

    def test_scan_reports_static_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sample.py"
            path.write_text("print(nam)\n", encoding="utf-8")
            static = Finding(
                path=str(path),
                line=1,
                severity="error",
                category="bug",
                comment="F821: Undefined name `nam`",
                source="ruff",
            )
            output = io.StringIO()

            with (
                patch("frontends.cli.run_static_tools", return_value=[static]),
                patch("frontends.cli.review", return_value=[]),
                patch(
                    "frontends.cli.console",
                    Console(file=output, force_terminal=False, color_system=None),
                ),
            ):
                result = cmd_scan(argparse.Namespace(path=str(root), config=None))

        rendered = output.getvalue()
        self.assertEqual(result, 1)
        self.assertIn("sample.py", rendered)
        self.assertIn("Undefined name", rendered)

    def test_scan_respects_ignore_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            included = root / "sample.py"
            ignored_dir = root / ".venv"
            ignored = ignored_dir / "ignored.py"
            included.write_text("x = 1\n", encoding="utf-8")
            ignored_dir.mkdir()
            ignored.write_text("raise RuntimeError('skip me')\n", encoding="utf-8")
            output = io.StringIO()
            static_calls: list[list[Path]] = []
            review_calls: list[str] = []

            def fake_static(paths: list[Path], root: Path) -> list[Finding]:
                static_calls.append(paths)
                return []

            def fake_review(**kwargs: object) -> list[Finding]:
                review_calls.append(str(kwargs["path"]))
                return []

            with (
                patch("frontends.cli.run_static_tools", side_effect=fake_static),
                patch("frontends.cli.review", side_effect=fake_review),
                patch(
                    "frontends.cli.console",
                    Console(file=output, force_terminal=False, color_system=None),
                ),
            ):
                result = cmd_scan(argparse.Namespace(path=str(root), config=None))

        self.assertEqual(result, 0)
        self.assertEqual(static_calls, [[included]])
        self.assertEqual(review_calls, [str(included)])
        self.assertIn("prr is purring", output.getvalue())

    def test_scan_invalid_config_is_user_facing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.yaml"
            config_path.write_text("unknown: true\n", encoding="utf-8")
            output = io.StringIO()

            with patch(
                "frontends.cli.console",
                Console(file=output, force_terminal=False, color_system=None),
            ):
                result = cmd_scan(argparse.Namespace(path=str(root), config=str(config_path)))

        self.assertEqual(result, 1)
        self.assertIn("Config error", output.getvalue())


if __name__ == "__main__":
    unittest.main()
