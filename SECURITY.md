# Security policy

## Supported versions

Amber is pre-release (`0.1.x`). Only the latest release on `main` receives fixes.

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Use GitHub's private
vulnerability reporting on this repository ("Security" → "Report a vulnerability"). You will
get an acknowledgement within 7 days.

## Scope notes

- Compiled programs (`programs/<workflow>/vN/program.py`) are Python that runs on your
  machine. Review them before running live; `REPORT.md` says so for every version.
- Generated transform code passes an AST allow-list and runs in a restricted namespace, but
  this is a defense in depth, not a sandbox boundary.
- Live runs default to dry-run for write-effect tools; `--allow-writes` is explicit.
- Secrets are read only from environment variables. `.amber/` and `*.env` are gitignored and
  the pre-commit hook runs `gitleaks`.
