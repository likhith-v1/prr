from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from core.model import ModelBackendError
from frontends.cli import cmd_review


class CliTests(unittest.TestCase):
    def test_model_backend_error_is_user_facing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            path.write_text("x = 1\n", encoding="utf-8")
            output = io.StringIO()

            with (
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


if __name__ == "__main__":
    unittest.main()
