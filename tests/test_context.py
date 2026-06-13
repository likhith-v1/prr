from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.context import build_context, findings_for_chunk
from core.ingest import Chunk
from core.schema import Finding


def make_chunk(path: str, **overrides: object) -> Chunk:
    data = {
        "path": path,
        "name": "<module>",
        "kind": "module",
        "start_line": 1,
        "end_line": 1,
        "code": "print(nam)",
    }
    data.update(overrides)
    return Chunk(**data)


def make_finding(path: str, **overrides: object) -> Finding:
    data = {
        "path": path,
        "line": 1,
        "severity": "error",
        "category": "bug",
        "comment": "F821: Undefined name `nam`",
        "source": "ruff",
    }
    data.update(overrides)
    return Finding(**data)


class ContextTests(unittest.TestCase):
    def test_relative_static_finding_matches_absolute_chunk_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "pkg" / "sample.py"
            path.parent.mkdir()
            path.write_text("print(nam)\n", encoding="utf-8")
            chunk = make_chunk(str(path))
            finding = make_finding("pkg/sample.py")

            matched = findings_for_chunk(chunk, [finding], root=root)

        self.assertEqual(matched, [finding])

    def test_same_basename_in_other_directory_does_not_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for sub in ("", "utils"):
                directory = root / sub
                directory.mkdir(exist_ok=True)
                (directory / "sample.py").write_text("print(nam)\n", encoding="utf-8")
            chunk = make_chunk(str(root / "utils" / "sample.py"))
            finding = make_finding("sample.py")

            matched = findings_for_chunk(chunk, [finding], root=root)

        self.assertEqual(matched, [])

    def test_build_context_returns_structural_context_without_findings(self) -> None:
        chunk = make_chunk("sample.py", context="Top-level imports:\nimport os")

        context = build_context(chunk)

        self.assertEqual(context, "Top-level imports:\nimport os")
        self.assertNotIn("Static findings", context)


if __name__ == "__main__":
    unittest.main()
