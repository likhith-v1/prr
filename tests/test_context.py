from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.context import build_context, findings_for_chunk
from core.ingest import Chunk
from core.schema import Finding


class ContextTests(unittest.TestCase):
    def test_relative_static_finding_matches_absolute_chunk_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "pkg" / "sample.py"
            path.parent.mkdir()
            path.write_text("print(nam)\n", encoding="utf-8")
            chunk = Chunk(
                path=str(path),
                name="<module>",
                kind="module",
                start_line=1,
                end_line=1,
                code="print(nam)",
            )
            finding = Finding(
                path="pkg/sample.py",
                line=1,
                severity="error",
                category="bug",
                comment="F821: Undefined name `nam`",
                source="ruff",
            )

            matched = findings_for_chunk(chunk, [finding])
            context = build_context(chunk, [finding])

        self.assertEqual(matched, [finding])
        self.assertIn("Static findings in this chunk", context)
        self.assertIn("Undefined name", context)


if __name__ == "__main__":
    unittest.main()
