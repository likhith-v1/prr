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

        self.assertEqual([(c.kind, c.name) for c in chunks], [
            ("method", "Example.method"),
            ("method", "Example.helper"),
        ])
        self.assertIn("class Example:", chunks[0].context)
        self.assertIn("value = 1", chunks[0].context)

    def test_class_without_methods_remains_class_chunk(self) -> None:
        chunks = self.chunk_source(
            """
            class Constants:
                answer = 42
            """
        )

        self.assertEqual([(c.kind, c.name) for c in chunks], [("class", "Constants")])

    def test_duplicate_method_names_are_qualified_by_class(self) -> None:
        chunks = self.chunk_source(
            """
            class First:
                def run(self):
                    return 1

            class Second:
                def run(self):
                    return 2
            """
        )

        self.assertEqual([c.name for c in chunks], ["First.run", "Second.run"])

    def test_async_functions_are_chunked(self) -> None:
        chunks = self.chunk_source(
            """
            async def fetch():
                return 1

            class Worker:
                async def run(self):
                    return 2
            """
        )

        self.assertEqual([(c.kind, c.name) for c in chunks], [
            ("function", "fetch"),
            ("method", "Worker.run"),
        ])

    def test_crlf_source_matches_lf_chunking(self) -> None:
        # A Windows/CRLF checkout must produce identical, LF-normalized chunks so
        # line numbers stay 1-based and absolute regardless of platform.
        body = (
            "import os\n"
            "\n"
            "def first():\n"
            "    return 1\n"
            "\n"
            "def second():\n"
            "    return 2\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            lf_path = Path(tmp) / "lf.py"
            crlf_path = Path(tmp) / "crlf.py"
            lf_path.write_bytes(body.encode("utf-8"))
            crlf_path.write_bytes(body.replace("\n", "\r\n").encode("utf-8"))

            lf_chunks = chunk_file(lf_path)
            crlf_chunks = chunk_file(crlf_path)

        self.assertEqual(
            [(c.kind, c.name, c.start_line, c.end_line) for c in crlf_chunks],
            [(c.kind, c.name, c.start_line, c.end_line) for c in lf_chunks],
        )
        # Chunk code is LF-normalized — no stray carriage returns leak through.
        for chunk in crlf_chunks:
            self.assertNotIn("\r", chunk.code)
        self.assertEqual(
            [c.code for c in crlf_chunks],
            [c.code for c in lf_chunks],
        )

    def test_syntax_error_file_falls_back_to_module_chunk(self) -> None:
        chunks = self.chunk_source(
            """
            def broken(:
                pass
            """
        )

        self.assertEqual([(c.kind, c.name) for c in chunks], [("module", "<module>")])
        self.assertIn("syntax errors", chunks[0].context)


if __name__ == "__main__":
    unittest.main()
