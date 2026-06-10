from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.config import PrrConfig
from core.filter import filter_findings
from core.schema import Finding


def make_finding(**overrides: object) -> Finding:
    data = {
        "path": "sample.py",
        "line": 1,
        "severity": "warning",
        "category": "bug",
        "comment": "Real issue.",
        "source": "llm",
        "confidence": 0.9,
    }
    data.update(overrides)
    return Finding(**data)


class FilterTests(unittest.TestCase):
    def test_drops_findings_outside_file_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sample.py"
            path.write_text("a = 1\nb = 2\n", encoding="utf-8")

            findings = filter_findings(
                [
                    make_finding(path=str(path), line=1),
                    make_finding(path=str(path), line=3),
                    make_finding(path=str(path), line=2, end_line=4),
                ],
                config=PrrConfig(),
                root=root,
            )

        self.assertEqual([finding.line for finding in findings], [1])
        self.assertEqual(findings[0].path, "sample.py")

    def test_applies_severity_and_llm_confidence_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sample.py"
            path.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")

            findings = filter_findings(
                [
                    make_finding(path=str(path), line=1, severity="info", source="ruff"),
                    make_finding(path=str(path), line=2, confidence=0.4),
                    make_finding(path=str(path), line=3, confidence=0.9),
                ],
                config=PrrConfig(severity_threshold="warning", min_confidence=0.8),
                root=root,
            )

        self.assertEqual([finding.line for finding in findings], [3])

    def test_merges_static_and_llm_duplicates_on_same_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sample.py"
            path.write_text("print(nam)\n", encoding="utf-8")

            findings = filter_findings(
                [
                    make_finding(
                        path=str(path),
                        source="ruff",
                        severity="warning",
                        comment="F821: Undefined name `nam`",
                    ),
                    make_finding(
                        path=str(path),
                        severity="error",
                        comment="This crashes when the function is called.",
                        suggestion="print(name)",
                    ),
                ],
                config=PrrConfig(),
                root=root,
            )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].source, "ruff")
        self.assertEqual(findings[0].severity, "error")
        self.assertIn("Undefined name", findings[0].comment)
        self.assertIn("crashes", findings[0].comment)
        self.assertEqual(findings[0].suggestion, "print(name)")

    def test_merges_distinct_static_findings_on_same_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sample.py"
            path.write_text("x = eval(data)\n", encoding="utf-8")

            findings = filter_findings(
                [
                    make_finding(
                        path=str(path),
                        source="ruff",
                        severity="error",
                        comment="S307: Use of possibly insecure `eval`",
                    ),
                    make_finding(
                        path=str(path),
                        source="ruff",
                        severity="warning",
                        comment="F841: Local variable `x` is assigned to but never used",
                    ),
                ],
                config=PrrConfig(),
                root=root,
            )

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "error")
        self.assertIn("insecure `eval`", findings[0].comment)
        self.assertIn("never used", findings[0].comment)

    def test_sorts_by_severity_and_caps_per_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sample.py"
            path.write_text("a\nb\nc\nd\n", encoding="utf-8")

            findings = filter_findings(
                [
                    make_finding(path=str(path), line=1, severity="warning"),
                    make_finding(path=str(path), line=3, severity="error"),
                    make_finding(path=str(path), line=2, severity="error"),
                ],
                config=PrrConfig(max_comments_per_file=2),
                root=root,
            )

        self.assertEqual([(finding.severity, finding.line) for finding in findings], [
            ("error", 2),
            ("error", 3),
        ])


if __name__ == "__main__":
    unittest.main()
