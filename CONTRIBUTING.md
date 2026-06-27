# Contributing to AI Key Pool

Thank you for your interest in contributing to AI Key Pool! This document provides guidelines for contributing.

## Getting Started

1. Fork the repository
2. Clone your fork
3. Create a feature branch: `git checkout -b feature/my-change`
4. Make your changes
5. Run tests: `python tests/test_simulation.py`
6. Commit your changes
7. Push to your fork and submit a pull request

## Development Setup

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/ai-key-pool.git
cd ai-key-pool

# No external dependencies needed for core engine
# All modules use Python standard library only

# Run tests
python tests/test_simulation.py
```

## Code Style

- Follow [PEP 8](https://peps.python.org/pep-0008/) for Python code.
- Use type hints for all public methods.
- Write docstrings for all public classes and methods.
- Keep imports organized: stdlib, then third-party, then local.

## Project Structure

```text
src/
    key_pool/           # Core key management
    health/             # Health tracking
    utils/              # Config, logging
tests/                  # Simulation tests
data/                   # Runtime state (gitignored)
dashboard/              # GitHub Pages dashboard
```

## Adding Features

1. **New provider support**: Add a provider-specific module under `src/providers/`.
2. **New health checks**: Extend `HealthChecker` in `src/health/`.
3. **New config options**: Add to `Config` dataclass in `src/utils/config.py`.

## Reporting Issues

- Use GitHub Issues for bug reports and feature requests.
- Include Python version, OS, and steps to reproduce.

## Pull Request Guidelines

- Keep PRs focused on a single change.
- Include tests for new functionality.
- Update documentation if needed.
- Ensure all existing tests pass.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
