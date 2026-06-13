"""Unified-diff (GitHub `patch`) parsing for PR mode.

GitHub's "list pull request files" API returns a per-file `patch` string in
unified-diff format without file headers.  This module extracts which
new-file (RIGHT side) line numbers were added and which are commentable —
GitHub rejects review comments on lines outside the diff hunks with a 422.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<new_start>\d+)(?:,\d+)? @@")


@dataclass
class PatchInfo:
    added_lines: set[int] = field(default_factory=set)        # '+' lines (new file)
    commentable_lines: set[int] = field(default_factory=set)  # '+' and context lines


def parse_patch(patch: str) -> PatchInfo:
    """Parse one file's `patch` text into RIGHT-side line sets.

    Line numbers are 1-based and absolute within the new version of the file.
    Deleted ('-') lines advance only the old-file counter and are never
    commentable on the RIGHT side.
    """
    info = PatchInfo()
    new_line = 0
    in_hunk = False

    for line in patch.splitlines():
        header = _HUNK_HEADER.match(line)
        if header is not None:
            new_line = int(header.group("new_start"))
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("\\"):  # "\ No newline at end of file"
            continue
        if line.startswith("+"):
            info.added_lines.add(new_line)
            info.commentable_lines.add(new_line)
            new_line += 1
        elif line.startswith("-"):
            continue
        else:  # context line (starts with ' ', or empty for blank context lines)
            info.commentable_lines.add(new_line)
            new_line += 1

    return info
