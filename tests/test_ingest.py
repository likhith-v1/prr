from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from core.ingest import chunk_file


class IngestTests(unittest.TestCase):
    def chunk_source(self, source: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
            return chunk_file(path)

    def test_module_only_file_is_single_module_chunk(self) -> None:
        chunks = self.chunk_source(
            """
            x = 1
            y = x + 1
            """
        )

        self.assertEqual([(c.kind, c.name) for c in chunks], [("module", "<module>")])

    def test_top_level_and_decorated_functions_are_chunked(self) -> None:
        chunks = self.chunk_source(
            """
            import functools

            def plain():
                return 1

            @functools.cache
            def cached():
                return 2
            """
        )

        self.assertIn(("function", "plain"), [(c.kind, c.name) for c in chunks])
        self.assertIn(("function", "cached"), [(c.kind, c.name) for c in chunks])

    def test_class_methods_are_method_chunks(self) -> None:
        chunks = self.chunk_source(
            """
            class Example:
                value = 1

                def method(self):
                    return self.value

                @staticmethod
                def helper():
                    return 2
            """
        )

        self.assertIn(("method", "method"), [(c.kind, c.name) for c in chunks])
        self.assertIn(("method", "helper"), [(c.kind, c.name) for c in chunks])

    def test_class_without_methods_remains_class_chunk(self) -> None:
        chunks = self.chunk_source(
            """
            class Constants:
                answer = 42
            """
        )

        self.assertEqual([(c.kind, c.name) for c in chunks], [("class", "Constants")])


if __name__ == "__main__":
    unittest.main()
