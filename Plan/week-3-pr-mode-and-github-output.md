# Week 3 — PR Mode + GitHub Output

**Goal:** `prr review --pr owner/repo#123` posts a real inline review on a real PR — comments on the right lines, one-click fix suggestions, a cat-verdict summary. Triggered by hand for now.

**Milestone:** you open a test PR, run the CLI against it, and see correctly-placed inline comments + `suggestion` blocks appear on GitHub, with a summary comment telling you whether prr is purring.

## Why this week
PR review only looks at the **diff**, not the whole repo, so it's naturally bounded and fast. The fiddly part is mapping findings to GitHub's diff-line model — get that right and the bot feels real.

## Tasks
- [x] `core/diff.py` (instead of `core/ingest.py` — ingest stays tree-sitter-only) — parse each PR file's `patch` into `added_lines` (drives which chunks get reviewed) and `commentable_lines` (`line` + `side="RIGHT"` validity for the review API). Full files are fetched at the head SHA into a temp workspace so Week 1–2 chunking/static/filter code runs unchanged.
- [x] Extend `filter.py` — `filter_findings(..., allowed_lines=...)` drops findings whose line **isn't an added line** in PR mode; ranges that partially leave the diff fall back to a single-line anchor without a suggestion.
- [x] `core/github_out.py` — build and post **one** review:
  - `POST /repos/{owner}/{repo}/pulls/{n}/reviews` with `comments[]`.
  - Each comment: `path`, `line` (+ `start_line` for ranges), `side`, `body`. Prefix the body with the mood for the finding's severity (chirp / hiss / swat).
  - Render `suggestion` as a fenced ` ```suggestion ` block so it's one-click-appliable.
  - Add a summary comment: the cat's verdict (`prr is purring` when clean, otherwise `prr is not happy`) + counts by severity + a one-line note.
- [x] `frontends/cli.py` — `prr review --pr owner/repo#n [--dry-run]`: fetch diff via REST (PAT from `GITHUB_TOKEN`) → review chunks overlapping added lines (reuse Week 1–2 core) → filter to added lines only → cap via `max_comments_per_pr` (default 10) → `post_review`. PR static analysis is file-scoped (`ruff` + `bandit`); skipped Python files are noted.

## Deliverables
- Diff parsing + diff-aware line filtering.
- GitHub review poster with inline comments + suggestion blocks + cat-verdict summary.
- `prr review --pr` working against a live PR.

## Done when
- Inline comments land on the exact changed lines, never off-by-one.
- Suggestion blocks apply cleanly with GitHub's "commit suggestion" button.
- No 422 errors from out-of-diff comment positions.
- No comments are posted on unchanged context lines in v1.
- A clean PR gets a single purring summary and no inline noise.

## Notes / gotchas
- **This is the most fiddly week.** GitHub review comments must reference lines that are part of the diff; wrong `line`/`side`/`position` → 422. Test the mapping on a tiny PR before trusting it.
- Post a **single review** with batched comments, not one API call per comment (rate limits + cleaner UX).
- Keep the per-PR comment cap aggressive here — a noisy first impression kills adoption (even for yourself).
