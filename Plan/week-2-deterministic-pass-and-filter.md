# Week 2 — Deterministic Pass + Filter

**Goal:** `prr scan <repo>` produces a trustworthy whole-repo terminal report — no phantom lines, no duplicates, no nitpick spam.

**Milestone:** scanning a real repo of yours gives a report you'd actually act on, where every comment points at a line that exists and the obvious stuff (lint/type/security) is caught for free.

## Why this week
This is the trust layer. It's what makes a weaker local model usable: deterministic tools catch the precise/obvious issues and anchor the LLM, and the filter throws out the model's hallucinations before you ever see them.

## Tasks
- [x] `core/detect_static.py` — run and normalize into `Finding`s:
  - `ruff check --output-format json` → `source="ruff"`
  - `mypy` text output → `source="mypy"`
  - `bandit -f json` → `source="bandit"`
  - (Optional, multi-language later: `semgrep --json`.)
- [x] `core/context.py` — for each code chunk, attach the static findings landing on it, so the LLM sees them as anchors ("ruff already flagged line 42; reason about logic the tools can't").
- [x] `core/filter.py` — the trust gate, applied to **all** findings:
  1. **Line validation** — drop any finding whose `line` doesn't exist in the file (and, in PR mode next week, isn't in the diff). Kills phantom-line comments.
  2. **Dedup** — if the LLM and a tool flag the same line/issue, merge and prefer the tool's located fact + the LLM's explanation.
  3. **Threshold** — drop LLM findings below a confidence/severity cut (from config).
  4. **Cap** — max findings per file.
- [x] `frontends/cli.py` — add `prr scan <path>`: walk files (respect `ignore` globs) → static pass → LLM pass → filter → render a terminal report grouped by file.
- [x] `config.yaml` — `severity_threshold`, `ignore_paths`, `max_comments_per_file`, `model`.

## Deliverables
- Static-analysis runners feeding the same `Finding` schema.
- The filter (line-validation + dedup + threshold + cap).
- `prr scan` producing a clean terminal report. Markdown output is deferred.

## Done when
- A whole-repo scan report has zero comments on non-existent lines.
- Things `ruff`/`mypy` catch trivially are not missed.
- The report is short enough to read — noise is controlled by threshold + cap.
- Ignored paths are skipped before static analysis and LLM review.

## Notes / gotchas
- **Don't over-build the static layer.** ruff + mypy + bandit is an afternoon. Stop there; the LLM does the reasoning.
- When a tool and the LLM agree, that's your highest-confidence finding — surface those first.
- Whole-repo context won't fit the model — that's fine: you scan **per chunk**, not per repo. Tree-sitter chunking from Week 1 already handles this.
