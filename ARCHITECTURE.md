# AI Key Pool — Architecture Guide

A complete guide to understanding the AI Key Pool system.

---

## Table of Contents

1. [High-Level Architecture](#1-high-level-architecture)
2. [Folder Structure](#2-folder-structure)
3. [Module Dependency Diagram](#3-module-dependency-diagram)
4. [Request Lifecycle](#4-request-lifecycle)
5. [Key Rotation Lifecycle](#5-key-rotation-lifecycle)
6. [Daily Maintenance Lifecycle](#6-daily-maintenance-lifecycle)
7. [Dashboard Generation Lifecycle](#7-dashboard-generation-lifecycle)
8. [Provider Integration Architecture](#8-provider-integration-architecture)
9. [Security Model](#9-security-model)
10. [Configuration Model](#10-configuration-model)
11. [GitHub Actions Workflows](#11-github-actions-workflows)
12. [Deployment Guide](#12-deployment-guide)
13. [Sequence Diagrams](#13-sequence-diagrams)
14. [Extension Guide](#14-extension-guide)
15. [Known Limitations](#15-known-limitations)
16. [Future Roadmap](#16-future-roadmap)

---

## 1. High-Level Architecture

AI Key Pool is a provider-agnostic API key management system with automatic rotation. It is composed of five layers:

```
┌──────────────────────────────────────────────────────────────────┐
│                     Presentation Layer                           │
│  GitHub Pages Dashboard (static HTML + JSON)                     │
│  Email Summaries (SMTP HTML)                                     │
├──────────────────────────────────────────────────────────────────┤
│                        API Layer                                 │
│  FastAPI HTTP Service                                            │
│  POST /chat  POST /rotate  GET /status  GET /health             │
│  Master Key authentication (Bearer token)                        │
├──────────────────────────────────────────────────────────────────┤
│                      Core Engine Layer                           │
│  KeyRotator    — automatic rotation with retry                   │
│  KeyManager    — key selection, lifecycle, auto-disable           │
│  KeyRegistry   — persistent key storage, status tracking          │
│  HealthChecker — consecutive failure tracking, health states      │
├──────────────────────────────────────────────────────────────────┤
│                    Provider Adapter Layer                         │
│  BaseProvider (ABC)  →  chat(), health_check(), classify()       │
│  GitHubModelsProvider | GroqProvider | OpenRouterProvider         │
├──────────────────────────────────────────────────────────────────┤
│                     Infrastructure Layer                          │
│  Config (env vars + JSON)  |  Logger (structured)  |  Data (JSON)│
└──────────────────────────────────────────────────────────────────┘
```

**Key design principles:**
- Core engine has zero external dependencies (stdlib only)
- Provider adapters are decoupled from the key management logic
- All secrets live in environment variables — never hardcoded
- Persistence is JSON files in `data/` — no database required
- The HTTP API never exposes provider API keys to clients

---

## 2. Folder Structure

```
ai-key-pool/
├── src/
│   ├── __init__.py                  # Package root
│   │
│   ├── key_pool/                    # Core engine (stdlib only)
│   │   ├── __init__.py              # Exports: KeyManager, KeyRotator, RotationResult, etc.
│   │   ├── key_registry.py          # Key storage, status enum, JSON persistence
│   │   ├── key_manager.py           # High-level interface: select, mark, disable
│   │   └── key_rotator.py           # Rotation logic, retry loop, error classification
│   │
│   ├── health/
│   │   ├── __init__.py              # Exports: HealthChecker, KeyHealth, HealthStatus
│   │   └── health_checker.py        # Consecutive failure tracking, health states
│   │
│   ├── utils/
│   │   ├── __init__.py              # Exports: Config, load_config, get_logger
│   │   ├── config.py                # Env-var config, ProviderConfig, load_config()
│   │   └── logger.py                # Structured logging, event helpers
│   │
│   ├── providers/                   # Provider adapters (requires httpx)
│   │   ├── __init__.py
│   │   ├── base_provider.py         # BaseProvider ABC, ChatMessage, ChatResponse, ProviderError
│   │   ├── github_models.py         # GitHub Models adapter (models.github.ai)
│   │   ├── groq.py                  # Groq adapter (api.groq.com)
│   │   ├── openrouter.py            # OpenRouter adapter (openrouter.ai)
│   │   └── provider_factory.py      # create_provider(), list_providers()
│   │
│   ├── api/                         # HTTP service (requires fastapi, uvicorn)
│   │   ├── __init__.py
│   │   ├── app.py                   # FastAPI factory with lifespan
│   │   ├── auth.py                  # Bearer token verification
│   │   ├── models.py                # Pydantic request/response schemas
│   │   └── routes.py                # API endpoints
│   │
│   └── maintenance/                 # Daily automation
│       ├── __init__.py
│       ├── orchestrator.py          # 5-step daily cycle
│       ├── research.py              # AI research via KeyRotator
│       ├── email_sender.py          # SMTP daily summary
│       └── dashboard_gen.py         # Writes status.json + recommendations.json
│
├── dashboard/                       # GitHub Pages (static)
│   ├── index.html                   # Status dashboard (dark theme, auto-refresh)
│   ├── recommendations.html         # Recommendations dashboard
│   └── data/                        # Generated by daily maintenance (committed by Actions)
│       ├── status.json
│       └── recommendations.json
│
├── tests/
│   ├── test_simulation.py           # 7 core engine tests
│   └── test_mvp.py                  # 10 full-stack tests (providers, API, dashboard)
│
├── data/                            # Runtime state (gitignored)
│   ├── key_registry.json            # Key entries and statuses
│   └── key_health.json              # Health records
│
├── .github/workflows/
│   ├── daily-maintenance.yml        # Cron: research + email + dashboard
│   └── deploy-pages.yml             # Deploy dashboard to GitHub Pages
│
├── requirements.txt                 # httpx, fastapi, uvicorn
├── .gitignore
├── LICENSE                          # MIT
├── CHANGELOG.md
├── CONTRIBUTING.md
└── README.md
```

---

## 3. Module Dependency Diagram

```
                    ┌──────────────┐
                    │   config.py  │  (no internal deps)
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  logger.py   │  (no internal deps)
                    └──────┬───────┘
                           │
         ┌─────────────────┼─────────────────┐
         │                 │                 │
┌────────▼────────┐ ┌──────▼───────┐ ┌───────▼───────┐
│ key_registry.py │ │health_checker│ │base_provider  │
│   (no internal  │ │   .py        │ │   .py         │
│    deps)        │ │(no internal) │ │(lazy httpx)   │
└────────┬────────┘ └──────┬───────┘ └───────┬───────┘
         │                 │                 │
         └────────┬────────┘                 │
                  │                          │
           ┌──────▼───────┐          ┌───────▼───────┐
           │ key_manager  │          │ provider_     │
           │    .py       │          │  factory.py   │
           └──────┬───────┘          └───────────────┘
                  │
           ┌──────▼───────┐
           │ key_rotator  │
           │    .py       │
           └──────┬───────┘
                  │
    ┌─────────────┼─────────────┐
    │             │             │
┌───▼───┐  ┌─────▼─────┐ ┌────▼────┐
│routes │  │orchestrat  │ │research │
│  .py  │  │  or.py     │ │  .py    │
└───┬───┘  └─────┬─────┘ └─────────┘
    │            │
┌───▼───┐  ┌────▼──────────┐
│app.py │  │email_sender   │
└───────┘  │  .py          │
           │dashboard_gen  │
           │  .py          │
           └───────────────┘
```

**Import rules:**
- Core engine (`key_pool/`, `health/`, `utils/`) never imports from `providers/`, `api/`, or `maintenance/`
- `providers/` never imports from `key_pool/`, `api/`, or `maintenance/`
- `api/` imports from `key_pool/`, `providers/`, and `utils/`
- `maintenance/` imports from `key_pool/`, `providers/`, and `utils/`
- No circular imports exist between any layers

---

## 4. Request Lifecycle

When a client sends `POST /chat`:

```
Client                    API (FastAPI)              Core Engine           Provider
  │                           │                         │                    │
  │  POST /chat               │                         │                    │
  │  Authorization: Bearer    │                         │                    │
  │  {provider, model, msgs}  │                         │                    │
  │──────────────────────────>│                         │                    │
  │                           │                         │                    │
  │  1. Verify Bearer token   │                         │                    │
  │     against master key    │                         │                    │
  │                           │                         │                    │
  │  2. Validate messages[]   │                         │                    │
  │     (role + content)      │                         │                    │
  │                           │                         │                    │
  │  3. Create provider       │                         │                    │
  │     adapter by name       │──────────────────────────────────────────────>│
  │                           │                         │                    │
  │  4. Execute with rotation │                         │                    │
  │     ─────────────────────>│                         │                    │
  │                           │  get_next_key(provider) │                    │
  │                           │──────────────────────>  │                    │
  │                           │  ◄── KeyEntry           │                    │
  │                           │                         │                    │
  │                           │  provider.chat(key,     │                    │
  │                           │    model, messages)     │                    │
  │                           │─────────────────────────────────────────────>│
  │                           │                         │    HTTP POST       │
  │                           │                         │    to endpoint     │
  │                           │                         │  ◄── ChatResponse  │
  │                           │  ◄── response           │                    │
  │                           │                         │                    │
  │                           │  mark_success(key_id)   │                    │
  │                           │──────────────────────>  │                    │
  │                           │                         │                    │
  │  5. Return response       │                         │                    │
  │  ◄───────────────────────│                         │                    │
  │  {success, content,       │                         │                    │
  │   model, provider,        │                         │                    │
  │   key_id, rotations}      │                         │                    │
```

**Error handling:**
- Invalid provider name → `ChatResponse(success=False, error="Unknown provider...")`
- Missing message fields → HTTP 422 with validation error
- Provider HTTP error → KeyRotator classifies error, may rotate
- No healthy keys → `ChatResponse(success=False, error="No healthy keys...")`
- Invalid master key → HTTP 401
- Unconfigured master key → HTTP 503

---

## 5. Key Rotation Lifecycle

The `KeyRotator.execute_with_rotation()` method is the heart of the system:

```
execute_with_rotation(provider, request_fn, max_retries)
│
├─ failed_key_ids = []
├─ attempts = 0
│
├─ LOOP while attempts <= max_retries:
│   │
│   ├─ get_next_key(provider, exclude=failed_key_ids)
│   │  └─ Returns first ACTIVE key not in exclude list
│   │  └─ Returns None if no healthy keys remain
│   │
│   ├─ If key is None:
│   │  └─ RETURN RotationResult(success=False, error="No healthy keys...")
│   │
│   ├─ If failed_key_ids is not empty:
│   │  └─ rotations += 1 (track rotation count)
│   │
│   ├─ request_fn(key.key_value)
│   │  │
│   │  ├─ SUCCESS:
│   │  │  ├─ mark_success(key_id)
│   │  │  │  ├─ registry.record_usage(success=True)
│   │  │  │  │  └─ success_count += 1, reset failure_count if EXHAUSTED
│   │  │  │  └─ health_checker.record_success()
│   │  │  │     └─ consecutive_failures = 0, status = HEALTHY
│   │  │  └─ RETURN RotationResult(success=True, response=...)
│   │  │
│   │  └─ EXCEPTION:
│   │     ├─ _classify_error(e) → error_type
│   │     │  └─ Substring matching: "rate_limit", "quota_exhausted", "auth_error", "unknown"
│   │     │
│   │     ├─ mark_failure(key_id, error_type)
│   │     │  ├─ registry.record_usage(success=False)
│   │     │  │  └─ failure_count += 1
│   │     │  ├─ health_checker.record_failure()
│   │     │  │  └─ consecutive_failures += 1
│   │     │  │     ├─ >= 5 → UNHEALTHY
│   │     │  │     ├─ >= 2 → DEGRADED
│   │     │  │     └─ else → HEALTHY
│   │     │  └─ Auto-disable if consecutive_failures >= max_consecutive_failures
│   │     │
│   │     ├─ failed_key_ids.append(key_id)
│   │     │
│   │     ├─ If should_rotate(error_type):
│   │     │  └─ continue (try next key)
│   │     │
│   │     └─ Else (non-rotation error):
│   │        └─ RETURN RotationResult(success=False, error=str(e))
│   │
│   └─ attempts += 1
│
└─ RETURN RotationResult(success=False, error="Max retries exceeded...")
```

**Error types that trigger rotation:**
| Error Type | Trigger Condition |
|------------|-------------------|
| `rate_limit` | HTTP 429 or "rate limit" in message |
| `quota_exhausted` | HTTP 402/403 with "quota" or "exceeded" |
| `auth_error` | HTTP 401/403 or "auth"/"invalid" in message |
| `unknown` | Does NOT trigger rotation (fails immediately) |

---

## 6. Daily Maintenance Lifecycle

The `orchestrator.run_daily_maintenance()` function runs a 5-step cycle:

```
run_daily_maintenance()
│
├─ Initialize: load_config(), KeyManager(data_dir)
├─ Initialize: stats = {total_keys: 0, by_status: {}}
│
├─ Step 1: Health Check
│  ├─ key_manager.get_all_stats()
│  ├─ Record: total_keys, by_status
│  └─ On error: log, append to errors[], continue
│
├─ Step 2: Generate Status Report
│  ├─ dashboard_gen.generate_status_json(key_manager, config, dashboard/data/)
│  │  ├─ Build status dict with active provider, key counts, timestamps
│  │  ├─ Write dashboard/data/status.json
│  │  └─ Log: "Generated status.json — N keys"
│  └─ On error: log, append to errors[], continue
│
├─ Step 3: Run AI Research
│  ├─ research.research_providers(config, key_manager, history_path)
│  │  ├─ Create provider adapter for active_provider
│  │  ├─ Build research prompt (system + user messages)
│  │  ├─ Execute via KeyRotator (auto-rotates on quota)
│  │  │  └─ request_fn = provider.chat(api_key, "gpt-4o-mini", messages)
│  │  ├─ Parse JSON response into findings dict
│  │  ├─ Merge with research_history.json (keep last 30 days)
│  │  └─ Return: {findings: [...], summary: "..."}
│  └─ On error: set research_data = {findings: [], summary: "Research failed"}
│
├─ Step 4: Generate Recommendations
│  ├─ dashboard_gen.generate_recommendations_json(research_data, dashboard/data/)
│  │  ├─ Categorize findings: providers, free_tiers, models, changes
│  │  ├─ Build recommendations list (high/medium priority)
│  │  ├─ Write dashboard/data/recommendations.json
│  │  └─ Log: "Generated recommendations.json — N findings"
│  └─ On error: log, append to errors[], continue
│
├─ Step 5: Send Email Summary
│  ├─ Build status_data dict from stats
│  ├─ email_sender.send_daily_summary(smtp_host, ..., status, recommendations, errors)
│  │  ├─ Skip if SMTP not configured (returns False)
│  │  ├─ Build HTML body with health, research, errors
│  │  ├─ Send via SMTP with TLS
│  │  └─ Return True/False
│  └─ On error: log, append to errors[], continue
│
├─ Return: {timestamp, steps: {...}, errors: [...], status: "completed"|"completed_with_errors"}
```

**When invoked via GitHub Actions:**
1. Python runs `python -m src.maintenance.orchestrator`
2. Script calls `run_daily_maintenance()` and prints JSON result
3. Git commit step picks up `dashboard/data/*.json`
4. Committed files are served by GitHub Pages

---

## 7. Dashboard Generation Lifecycle

The dashboard consists of static HTML files that fetch JSON data at runtime.

### status.json Generation

```
generate_status_json(key_manager, config, output_path)
│
├─ key_manager.get_all_stats()
│  └─ Returns: {registry: {total_keys, by_status, by_provider}, health: {...}}
│
├─ key_manager.get_active_key(config.active_provider)
│  └─ Returns: KeyEntry or None
│
├─ Find most recent success/failure across all keys
│  └─ Iterate registry.keys, compare timestamps
│
├─ Build providers dict
│  └─ For each provider: key_manager.get_provider_summary(name)
│
├─ Assemble status dict:
│  {
│    active_provider: "groq",
│    active_key: {key_id, provider, status} | null,
│    total_keys: N,
│    healthy_keys: N,
│    exhausted_keys: N,
│    disabled_keys: N,
│    last_success: "ISO timestamp",
│    last_failure: "ISO timestamp",
│    last_update: "ISO timestamp",
│    providers: {name: {total_keys, healthy_keys, keys: [...]}}
│  }
│
└─ Write to output_path / "status.json"
```

### recommendations.json Generation

```
generate_recommendations_json(research_data, output_path)
│
├─ Extract findings from research_data
│
├─ Categorize by type:
│  ├─ new_providers  = [f for f in findings if type == "provider"]
│  ├─ free_tiers     = [f for f in findings if type == "free_tier"]
│  ├─ new_models     = [f for f in findings if type == "model"]
│  └─ provider_changes = [f for f in findings if type == "change"]
│
├─ Build recommendations list:
│  └─ For each finding with action "add_key" or "monitor":
│     {priority: "high"|"medium", action: description, reason: name}
│
└─ Write to output_path / "recommendations.json"
```

### Dashboard HTML Flow

```
Browser loads index.html
│
├─ Show "Loading status..."
├─ fetch('data/status.json')
│  ├─ On success:
│  │  ├─ Hide loading, show content
│  │  ├─ Populate: total, healthy, exhausted, disabled
│  │  ├─ Populate: active provider, active key
│  │  ├─ Build provider table with status badges
│  │  └─ Show last update timestamp
│  └─ On failure:
│     └─ Show "No status data available. Run daily maintenance first."
│
└─ Auto-refresh every 5 minutes (setInterval 300000ms)
```

---

## 8. Provider Integration Architecture

### BaseProvider ABC

All providers extend `BaseProvider` and implement three abstract methods:

```
BaseProvider (ABC)
│
├── get_provider_name() → str          # "groq", "openrouter", "github_models"
├── get_endpoint() → str               # Full chat completions URL
├── get_auth_headers(api_key) → dict   # Authorization + provider-specific headers
│
├── chat(api_key, model, messages) → ChatResponse    # Concrete
│  ├─ Build headers via get_auth_headers()
│  ├─ Build payload: {model, messages}
│  ├─ POST to get_endpoint() via httpx (60s timeout)
│  ├─ On HTTP error: _classify_http_error() → ProviderError
│  ├─ On success: parse JSON → ChatResponse
│  └─ On parse error: ProviderError("Invalid response format")
│
├── health_check(api_key, model?) → bool              # Concrete
│  └─ Calls chat() with minimal message, returns True/False
│
└── _classify_http_error(status_code, body) → str     # Concrete, overridable
   ├─ 429 → "rate_limit"
   ├─ 402/403 + "quota" → "quota_exhausted"
   ├─ 401/403 → "auth_error"
   ├─ 5xx → "provider_unavailable"
   ├─ 400 → "invalid_request"
   └─ else → "unknown_error"
```

### Provider Adapters

| Provider | Endpoint | Auth Header | Notes |
|----------|----------|-------------|-------|
| GitHub Models | `models.github.ai/inference/chat/completions` | Bearer + `X-GitHub-Api-Version` | Uses GitHub PAT |
| Groq | `api.groq.com/openai/v1/chat/completions` | Bearer | Standard OpenAI format |
| OpenRouter | `openrouter.ai/api/v1/chat/completions` | Bearer + optional `HTTP-Referer`, `X-OpenRouter-Title` | Multi-model routing |

### Provider Factory

```python
# provider_factory.py
PROVIDER_MAP = {
    "github_models": GitHubModelsProvider,
    "groq": GroqProvider,
    "openrouter": OpenRouterProvider,
}

create_provider("groq")  # → GroqProvider()
create_provider("unknown")  # → ValueError: Unknown provider: 'unknown'. Available: github_models, groq, openrouter
```

### Error Type Alignment

The provider layer and rotator layer must agree on error type strings:

| Provider `_classify_http_error` | Rotator `ROTATION_ERRORS` | Triggers Rotation |
|---------------------------------|---------------------------|-------------------|
| `rate_limit` | `rate_limit` | Yes |
| `quota_exhausted` | `quota_exhausted` | Yes |
| `auth_error` | `auth_error` | Yes |
| `provider_unavailable` | (not in set) | No |
| `invalid_request` | (not in set) | No |
| `unknown_error` | (not in set) | No |

---

## 9. Security Model

### Authentication

```
Client Request
│
├─ Must include: Authorization: Bearer <master_key>
│
├─ verify_master_key() dependency:
│  ├─ If master_key not configured → HTTP 503
│  ├─ If credentials missing → HTTP 401 (HTTPBearer auto-handles)
│  └─ If credentials != master_key → HTTP 401
│
└─ Returns: validated key string (unused, just verification)
```

### Secrets Management

| Secret | Location | Never In |
|--------|----------|----------|
| Provider API keys | `AIKEYPOOL_PROVIDER_<NAME>_KEYS` env var | Response bodies, logs, dashboard JSON |
| Master key | `AIKEYPOOL_MASTER_KEY` env var | URLs, query params |
| SMTP credentials | `SMTP_USER` / `SMTP_PASSWORD` env vars | Commit history, dashboard |

### What the API exposes

```
POST /chat response:
{
  success: true,
  content: "...",           # AI response text
  model: "llama-3.3-70b-versatile",
  provider: "groq",
  key_id: "groq-key-1",    # Key identifier (NOT the key value)
  rotations: 0
}

GET /status response:
{
  total_keys: 5,
  healthy_keys: 3,
  providers: {
    "groq": {
      total_keys: 2,
      keys: [
        {key_id: "groq-key-1", status: "active", ...}  # ID only, no value
      ]
    }
  }
}
```

**Key values are never returned by any endpoint.**

### Dashboard Security

The dashboard is a GitHub Pages static site. The `data/status.json` and `data/recommendations.json` files contain:
- Key counts and statuses (no key values)
- Active provider name
- Timestamps

These files are safe to expose publicly.

---

## 10. Configuration Model

### Loading Order

```
load_config(config_path=None)
│
├─ 1. Environment variables (primary source):
│  ├─ AIKEYPOOL_MASTER_KEY
│  ├─ AIKEYPOOL_ACTIVE_PROVIDER
│  ├─ AIKEYPOOL_RETRY_COUNT (default: 3)
│  ├─ AIKEYPOOL_MAX_CONSECUTIVE_FAILURES (default: 5)
│  ├─ AIKEYPOOL_LOG_LEVEL (default: "INFO")
│  ├─ AIKEYPOOL_DATA_DIR (default: ./data)
│  └─ AIKEYPOOL_PROVIDER_<NAME>_KEYS (comma-separated)
│
├─ 2. Optional JSON config file (overrides env vars):
│  ├─ master_key
│  ├─ active_provider
│  ├─ retry_count
│  └─ providers: {name: {keys: [...]}}
│
├─ 3. Auto-set active_provider to first provider if empty
│
└─ 4. Ensure data directory exists (mkdir -p)
```

### Config Dataclass

```python
@dataclass
class Config:
    master_key: Optional[str]       # API auth token
    providers: dict[str, ProviderConfig]  # name → {name, keys[]}
    active_provider: str            # Currently selected provider
    retry_count: int                # Max retries per request (default: 3)
    max_consecutive_failures: int   # Auto-disable threshold (default: 5)
    data_dir: Path                  # JSON persistence directory
    log_level: str                  # Logging level

@dataclass
class ProviderConfig:
    name: str                       # Provider identifier
    keys: list[str]                 # API key values
```

### Environment Variable Reference

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `AIKEYPOOL_MASTER_KEY` | string | None | Master key for API authentication |
| `AIKEYPOOL_ACTIVE_PROVIDER` | string | First provider | Currently active provider name |
| `AIKEYPOOL_RETRY_COUNT` | int | 3 | Retries before giving up |
| `AIKEYPOOL_MAX_CONSECUTIVE_FAILURES` | int | 5 | Auto-disable after N failures |
| `AIKEYPOOL_LOG_LEVEL` | string | INFO | DEBUG, INFO, WARNING, ERROR |
| `AIKEYPOOL_DATA_DIR` | path | ./data | JSON persistence directory |
| `AIKEYPOOL_PROVIDER_*_KEYS` | string | None | Comma-separated API keys |
| `SMTP_HOST` | string | None | SMTP server hostname |
| `SMTP_PORT` | int | 587 | SMTP server port |
| `SMTP_USER` | string | None | SMTP username |
| `SMTP_PASSWORD` | string | None | SMTP password |
| `EMAIL_RECIPIENT` | string | None | Email recipient address |

---

## 11. GitHub Actions Workflows

### Daily Maintenance (`daily-maintenance.yml`)

```
Trigger: cron "0 6 * * *" (06:00 UTC daily) + manual dispatch
Permissions: contents: write
│
├─ Step 1: Checkout repository
├─ Step 2: Setup Python 3.12
├─ Step 3: pip install -r requirements.txt
├─ Step 4: Run daily maintenance
│  ├─ Env: All AIKEYPOOL_* secrets, SMTP secrets
│  └─ Command: python -m src.maintenance.orchestrator
│     └─ Produces: dashboard/data/status.json, dashboard/data/recommendations.json
├─ Step 5: Commit updated dashboard data
│  ├─ git config user.name "github-actions[bot]"
│  ├─ git add dashboard/data/
│  ├─ git diff --staged --quiet || git commit -m "Daily maintenance update [skip ci]"
│  └─ git push
│
└─ Failure behavior:
   ├─ If maintenance fails → step outputs error, commit still runs
   ├─ If commit fails → push fails, workflow fails
   └─ [skip ci] prevents infinite loop
```

### Pages Deployment (`deploy-pages.yml`)

```
Trigger: push to main/master + manual dispatch
Permissions: contents: read, pages: write, id-token: write
Concurrency: group "pages", cancel-in-progress: false
│
├─ Job: deploy
│  ├─ Environment: github-pages
│  ├─ Step 1: Checkout
│  ├─ Step 2: Setup Pages (configure-pages@v5)
│  ├─ Step 3: Upload artifact (upload-pages-artifact@v3)
│  │  └─ Path: ./dashboard (includes data/ if committed)
│  └─ Step 4: Deploy to GitHub Pages (deploy-pages@v4)
│
└─ The daily maintenance workflow commits data/ files,
   which triggers this workflow to re-deploy the dashboard.
```

### Workflow Interaction

```
daily-maintenance.yml (06:00 UTC)
│
├─ Run orchestrator
├─ Generate dashboard/data/*.json
├─ git commit + push
│
└─ Triggers deploy-pages.yml (on push to main/master)
   │
   └─ Upload ./dashboard → GitHub Pages
      └─ Live at https://<user>.github.io/<repo>/
```

---

## 12. Deployment Guide

### Prerequisites

- Python 3.12+
- GitHub repository
- At least one provider API key

### Step 1: Clone and Install

```bash
git clone https://github.com/YOUR_USERNAME/ai-key-pool.git
cd ai-key-pool
pip install -r requirements.txt
```

### Step 2: Configure Secrets

```bash
# Required
export AIKEYPOOL_MASTER_KEY="your-secret-master-key"
export AIKEYPOOL_PROVIDER_GROQ_KEYS="gsk_key1,gsk_key2"

# Optional (for email)
export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USER="you@gmail.com"
export SMTP_PASSWORD="app-password"
export EMAIL_RECIPIENT="you@gmail.com"
```

### Step 3: Run Locally

```bash
# API server
uvicorn src.api.app:app --host 0.0.0.0 --port 8000

# Daily maintenance (standalone)
python -m src.maintenance.orchestrator
```

### Step 4: Deploy to GitHub

```bash
# Set repository secrets (Settings → Secrets → Actions)
# Add: AIKEYPOOL_MASTER_KEY, AIKEYPOOL_PROVIDER_GROQ_KEYS, etc.

# Enable GitHub Pages
# Settings → Pages → Source: GitHub Actions

# Push to trigger deployment
git push origin main
```

### Step 5: Verify

```bash
# Check API
curl -H "Authorization: Bearer YOUR_MASTER_KEY" http://localhost:8000/health

# Trigger first maintenance run
# Actions → Daily Maintenance → Run workflow

# Check dashboard
# https://YOUR_USERNAME.github.io/ai-key-pool/
```

### Environment Variables for GitHub Actions

Set these in Settings → Secrets → Actions:

| Secret Name | Required | Description |
|-------------|----------|-------------|
| `AIKEYPOOL_MASTER_KEY` | Yes | API authentication |
| `AIKEYPOOL_ACTIVE_PROVIDER` | Yes | Default provider (e.g., "groq") |
| `AIKEYPOOL_PROVIDER_GROQ_KEYS` | One required | Groq API keys |
| `AIKEYPOOL_PROVIDER_OPENROUTER_KEYS` | One required | OpenRouter API keys |
| `AIKEYPOOL_PROVIDER_GITHUB_MODELS_KEYS` | One required | GitHub PATs |
| `SMTP_HOST` | No | Email server |
| `SMTP_PORT` | No | SMTP port (default 587) |
| `SMTP_USER` | No | SMTP username |
| `SMTP_PASSWORD` | No | SMTP password |
| `EMAIL_RECIPIENT` | No | Report recipient |

---

## 13. Sequence Diagrams

### Chat Request

```
Client              API               KeyRotator         KeyManager        Provider
  │                  │                    │                   │                │
  │ POST /chat       │                    │                   │                │
  │ {provider,model, │                    │                   │                │
  │  messages}       │                    │                   │                │
  │─────────────────>│                    │                   │                │
  │                  │                    │                   │                │
  │                  │ verify_master_key()│                   │                │
  │                  │─── OK ────────────│                   │                │
  │                  │                    │                   │                │
  │                  │ validate messages  │                   │                │
  │                  │─── OK ────────────│                   │                │
  │                  │                    │                   │                │
  │                  │ create_provider()  │                   │                │
  │                  │────────────────────────────────────────────────────────>│
  │                  │ ◄─ GroqProvider    │                   │                │
  │                  │                    │                   │                │
  │                  │ execute_with_rotation(provider, fn)    │                │
  │                  │──────────────────>│                   │                │
  │                  │                    │ get_next_key()    │                │
  │                  │                    │──────────────────>│                │
  │                  │                    │ ◄─ KeyEntry       │                │
  │                  │                    │                   │                │
  │                  │                    │ provider.chat()   │                │
  │                  │                    │───────────────────────────────────>│
  │                  │                    │                   │   POST to API  │
  │                  │                    │ ◄─ ChatResponse   │                │
  │                  │                    │                   │                │
  │                  │                    │ mark_success()    │                │
  │                  │                    │──────────────────>│                │
  │                  │                    │                   │                │
  │                  │ ◄─ RotationResult  │                   │                │
  │                  │   {success=True,   │                   │                │
  │                  │    response=...}   │                   │                │
  │                  │                    │                   │                │
  │ ◄─ 200 OK        │                    │                   │                │
  │ {success,content,│                    │                   │                │
  │  model,provider} │                    │                   │                │
```

### Key Rotation (Rate Limit)

```
KeyRotator           KeyManager          Provider
  │                    │                    │
  │ get_next_key()     │                    │
  │──────────────────>│                    │
  │ ◄─ key-1          │                    │
  │                    │                    │
  │ provider.chat(key-1)                   │
  │───────────────────────────────────────>│
  │ ◄─ 429 Rate Limit │                    │
  │                    │                    │
  │ mark_failure(key-1, "rate_limit")      │
  │──────────────────>│                    │
  │                    │ failure_count += 1 │
  │                    │ save to disk       │
  │                    │                    │
  │ get_next_key(exclude=[key-1])          │
  │──────────────────>│                    │
  │ ◄─ key-2          │                    │
  │                    │                    │
  │ provider.chat(key-2)                   │
  │───────────────────────────────────────>│
  │ ◄─ 200 OK         │                    │
  │                    │                    │
  │ mark_success(key-2)│                    │
  │──────────────────>│                    │
  │                    │ success_count += 1 │
  │                    │ consecutive = 0    │
  │                    │                    │
  │ ◄─ RotationResult │                    │
  │   {success=True,  │                    │
  │    key_used=key-2,│                    │
  │    rotations=1}   │                    │
```

### Daily Maintenance

```
GitHub Actions       orchestrator         research            dashboard_gen       email_sender
  │                    │                    │                    │                    │
  │ python -m          │                    │                    │                    │
  │ src.maintenance    │                    │                    │                    │
  │──────────────────>│                    │                    │                    │
  │                    │                    │                    │                    │
  │                    │ load_config()      │                    │                    │
  │                    │ KeyManager()       │                    │                    │
  │                    │                    │                    │                    │
  │                    │ STEP 1: Health     │                    │                    │
  │                    │ get_all_stats()    │                    │                    │
  │                    │─── OK ────────────│                    │                    │
  │                    │                    │                    │                    │
  │                    │ STEP 2: Status     │                    │                    │
  │                    │ generate_status_json()                  │                    │
  │                    │──────────────────────────────────────>│                    │
  │                    │                    │                    │ write status.json  │
  │                    │ ◄─ OK ─────────────────────────────────│                    │
  │                    │                    │                    │                    │
  │                    │ STEP 3: Research   │                    │                    │
  │                    │ research_providers()                    │                    │
  │                    │─────────────────>│                    │                    │
  │                    │                    │ create_provider()  │                    │
  │                    │                    │ rotator.execute()  │                    │
  │                    │                    │ (calls AI API)     │                    │
  │                    │                    │ parse JSON         │                    │
  │                    │ ◄─ findings ─────│                    │                    │
  │                    │                    │                    │                    │
  │                    │ STEP 4: Recs       │                    │                    │
  │                    │ generate_recommendations_json()         │                    │
  │                    │──────────────────────────────────────>│                    │
  │                    │                    │                    │ write recs.json    │
  │                    │ ◄─ OK ─────────────────────────────────│                    │
  │                    │                    │                    │                    │
  │                    │ STEP 5: Email      │                    │                    │
  │                    │ send_daily_summary()                    │                    │
  │                    │─────────────────────────────────────────────────────────>│
  │                    │                    │                    │ SMTP send          │
  │                    │ ◄─ True/False ────────────────────────────────────────────│
  │                    │                    │                    │                    │
  │ ◄─ JSON result     │                    │                    │                    │
  │                    │                    │                    │                    │
  │ git add dashboard/data/                │                    │                    │
  │ git commit + push  │                    │                    │                    │
  │ (triggers Pages deploy)                │                    │                    │
```

---

## 14. Extension Guide

### Adding a New Provider

**1. Create the adapter file:**

```python
# src/providers/my_provider.py

from .base_provider import BaseProvider

class MyProvider(BaseProvider):
    def get_provider_name(self) -> str:
        return "my_provider"

    def get_endpoint(self) -> str:
        return "https://api.myprovider.com/v1/chat/completions"

    def get_auth_headers(self, api_key: str) -> dict:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
```

**2. Register in the factory:**

```python
# src/providers/provider_factory.py

from .my_provider import MyProvider

PROVIDER_MAP = {
    "github_models": GitHubModelsProvider,
    "groq": GroqProvider,
    "openrouter": OpenRouterProvider,
    "my_provider": MyProvider,          # ← Add this
}
```

**3. Add tests:**

```python
# tests/test_mvp.py

def test_my_provider():
    p = create_provider("my_provider")
    assert isinstance(p, MyProvider)
    assert p.get_provider_name() == "my_provider"
    assert "myprovider.com" in p.get_endpoint()
```

**4. Configure keys:**

```bash
export AIKEYPOOL_PROVIDER_MY_PROVIDER_KEYS="key1,key2"
```

**No changes needed in the core engine, API routes, or maintenance scripts.**

### Adding a New Dashboard Page

**1. Create the HTML file:**

```html
<!-- dashboard/my_page.html -->
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>AI Key Pool — My Page</title>
    <style>/* Copy styles from index.html */</style>
</head>
<body>
    <div class="container">
        <div class="nav">
            <a href="index.html">Status</a>
            <a href="recommendations.html">Recommendations</a>
            <a href="my_page.html">My Page</a>
        </div>
        <h1>My Custom Page</h1>
        <div id="content">Loading...</div>
    </div>
    <script>
        async function loadData() {
            const resp = await fetch('data/my_data.json');
            const d = await resp.json();
            // Render your data
        }
        loadData();
    </script>
</body>
</html>
```

**2. Add navigation links to existing pages** (update the `.nav` divs in `index.html` and `recommendations.html`).

**3. Generate the JSON data** in `orchestrator.py` or a new generator module.

### Adding a New Maintenance Task

**1. Create the task module:**

```python
# src/maintenance/my_task.py

from ..utils.logger import get_logger

logger = get_logger("my_task")

def run_my_task(config, key_manager) -> dict:
    """Run custom maintenance task."""
    # Your logic here
    logger.info("My task completed")
    return {"status": "ok"}
```

**2. Add to orchestrator:**

```python
# src/maintenance/orchestrator.py

from .my_task import run_my_task

def run_daily_maintenance() -> dict:
    # ... existing steps ...

    # Step N: My Task
    logger.info("Step N: Run my task")
    try:
        my_result = run_my_task(config, key_manager)
        results["steps"]["my_task"] = {"status": "ok"}
    except Exception as e:
        logger.error("My task failed: %s", e)
        errors.append(f"My task: {e}")
        results["steps"]["my_task"] = {"status": "error", "error": str(e)}
```

---

## 15. Known Limitations

| Limitation | Impact | Workaround |
|------------|--------|------------|
| **No connection pooling** — `httpx.Client` is created per request in `BaseProvider.chat()` | Cold start latency on each provider call | Acceptable for low-throughput; use a reverse proxy for high-throughput |
| **Dual JSON persistence** — `key_registry.json` and `key_health.json` written separately | 2 disk writes per key operation | Acceptable for small key counts (<100) |
| **Global mutable state** — API routes use module-level globals for manager/rotator | Cannot run multi-worker uvicorn | Use single worker or externalize state |
| **No rate limiting** — API has no request throttling | Vulnerable to abuse | Deploy behind a rate-limiting reverse proxy |
| **Research uses AI** — Daily research queries an AI provider | Costs tokens, may return hallucinated findings | Review findings manually; set low max_tokens |
| **Single-process maintenance** — No locking on `data/*.json` | Race condition if run concurrently | Only run via GitHub Actions (single runner) |
| **Health check uses model "gpt-4o-mini"** — Fallback default may not exist on all providers | `health_check()` may fail on some providers | Pass explicit model parameter |
| **No HTTPS in local dev** — uvicorn serves HTTP | Credentials sent in clear text | Use HTTPS reverse proxy in production |
| **Dashboard data is stale** — Updated once daily by cron | Status may be up to 24 hours old | Trigger manual maintenance run for fresh data |
| **Email via SMTP** — No retry on transient failures | May miss daily reports | Monitor workflow runs for failures |

---

## 16. Future Roadmap

### v1.1.0 — Provider Improvements
- [ ] Anthropic native adapter (Messages API)
- [ ] OpenAI native adapter ( Responses API)
- [ ] Configurable HTTP timeout per provider
- [ ] Connection pooling (shared `httpx.Client`)
- [ ] Use `ProviderError.error_type` directly in rotator (remove substring matching)

### v1.2.0 — Operational Improvements
- [ ] Consolidate `key_registry.json` and `key_health.json` into single file
- [ ] Rate limiting middleware for API
- [ ] Request logging middleware (latency, key used, provider)
- [ ] Health check endpoint for each provider (not just overall)
- [ ] Prometheus metrics export

### v1.3.0 — Dashboard Improvements
- [ ] Real-time dashboard via WebSocket
- [ ] Key usage charts (success/failure over time)
- [ ] Provider comparison view
- [ ] Cost tracking per provider
- [ ] Dark/light theme toggle

### v2.0.0 — Advanced Features
- [ ] Multi-user support with per-user key quotas
- [ ] Key pooling across organizations
- [ ] Automatic key rotation on provider side (detect key expiry)
- [ ] Webhook notifications (Slack, Discord)
- [ ] CLI management tool
- [ ] Docker containerization
- [ ] Kubernetes operator

---

*This document was generated from the AI Key Pool v1.0.0 codebase.*
