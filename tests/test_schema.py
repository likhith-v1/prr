from __future__ import annotations

import unittest

from pydantic import ValidationError

from core.schema import Finding


def make_finding(**overrides: object) -> Finding:
    data = {
        "path": "sample.py",
        "line": 1,
        "severity": "warning",
        "category": "bug",
        "comment": "Real issue.",
        "source": "llm",
    }
    data.update(overrides)
    return Finding(**data)


class FindingSchemaTests(unittest.TestCase):
    def test_valid_finding(self) -> None:
        finding = make_finding(confidence=0.5, end_line=2)

        self.assertEqual(finding.path, "sample.py")
        self.assertEqual(finding.confidence, 0.5)

    def test_line_must_be_positive(self) -> None:
        with self.assertRaises(ValidationError):
            make_finding(line=0)

    def test_end_line_must_not_precede_line(self) -> None:
        with self.assertRaises(ValidationError):
            make_finding(line=5, end_line=4)

    def test_confidence_must_be_between_zero_and_one(self) -> None:
        with self.assertRaises(ValidationError):
            make_finding(confidence=1.1)

        with self.assertRaises(ValidationError):
            make_finding(confidence=-0.1)

    def test_path_and_comment_must_not_be_empty(self) -> None:
        with self.assertRaises(ValidationError):
            make_finding(path="   ")

        with self.assertRaises(ValidationError):
            make_finding(comment="")

    def test_eslint_source_is_valid(self) -> None:
        finding = make_finding(source="eslint")
        self.assertEqual(finding.source, "eslint")


if __name__ == "__main__":
    unittest.main()
