# Week 1 — Core + Model Seam

**Goal:** `prr review <file.py>` reads a file and prints structured issues + suggested fixes from your local model.

**Milestone:** running the CLI on a single Python file produces a clean list of `Finding`s (line, severity, comment, suggestion) — even if quality is rough. The pipeline exists end to end for one file.

## Why this week first
Freeze the two things everything else depends on: the **Finding schema** (the contract) and the **model seam** (the swap point). Get them right now and the rest of the project is plumbing.

## Tasks
- [ ] Scaffold the `prr/` repo layout from the README.
- [ ] `core/schema.py` — define the Pydantic `Finding` model exactly as in the README. This is frozen from here on.
- [ ] Pull the model in Ollama and confirm JSON mode works:
  ```bash
  ollama pull qwen3-coder   # or devstral
  ```
- [ ] `core/model.py` — **the seam.** Single entry point:
  ```python
  def review(code: str, path: str, context: str = "", findings: list[Finding] = []) -> list[Finding]: ...
  ```
  - Ollama backend, `format="json"`.
  - Build the prompt (next bullet), call, parse JSON into `list[Finding]`, validate with Pydantic.
  - On parse failure: retry once with a "return valid JSON only" nudge, then drop.
  - Keep the backend behind a tiny interface so vLLM/another model swaps in later without touching callers.
- [ ] `core/prompts/review.txt` — system prompt. Must enforce:
  - Role: terse senior reviewer. Output **JSON array only**, no prose.
  - Each finding must **quote the exact offending line** (used later to validate it exists).
  - Severity rubric (error = likely bug/security; warning = real smell; info = minor).
  - **Only flag real issues. Do not nitpick. Prefer fewer, higher-confidence findings.** (A noisy cat gets muted.)
  - Include a `suggestion` (replacement code) when a concrete fix exists.
- [ ] `core/ingest.py` (minimal) — read a file, tree-sitter parse into function/class chunks with start/end line offsets. (Diff parsing comes Week 3.)
- [ ] `frontends/cli.py` — `prr review <file>`: ingest → `model.review` per chunk → print findings (plain or `rich`).

## Deliverables
- Frozen `Finding` schema.
- Working model seam (Ollama backend) returning validated `Finding`s.
- `prr review <file>` CLI.

## Done when
- You can run `prr review some_file.py` and get back structured findings with line numbers and suggestions.
- Swapping the model is a one-line change in `model.py` / config.

## Notes / gotchas
- **Structured output is the risk this week.** Weak models break free-form JSON — `format=json` + Pydantic + a single retry handles most of it. If it's flaky, that's the signal to move the seam to vLLM (guided decoding) sooner.
- Don't optimize prompt quality hard yet — that effort partly evaporates on a future model swap. Get it *working*, not perfect.
- Resist adding features. One file in, structured findings out. That's the whole week.
