from __future__ import annotations

import unittest

from core.diff import parse_patch


class DiffParserTests(unittest.TestCase):
    def test_single_hunk_added_and_context_lines(self) -> None:
        patch = "\n".join([
            "@@ -1,3 +1,4 @@",
            " def f():",
            "+    x = 1",
            "     return x",
            " # trailing",
        ])

        info = parse_patch(patch)

        self.assertEqual(info.added_lines, {2})
        self.assertEqual(info.commentable_lines, {1, 2, 3, 4})

    def test_multi_hunk_absolute_line_numbers(self) -> None:
        patch = "\n".join([
            "@@ -1,2 +1,2 @@",
            "-old = 1",
            "+new = 1",
            " keep = 2",
            "@@ -10,2 +10,3 @@",
            " context = 10",
            "+added = 11",
            " context = 12",
        ])

        info = parse_patch(patch)

        self.assertEqual(info.added_lines, {1, 11})
        self.assertEqual(info.commentable_lines, {1, 2, 10, 11, 12})

    def test_deletion_only_hunk_has_no_added_lines(self) -> None:
        patch = "\n".join([
            "@@ -5,3 +5,2 @@",
            " before = 5",
            "-removed = 6",
            " after = 6",
        ])

        info = parse_patch(patch)

        self.assertEqual(info.added_lines, set())
        self.assertEqual(info.commentable_lines, {5, 6})

    def test_no_newline_marker_is_ignored(self) -> None:
        patch = "\n".join([
            "@@ -1,1 +1,1 @@",
            "-old",
            "\\ No newline at end of file",
            "+new",
            "\\ No newline at end of file",
        ])

        info = parse_patch(patch)

        self.assertEqual(info.added_lines, {1})
        self.assertEqual(info.commentable_lines, {1})

    def test_empty_patch(self) -> None:
        info = parse_patch("")

        self.assertEqual(info.added_lines, set())
        self.assertEqual(info.commentable_lines, set())


if __name__ == "__main__":
    unittest.main()
