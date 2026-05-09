
## Rules

Always use `uv run` for all **Python commands** — never call `python`, `pytest`, `mypy`, or `ruff` directly. This project uses uv for environment management.

Golden test must run separately from unit tests and always in background. Exclude with: `pytest tests/ --ignore=tests/test_golden.py`. Golden test takes ~10min; use background task with until-loop completion check on output file. Cancel loop immediately after retrieving result.

## Applied Learning

When something fails repeatedly, when user has to re-explain, or workaround is found for a platform/tool limitation, add one line bullet here. Keep each bullet under 15 words. No explanations. Only add things that will save time in future sessions.

- Edit tool: `old_string` must be unique or provide more context; use `replace_all: true` carefully for multi-occurrence strings.
- Bash sleep blocking: can't chain `sleep 60 && command`. Use `until <condition>; do sleep N; done` for waiting on file state.
- Background tasks: monitor output file with `until grep -q PATTERN file` instead of naive sleep chains; use tail -f cautiously.
- Golden test runtime ~10min; prefer `timeout` wrapper or background task with `until` completion check on output file.