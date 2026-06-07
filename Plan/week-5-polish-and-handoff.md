# Week 5 — Polish + Handoff (optional)

**Goal:** make prr trustworthy enough to leave running, and clean enough that revisiting it later = swap the model and rerun a check. Then walk away.

**Milestone:** a seeded eval exists, noise is tuned down from real usage, and the model-swap procedure is one config change + one command.

## Why this week
The "revisit when models improve" goal lives or dies here. The eval is what lets a future-you confirm a new model is actually better in five minutes instead of re-reading the whole codebase.

## Tasks
- [ ] **Noise tuning from real PRs** — adjust `severity_threshold`, per-PR cap, and prompt wording based on what prr actually flagged in Weeks 3–4. Cut the nitpicks (keep the cat from getting muted).
- [ ] **Seeded eval** — pick 3–5 PRs/files with **known** issues (real bugs you've fixed before). Add `prr eval`:
  - runs prr over them, reports caught / missed / false-positives.
  - This is your model-swap regression test. Rerun on every future model change.
- [ ] **Optional — verify high-severity fixes:** for `error`-severity (swat) findings with a `suggestion`, apply it in a throwaway clone, run `ruff`/tests; only surface the suggestion if it doesn't break anything. Skip for low severity (too slow to be worth it).
- [ ] **Optional — move the seam to vLLM** for guided decoding (more reliable JSON) + concurrency. Behind the same `review()` interface, so nothing else changes.
- [ ] **Optional — the purr** — a small contented-cat ASCII printed on a clean CLI run, so a green check actually feels good.
- [ ] **Document the swap** in the README:
  > change `model:` in `config.yaml` → run `prr eval` → if numbers improve, keep it.

## Deliverables
- Tuned thresholds/prompt.
- `prr eval` regression check.
- (Optional) fix-verification + vLLM seam + the purr.
- Documented swap procedure.

## Done when
- prr's output is signal, not spam.
- You can swap models and re-validate in minutes.
- You're comfortable leaving it on and ignoring it until a better model ships.

## Notes / gotchas
- **Don't gold-plate.** This is a personal project — "good enough to leave running" is the bar, not "production SaaS."
- The eval is the single highest-leverage thing here. Everything else is optional; the eval is what makes the future revisit cheap.
- Effort spent prompt-hacking the *current* model has a short shelf life — invest in the eval and the seam (which don't go stale) over squeezing today's model.

## After Week 5 — natural extensions (future)
- Flip prr into a **reviewer of incoming PRs** (it already reviews diffs — point it at others' PRs).
- Add languages: tree-sitter + the matching linters (`eslint`, `clippy`, etc.); the core is language-agnostic.
- Add a "fix-and-open-PR" agent mode on top (the harder SWE-agent direction) once local models are strong enough — your seam + verifier are already the foundation for it.
