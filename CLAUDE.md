# CLAUDE.md

## Public repository rules

- This repository is **public**. Never put anything that must not be seen outside (customer names, sales or contract information, regulatory review notes, internal judgments) in code, docs, data, or commit messages.
- Internal documents that need customer context go in `internal/` (excluded from git).
- Before pushing, check with `git grep` that no secret values (keys and passwords from .env) or any of the above made it in.

## Git commit rules

- **Do not add** co-author trailer lines such as `Co-Authored-By: Claude ...` to commit messages.
- The author is `sehyun <21984972+setch3000@users.noreply.github.com>` (GitHub private address). This Mac has no global git user setting, so set it with `git -c user.name="sehyun" -c user.email="21984972+setch3000@users.noreply.github.com" commit ...`.
- Write commit messages in English.
- Work on a per-stage branch, and fast-forward merge into `main` once verified.
- Do not commit `.env`, `.streamlit/secrets.toml`, `.venv/`, or `internal/`. Do not commit `.agents/` or `.claude/` either until the user confirms.

## Comments and docs

- Code comments, docstrings, README.md, and docs/ARCHITECTURE.md are in English.
- UI text lives in `ui/i18n.py` and `ui/explain.py` as (Korean, English) pairs. LLM prompts, SQL labels, and runtime messages stay as they are, since changing them changes app behavior.

## Verification before merging

```bash
.venv/bin/python scripts/smoke_test.py            # external service connectivity and compatibility
.venv/bin/python scripts/run_scenario.py --lang ko # highlight scene, 7 pass criteria
.venv/bin/python scripts/run_scenario.py --lang en
.venv/bin/python scripts/ui_check.py              # 6 screens × 2 languages, sign-in gate, admin permissions
```

Design and decisions follow [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
