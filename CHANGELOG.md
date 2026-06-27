# Changelog

All notable changes to AI Key Pool will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-06-28

### Added

- **Key Registry**: Persistent storage for API keys with status tracking (active, exhausted, disabled).
- **Key Manager**: High-level interface for key selection, usage recording, and lifecycle management.
- **Key Rotator**: Automatic key rotation with configurable retry logic on rate limit and quota errors.
- **Health Checker**: Tracks consecutive failures, health status transitions, and timestamps per key.
- **Configuration System**: Environment-variable-based config with optional JSON file support.
- **Logging**: Structured logging for key selection, rotation, failures, and health events.
- **Input Validation**: `register_key()` validates non-empty key_id, provider, and key_value.
- **Auto-Disable**: Keys automatically disable after configurable consecutive failures (default: 5).
- **Dashboard Placeholder**: Basic GitHub Pages deployment for future status dashboard.
- **Simulation Tests**: 7 test cases covering registration, rotation, health tracking, and validation.
- **MIT License**.

### Changed

- Standardized all imports to relative style within the `src` package.
- Made auto-disable threshold configurable via `AIKEYPOOL_MAX_CONSECUTIVE_FAILURES`.
- Improved docstrings with Args/Returns sections across all public methods.
- Improved error messages with specific failure reasons.

### Fixed

- Type annotation bug in `KeyRotator.force_rotate()` — `KeyEntry` was referenced but not imported.

### Removed

- Unused `ProviderConfig.rate_limit` and `daily_limit` fields (not yet implemented).
- Unused `Config.get_provider_keys()` method.
- Empty `src/research/` placeholder module.
- Unused imports: `time`, `Path`, `field` from various modules.

## [0.1.0] - 2026-06-27

### Added

- Initial project structure.
- GitHub Pages deployment workflow.
- Status dashboard placeholder.
