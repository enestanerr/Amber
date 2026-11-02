---
title: "PROGRESS — Amber build log"
tags: [amber/log]
created: 2026-11-02
---
# PROGRESS.md — Amber build log

> [!NOTE]
> One dated entry per phase (see [PLAN.md](PLAN.md)). Each entry records the decisions taken
> from [docs/11-decisions.md](docs/11-decisions.md), what was built, the exact acceptance
> commands and their key output lines. Boxes in `PLAN.md` are ticked only after the output is
> recorded here.

## 2026-11-02 — Repository setup (pre-P0)

- Imported the plan package (`CLAUDE.md`, `PLAN.md`, `docs/00..11`) and made the repository an
  Obsidian-compatible vault: YAML frontmatter on every spec, `docs/index.md` map of content,
  `.obsidian/app.json` (markdown links, relative paths), workspace files gitignored.
- Remote: https://github.com/enestanerr/Amber (branch `main`).
- Environment verified: uv 0.13.6 · Python 3.12.10 (uv-managed) · git 2.55 · gh 2.103 ·
  claude CLI 2.1.316 · node 22.20 · GNU make 4.4.1 · gitleaks 8.30.1.
- Host is Windows 11; the plan targets macOS/Linux with Windows "best effort". Acceptance
  commands are run through Git Bash. `.gitattributes` forces LF so goldens stay byte-identical.

## 2026-11-02 — P0 Bootstrap

**Decisions taken (docs/11-decisions.md)**

| id | decision |
|---|---|
| D1 | **Default taken, not asked** (the user asked to start building autonomously and the GitHub repository is already named *Amber*): project **Amber**, distribution `amber-compiler`, import `amber`, CLI `amber`. The collision facts (PyPI `amber` placeholder, `amber-lang/amber`) stand; renaming later means touching `pyproject.toml`, `src/amber`, README and the console script. |
| D2 | Apache-2.0 (`LICENSE`, `license = "Apache-2.0"` in `pyproject.toml`). |
| D5 | `amber init` resolves model IDs via `anthropic.models.list()` when `ANTHROPIC_API_KEY` is set; otherwise writes `claude-sonnet-5-5` / `claude-haiku-5-5` with a `# verify` comment. Never hardcoded outside `amber.toml` except as the documented defaults in `amber/models/ids.py`. |
| D7 | SQLite + JSON files (store lands in P1). |
| D12 | Python ≥ 3.12; CI matrix 3.12 and 3.13 on ubuntu + macos. |
| D16 | Code, docs and README in English. |

**Spec corrections**

- `mcp` 2.3.0 has no `__version__`; the version-check command in `CLAUDE.md` and
  `docs/README-FIRST.md` now uses `importlib.metadata.version`.
- Installed API surfaces confirmed for P2: `mcp` 2.3.0 exports `Client`,
  `StdioServerParameters`; `mcp.server` exports `Server`, `MCPServer`, `ServerRequestContext`.
  `anthropic` 2.3.0. `claude --help` (2.1.316) shows `--output-format stream-json`,
  `--mcp-config`, `--strict-mcp-config`, `--permission-mode`, `--allowedTools`, `--verbose`
  and `--json-schema` (structured output for `-p`, usable by the `cli` model backend in P4).
- `ruff format` 0.17 formats fenced Python in Markdown; `*.md` and `docs/` are excluded so the
  specs keep their compact pseudo-code.

**Built**

- `pyproject.toml` (hatchling, `src/` layout, extras `sdk`/`ui`/`dev`, ruff/pyright/pytest/
  coverage config), `uv.lock`, `LICENSE`, `README.md` stub, `CHANGELOG.md`, `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, `SECURITY.md`, `.editorconfig`, `.gitattributes`, `Makefile`.
- `.pre-commit-config.yaml` (ruff, ruff-format, pyright local hook, gitleaks),
  `.github/workflows/ci.yml` (matrix, ruff, pyright, pytest `-m "not live"` with coverage
  artifact, gitleaks job), `.github/workflows/release.yml` (tag `v*` → `uv build` → Trusted
  Publishing; not run yet).
- `src/amber/`: `version.py`, `errors.py` (exit-code hierarchy), `config.py` (`amber.toml`
  model, `extra="forbid"`, secret lint), `project.py` (`init_project`), `util/{json,hashing,
  time,logging,paths}.py`, `models/ids.py`, `cli/{app,_output,init_cmd}.py`.
- `tests/conftest.py` (live tests skipped unless `-m live`; env cleared) and
  `tests/unit/test_smoke.py` (7 tests).

**Acceptance (run 2026-11-02, Windows 11 / Git Bash)**

```
$ uv sync --all-extras && uv run amber --version
amber 0.1.0a0

$ uv run amber init && test -f amber.toml && test -d .amber        # in a scratch directory
root     <scratch>/p0-init
created  .amber, amber.toml, .gitignore
models   synthesis=claude-sonnet-5-5 judgment=claude-haiku-5-5 (defaults)
ACCEPT: amber.toml and .amber exist

$ uv run ruff check . && uv run ruff format --check . && uv run pyright
All checks passed!
22 files already formatted
0 errors, 0 warnings, 0 informations

$ uv run pytest -q
7 passed in 0.32s
```

**Deferred / notes**

- `amber init` was run in a scratch directory, not at the repository root: the root is the
  Amber source tree, each `examples/<name>/` becomes its own project root in P8.
- `release.yml` needs the PyPI Trusted Publisher configured before the first tag.
