# Week 3 — PR Mode + GitHub Output

**Goal:** `prr review --pr owner/repo#123` posts a real inline review on a real PR — comments on the right lines, one-click fix suggestions, a cat-verdict summary. Triggered by hand for now.

**Milestone:** you open a test PR, run the CLI against it, and see correctly-placed inline comments + `suggestion` blocks appear on GitHub, with a summary comment telling you whether prr is purring.

## Why this week
PR review only looks at the **diff**, not the whole repo, so it's naturally bounded and fast. The fiddly part is mapping findings to GitHub's diff-line model — get that right and the bot feels real.

## Tasks
- [ ] `core/ingest.py` — diff parsing: fetch the PR's changed files + hunks; review only **changed lines + N lines of context**; record each line's diff position / `line` + `side` (needed by the review API).
- [ ] Extend `filter.py` — in PR mode, also drop findings whose line **isn't part of the diff** (GitHub rejects comments on unchanged lines unless explicitly ranged).
- [ ] `core/github_out.py` — build and post **one** review:
  - `POST /repos/{owner}/{repo}/pulls/{n}/reviews` with `comments[]`.
  - Each comment: `path`, `line` (+ `start_line` for ranges), `side`, `body`. Prefix the body with the mood for the finding's severity (chirp / hiss / swat).
  - Render `suggestion` as a fenced ` ```suggestion ` block so it's one-click-appliable.
  - Add a summary comment: the cat's verdict (`prr is purring` when clean, otherwise `prr is not happy`) + counts by severity + a one-line note.
- [ ] `frontends/cli.py` — `prr review --pr owner/repo#n`: fetch diff via REST (PAT from env) → review changed hunks (reuse Week 1–2 core) → filter → `github_out.post`.

## Deliverables
- Diff parsing + diff-aware line filtering.
- GitHub review poster with inline comments + suggestion blocks + cat-verdict summary.
- `prr review --pr` working against a live PR.

## Done when
- Inline comments land on the exact changed lines, never off-by-one.
- Suggestion blocks apply cleanly with GitHub's "commit suggestion" button.
- No 422 errors from out-of-diff comment positions.
- A clean PR gets a single purring summary and no inline noise.

## Notes / gotchas
- **This is the most fiddly week.** GitHub review comments must reference lines that are part of the diff; wrong `line`/`side`/`position` → 422. Test the mapping on a tiny PR before trusting it.
- Post a **single review** with batched comments, not one API call per comment (rate limits + cleaner UX).
- Keep the per-PR comment cap aggressive here — a noisy first impression kills adoption (even for yourself).
