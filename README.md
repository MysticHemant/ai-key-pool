# AI Key Pool

A lightweight, provider-agnostic API key management library for Python.

AI Key Pool automatically rotates between multiple API keys when requests fail due to rate limiting or quota exhaustion. It works with any AI provider that uses API key authentication.

## Features

- **Automatic Key Rotation** — Switches keys on rate limit (429) or quota errors
- **Health Tracking** — Monitors consecutive failures per key with status transitions
- **Auto-Disable** — Disables keys after configurable consecutive failures
- **Multi-Provider** — Manages keys across multiple providers simultaneously
- **Zero Dependencies** — Pure Python standard library core engine
- **Persistent State** — JSON-based storage in `data/` directory
- **Configurable** — All settings via environment variables
- **Provider Adapters** — GitHub Models, Groq, OpenRouter (OpenAI-compatible)
- **HTTP API** — FastAPI gateway with Master Key authentication
- **Dashboard** — Static HTML status + recommendations pages (GitHub Pages)
- **Daily Automation** — GitHub Actions: research, email, dashboard update
- **Email Summaries** — Daily health/recap via SMTP

## Quick Start

```python
from src.key_pool import KeyManager, KeyRotator
from src.utils.config import load_config

# Load configuration from environment variables
config = load_config()

# Initialize the key manager
manager = KeyManager(config.data_dir)

# Register API keys
manager.register_key("key-1", "openai", "sk-your-openai-key")
manager.register_key("key-2", "openai", "sk-your-openai-key-2")
manager.register_key("key-3", "anthropic", "sk-ant-your-key")

# Get the active key for a provider
key = manager.get_active_key("openai")
print(f"Using key: {key.key_id}")

# Use KeyRotator for automatic rotation on failure
rotator = KeyRotator(config, manager)
result = rotator.execute_with_rotation(
    "openai",
    lambda api_key: call_your_api(api_key)
)

if result.success:
    print(f"Request succeeded with key: {result.key_used}")
else:
    print(f"All keys exhausted: {result.error}")
```

## HTTP API

Start the service:

```bash
uvicorn src.api.app:app --host 0.0.0.0 --port 8000
```

Set your Master Key (required for authentication):

```bash
export AIKEYPOOL_MASTER_KEY="your-secret-key"
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Service info |
| GET | `/health` | Health check |
| POST | `/chat` | Chat completion (auto-rotates keys) |
| POST | `/rotate` | Manual key rotation |
| GET | `/status` | Full key pool status |

### Usage Example

```python
import httpx

resp = httpx.post(
    "http://localhost:8000/chat",
    headers={"Authorization": "Bearer your-master-key"},
    json={
        "provider": "groq",
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": "Hello!"}],
    },
)
print(resp.json())
```

### Configuration

All configuration is loaded from environment variables. No secrets are hardcoded.

| Variable | Default | Description |
|----------|---------|-------------|
| `AIKEYPOOL_MASTER_KEY` | — | Master key for API authentication |
| `AIKEYPOOL_ACTIVE_PROVIDER` | First provider | Currently active provider name |
| `AIKEYPOOL_RETRY_COUNT` | `3` | Number of retries before giving up |
| `AIKEYPOOL_MAX_CONSECUTIVE_FAILURES` | `5` | Auto-disable threshold |
| `AIKEYPOOL_PROVIDER_<NAME>_KEYS` | — | Comma-separated API keys per provider |
| `AIKEYPOOL_LOG_LEVEL` | `INFO` | Logging level |
| `AIKEYPOOL_DATA_DIR` | `./data` | Path to data directory |
| `AIKEYPOOL_RESEARCH_PROMPT` | — | Custom AI research prompt |
| `SMTP_HOST` | — | SMTP server for email |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | — | SMTP username |
| `SMTP_PASSWORD` | — | SMTP password |
| `EMAIL_RECIPIENT` | — | Email recipient |

### Example Setup

```bash
export AIKEYPOOL_MASTER_KEY="my-secret-key"
export AIKEYPOOL_ACTIVE_PROVIDER="groq"
export AIKEYPOOL_PROVIDER_GROQ_KEYS="gsk_key1,gsk_key2"
export AIKEYPOOL_PROVIDER_OPENROUTER_KEYS="or_key1,or_key2"
export AIKEYPOOL_RETRY_COUNT="3"
export AIKEYPOOL_LOG_LEVEL="INFO"

# Email (optional)
export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USER="you@gmail.com"
export SMTP_PASSWORD="app-password"
export EMAIL_RECIPIENT="you@gmail.com"
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       HTTP API (FastAPI)                        │
│  POST /chat — Chat completion (auto-rotates provider keys)      │
│  POST /rotate — Manual rotation                                 │
│  GET /status — Key pool status                                  │
│  GET /health — Health check                                     │
├─────────────────────────────────────────────────────────────────┤
│                   Provider Adapters                             │
│  github_models.py │ groq.py │ openrouter.py                    │
│  (OpenAI-compatible endpoints, custom auth headers)             │
├─────────────────────────────────────────────────────────────────┤
│                       Key Rotator                               │
│  - Executes requests with automatic retry                       │
│  - Triggers rotation on rate limit / quota errors               │
│  - Classifies errors into rotation-eligible categories          │
├─────────────────────────────────────────────────────────────────┤
│                       Key Manager                               │
│  - get_active_key() / get_next_key()                            │
│  - mark_success() / mark_failure()                              │
│  - disable_key() / enable_key()                                 │
│  - Auto-disables after N consecutive failures                   │
├─────────────────────────────────────────────────────────────────┤
│              ┌───────────────┬──────────────────┐               │
│              │  Key Registry │  Health Checker   │               │
│              │  - Key status │  - Consecutive    │               │
│              │  - Usage stats│    failures       │               │
│              │  - Persistence│  - Status         │               │
│              └───────────────┴──────────────────┘               │
├─────────────────────────────────────────────────────────────────┤
│                        Data Layer                               │
│            key_registry.json / key_health.json                  │
├─────────────────────────────────────────────────────────────────┤
│                   Daily Maintenance                             │
│  orchestrator.py — 5-step cycle: health → research → dashboard  │
│  research.py — AI research via KeyRotator                       │
│  email_sender.py — SMTP daily summary                           │
│  dashboard_gen.py — Writes status.json + recommendations.json   │
└─────────────────────────────────────────────────────────────────┘
```

## Key Rotation Flow

1. **Request initiated** — Uses the current active key for the provider
2. **Success** — Key remains active, failure count resets
3. **Rate limit / Quota exhausted** — Rotation triggered
4. **Next healthy key selected** — Request retried automatically
5. **No healthy keys** — Clear error returned with provider name

Rotation is provider-independent and works with any API key pattern.

## Health Tracking

Each key maintains a health record with:

| Field | Description |
|-------|-------------|
| `status` | ACTIVE, EXHAUSTED, or DISABLED |
| `consecutive_failures` | Reset on each success |
| `total_failures` | Lifetime failure count |
| `total_successes` | Lifetime success count |
| `last_success` | ISO timestamp of last success |
| `last_failure` | ISO timestamp of last failure |

## Error Classification

The `KeyRotator` classifies errors into categories that trigger rotation:

| Error Type | Triggers Rotation | Example Messages |
|------------|-------------------|------------------|
| `rate_limit` | Yes | "429 Rate limit exceeded" |
| `quota_exhausted` | Yes | "Quota exceeded" |
| `auth_error` | Yes | "401 Unauthorized", "Invalid API key" |
| `unknown` | No | Any other error |

## API Reference

### KeyManager

```python
manager = KeyManager(data_dir, max_consecutive_failures=5)

manager.register_key(key_id, provider, key_value)
manager.get_active_key(provider)
manager.get_next_key(provider, exclude_key_ids)
manager.mark_success(key_id)
manager.mark_failure(key_id, error_type)
manager.disable_key(key_id, reason)
manager.enable_key(key_id)
manager.get_key_status(key_id)
manager.get_provider_summary(provider)
manager.get_all_stats()
```

### KeyRotator

```python
rotator = KeyRotator(config, key_manager)

result = rotator.execute_with_rotation(provider, request_fn, max_retries)
# Returns: RotationResult(success, key_used, retries, error, rotations)

rotator.force_rotate(provider, current_key_id)
```

### Provider Adapters

```python
from src.providers import create_provider

provider = create_provider("groq")
# Or: GitHubModelsProvider(), GroqProvider(), OpenRouterProvider()

response = provider.chat(api_key, model, messages, temperature=0.7, max_tokens=1000)
# Returns: ChatResponse(content, model, provider, usage)

response = provider.health_check(api_key, model)
# Returns: ChatResponse or None if unhealthy
```

## Running Tests

```bash
python tests/test_simulation.py  # Core engine tests
python tests/test_mvp.py         # Full stack tests (providers, API, dashboard)
```

## Project Structure

```text
src/
    key_pool/
        key_registry.py     # Key storage and state tracking
        key_manager.py      # High-level key management interface
        key_rotator.py      # Automatic rotation logic
    health/
        health_checker.py   # Key health tracking
    utils/
        config.py           # Configuration system
        logger.py           # Logging utilities
    providers/
        base_provider.py    # BaseProvider ABC + ChatMessage/ChatResponse
        github_models.py    # GitHub Models adapter
        groq.py             # Groq adapter
        openrouter.py       # OpenRouter adapter
        provider_factory.py # create_provider(), list_providers()
    api/
        app.py              # FastAPI factory with lifespan
        auth.py             # Master Key authentication
        models.py           # Pydantic request/response models
        routes.py           # API endpoints
    maintenance/
        orchestrator.py     # 5-step daily maintenance cycle
        research.py         # AI research via KeyRotator
        email_sender.py     # SMTP daily summary
        dashboard_gen.py    # Write status.json + recommendations.json
dashboard/
    index.html              # Status dashboard (dark theme, auto-refresh)
    recommendations.html    # Recommendations dashboard
tests/
    test_simulation.py      # Core engine tests
    test_mvp.py             # Full stack tests
data/                       # Runtime state (gitignored)
    key_registry.json
    key_health.json
.github/workflows/
    daily-maintenance.yml   # Cron job: research + email + dashboard
    deploy-pages.yml        # Deploy dashboard to GitHub Pages
```

## Roadmap

- [x] Core key pool engine with rotation
- [x] Health tracking and auto-disable
- [x] Configuration system
- [x] Simulation tests
- [x] Provider adapters (GitHub Models, Groq, OpenRouter)
- [x] HTTP API gateway (FastAPI)
- [x] Dashboard (status + recommendations pages)
- [x] Daily maintenance automation (GitHub Actions)
- [x] Email summaries via SMTP
- [x] AI research integration
- [x] Full test coverage (17 tests passing)
- [ ] Anthropic / OpenAI native adapters
- [ ] Rate limiting / throttling
- [ ] API key cost tracking

## License

MIT License. See [LICENSE](LICENSE) for details.
