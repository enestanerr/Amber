# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Trace model and store (P1): canonical `Trace` models, JSON-path utilities,
  canonicalization (`canonicalize_args`, `args_fingerprint`, `normalize_result`), JSON +
  SQLite `TraceStore`, `amber traces ls|show|rm`, seeded weekly-report fixtures.
- Project bootstrap (P0): packaging, CLI skeleton (`amber --version`, `amber init`), lint,
  type-check, test and CI configuration.

[Unreleased]: https://github.com/enestanerr/Amber/compare/main...HEAD
