from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.detect_static import parse_bandit_json, parse_mypy_text, parse_ruff_json


class StaticParserTests(unittest.TestCase):
    def test_parse_ruff_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = json.dumps([
                {
                    "filename": str(root / "sample.py"),
                    "code": "F821",
                    "message": "Undefined name `nam`",
                    "location": {"row": 3, "column": 12},
                    "end_location": {"row": 3, "column": 15},
                }
            ])

            findings = parse_ruff_json(raw, root=root)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].path, "sample.py")
        self.assertEqual(findings[0].line, 3)
        self.assertEqual(findings[0].severity, "error")
        self.assertEqual(findings[0].category, "bug")
        self.assertEqual(findings[0].source, "ruff")

    def test_parse_mypy_text(self) -> None:
        raw = "\n".join([
            "sample.py:2: error: Name \"nam\" is not defined  [name-defined]",
            "sample.py:3: note: Revealed type is \"builtins.int\"",
        ])

        findings = parse_mypy_text(raw)

        self.assertEqual([(f.line, f.severity, f.source) for f in findings], [
            (2, "error", "mypy"),
            (3, "info", "mypy"),
        ])
        self.assertIn("name-defined", findings[0].comment)

    def test_parse_bandit_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = json.dumps({
                "results": [
                    {
                        "filename": str(root / "sample.py"),
                        "line_number": 8,
                        "issue_severity": "HIGH",
                        "issue_confidence": "MEDIUM",
                        "issue_text": "Use of hardcoded password",
                        "test_id": "B105",
                    }
                ]
            })

            findings = parse_bandit_json(raw, root=root)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].path, "sample.py")
        self.assertEqual(findings[0].line, 8)
        self.assertEqual(findings[0].severity, "error")
        self.assertEqual(findings[0].category, "security")
        self.assertEqual(findings[0].source, "bandit")
        self.assertEqual(findings[0].confidence, 0.7)

    def test_malformed_tool_output_fails_closed(self) -> None:
        self.assertEqual(parse_ruff_json("not json"), [])
        self.assertEqual(parse_bandit_json("not json"), [])
        self.assertEqual(parse_mypy_text("not mypy output"), [])


if __name__ == "__main__":
    unittest.main()
