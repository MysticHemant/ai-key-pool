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
9. [Dynamic Provider System](#9-dynamic-provider-system)
10. [Multi-Agent Research](#10-multi-agent-research)
11. [Provider Discovery](#11-provider-discovery)
12. [Historical Intelligence](#12-historical-intelligence)
13. [Security Model](#13-security-model)
14. [Configuration Model](#14-configuration-model)
15. [GitHub Actions Workflows](#15-github-actions-workflows)
16. [Deployment Guide](#16-deployment-guide)
17. [Sequence Diagrams](#17-sequence-diagrams)
18. [Extension Guide](#18-extension-guide)
19. [Known Limitations](#19-known-limitations)
20. [Future Roadmap](#20-future-roadmap)

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
│   ├── startup/                     # Initialization and key loading
│   │   ├── __init__.py              # Exports: load_provider_keys, get_configured_providers
│   │   └── key_loader.py            # AIKEYPOOL_PROVIDER_KEYS JSON support
│   │
│   ├── utils/
│   │   ├── __init__.py              # Exports: Config, load_config, get_logger
│   │   ├── config.py                # Env-var config, ProviderConfig, load_config()
│   │   └── logger.py                # Structured logging, event helpers
│   │
│   ├── providers/                   # Provider adapters and dynamic system
│   │   ├── __init__.py              # Exports: manifest_registry, CapabilityRouter, FallbackChain
│   │   ├── base_provider.py         # BaseProvider ABC, ChatMessage, ChatResponse, ProviderError
│   │   ├── manifest.py              # ProviderManifest, ManifestRegistry (global singleton)
│   │   ├── capability_router.py     # Capability-based routing across providers
│   │   ├── fallback_chain.py        # 3-phase fallback with deterministic fallback
│   │   ├── github_models.py         # GitHub Models adapter (models.github.ai)
│   │   ├── groq.py                  # Groq adapter (api.groq.com)
│   │   ├── gemini.py                # Gemini native API adapter (generativelanguage.googleapis.com)
│   │   ├── openrouter.py            # OpenRouter adapter (openrouter.ai)
│   │   ├── provider_factory.py      # create_provider(), list_providers()
│   │   └── plugins/                 # Plugin system for generic providers
│   │       ├── __init__.py
│   │       ├── generic_openai.py    # OpenAI-compatible adapter
│   │       └── loader.py            # Plugin discovery and loading
│   │
│   ├── api/                         # HTTP service (requires fastapi, uvicorn)
│   │   ├── __init__.py
│   │   ├── app.py                   # FastAPI factory with lifespan
│   │   ├── auth.py                  # Bearer token verification
│   │   ├── models.py                # Pydantic request/response schemas
│   │   └── routes.py                # API endpoints
│   │
│   └── maintenance/                 # Daily automation and intelligence
│       ├── __init__.py
│       ├── orchestrator.py          # Multi-step daily cycle with research loop
│       ├── research.py              # AI research via KeyRotator + fallback chain
│       ├── email_sender.py          # SMTP daily summary
│       ├── dashboard_gen.py         # Writes status.json + recommendations.json
│       ├── agents.py                # Multi-agent research (6 roles)
│       ├── discovery.py             # GitHub provider discovery
│       ├── history_tracker.py       # Historical intelligence tracking
│       └── report_sections.py       # Executive report with 12 sections
│
├── dashboard/                       # GitHub Pages (static)
│   ├── index.html                   # Status dashboard (dark theme, auto-refresh)
│   ├── recommendations.html         # Recommendations dashboard
│   └── data/                        # Generated by daily maintenance (committed by Actions)
│       ├── status.json
│       └── recommendations.json
│
├── tests/
│   ├── test_mvp.py                  # 10 full-stack tests (providers, API, dashboard)
│   ├── test_maintenance.py          # 40+ tests (maintenance, providers, startup)
│   ├── test_manifest.py             # Manifest registry tests
│   ├── test_capability_router.py    # Capability routing tests
│   ├── test_fallback_chain.py       # Fallback chain tests
│   ├── test_agents.py               # Multi-agent research tests
│   ├── test_discovery.py            # Provider discovery tests
│   ├── test_history_tracker.py      # History tracking tests
│   ├── test_report_sections.py      # Executive report section tests
│   ├── test_runtime_manager.py      # Runtime manager and orchestrator integration tests
│   └── test_simulation.py           # Full simulation tests
│
├── data/                            # Runtime state (gitignored)
│   ├── key_registry.json            # Key entries and statuses
│   ├── key_health.json              # Health records
│   ├── intelligence_history.json    # Historical intelligence tracking
│   └── discovery_results.json       # Latest provider discovery results
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
           │ key_manager  │          │  manifest.py  │
           │    .py       │          │ (global       │
           └──────┬───────┘          │  singleton)   │
                  │                  └───────┬───────┘
           ┌──────▼───────┐                 │
           │ key_rotator  │    ┌─────────────┤
           │    .py       │    │             │
           └──────┬───────┘ ┌──▼──────────┐ ┌▼────────────────┐
                  │         │capability_  │ │provider_factory │
    ┌─────────────┤         │router.py    │ │   .py           │
    │             │         └──────┬──────┘ └────────────────┘
    │             │                │
┌───▼───┐  ┌─────▼─────┐  ┌──────▼──────┐
│routes │  │orchestrat  │  │fallback_    │
│  .py  │  │  or.py     │  │chain.py     │
└───┬───┘  └─────┬─────┘  └─────────────┘
    │            │
    │      ┌─────┼──────────┬──────────────┐
    │      │     │          │              │
    │  ┌───▼──┐ ┌▼────────┐┌▼───────────┐ ┌▼──────────┐
    │  │report│ │discovery ││history_    │ │agents.py  │
    │  │_sects│ │  .py     ││tracker.py  │ │(6 roles)  │
    │  └──────┘ └─────────┘└────────────┘ └───────────┘
    │
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
- `providers/` can import from `providers/plugins/` and `providers/manifest.py`
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

The `orchestrator.run_daily_maintenance()` function runs a multi-step daily cycle:

```
run_daily_maintenance()
│
├─ Initialize: load_config(), KeyManager(data_dir), RuntimeManager
│
├─ Step 0a: validate_config()
├─ Step 0b: load_provider_keys(config)          ← AIKEYPOOL_PROVIDER_KEYS JSON
├─ Step 0c: sync_provider_keys(config, registry) ← sync keys to registry
├─ Step 0d: list_providers() + get_provider_status() ← provider discovery
├─ Diagnostics: _log_startup_diagnostics()
│
├─ Step 1: Health Check
│  ├─ key_manager.get_all_stats()
│  └─ Record: total_keys, by_status
│
├─ Step 1b: GitHub Provider Discovery
│  ├─ discover_providers(config)     ← fetch from zukixa/cool-ai-stuff, cheahjs/free-llm-api-resources
│  └─ save_discovery_results()       ← write to data/discovery_results.json
│
├─ Step 1c: Historical Intelligence
│  ├─ HistoryTracker.update_provider() for each provider
│  └─ HistoryTracker.record_discovery() for new discoveries
│
├─ Step 2: Research Loop (continuous iterations)
│  ├─ _run_single_iteration():
│  │  ├─ generate_research_plan()     ← AI generates targeted questions
│  │  ├─ research_providers()         ← multi-agent research with capability routing
│  │  │  ├─ MultiAgentOrchestrator.run_research_pipeline()
│  │  │  │  ├─ RESEARCHER agent      ← capability routing (reasoning/coding)
│  │  │  │  ├─ EVIDENCE_COLLECTOR    ← validates claims
│  │  │  │  ├─ VERIFIER              ← cross-references findings
│  │  │  │  ├─ CONTRADICTION_DETECTOR ← detects conflicts
│  │  │  │  ├─ CRITIC                ← quality control
│  │  │  │  └─ WRITER                ← executive summary
│  │  │  └─ FallbackChain: capability → all providers → deterministic
│  │  ├─ compress_memory() if needed
│  │  └─ Queue management + repetition detection
│  └─ Loop continues until safety limits reached
│
├─ Step 3: Final Report
│  └─ generate_final_report()
│     └─ build_executive_report()     ← 12-section executive report
│
├─ Step 4: Dashboard Status
│  └─ generate_status_json()
│
├─ Step 5: Recommendations (smart filtering)
│  └─ generate_recommendations_json(research_data, ..., configured_providers, discovery_results)
│     └─ Filters out findings for already-configured providers
│     └─ Includes suggested_providers from discovery
│
├─ Step 6: Email Delivery (Executive Intelligence Briefing)
│  └─ _do_send_email() → send_daily_summary()
│     ├─ Build executive briefing HTML with:
│     │  ├─ Executive Summary (What changed? Why? What to do?)
│     │  ├─ Top Developments (new providers, models, rate limits)
│     │  ├─ Capability Gap Analysis (Reasoning/Vision/Long Context/Coding coverage)
│     │  ├─ Key Health table (status/failure reason/reliability/action)
│     │  ├─ Verified Findings (deduplicated)
│     │  ├─ Contradictions (conflicting claims)
│     │  └─ Action Items (max 5, prioritized)
│     ├─ Shows "Research unavailable" with reason when data missing
│     ├─ Never repeats findings, no raw markdown
│     └─ Metadata: generation time, iterations, sources, providers tracked
│
├─ Step 7: Archive Cycle
│  └─ runtime_manager.archive_cycle()
│
├─ Write last_maintenance.json
└─ Return: {timestamp, steps: {...}, errors: [...], status: "completed"|"completed_with_errors"}
```

**Research loop safety limits:**
- Max iterations: configurable (default stops when research plan is empty)
- Max runtime: session timeout
- Max API budget: token limit protection
- Repetition detection: tracks previous findings to avoid loops

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
generate_recommendations_json(research_data, output_path, configured_providers, discovery_results)
│
├─ Extract findings from research_data
│
├─ Categorize by type:
│  ├─ new_providers  = [f for f in findings if type == "provider"]
│  ├─ free_tiers     = [f for f in findings if type == "free_tier"]
│  ├─ new_models     = [f for f in findings if type == "model"]
│  └─ provider_changes = [f for f in findings if type == "change"]
│
├─ Smart filtering:
│  ├─ Filter out findings for already-configured providers
│  └─ Add suggested_providers from discovery_results
│
├─ Build recommendations list:
│  └─ For each finding with action "add_key" or "monitor":
│     {priority: "high"|"medium", action: description, reason: name}
│
├─ Add configured_providers section:
│  └─ For each configured provider: {name, status, key_count}
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
├── get_manifest() → ProviderManifest  # Provider capabilities and metadata
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

| Provider | Endpoint | Auth Header | Capabilities | Priority |
|----------|----------|-------------|--------------|----------|
| GitHub Models | `models.github.ai/inference/chat/completions` | Bearer + `X-GitHub-Api-Version` | reasoning, coding | 3 |
| Groq | `api.groq.com/openai/v1/chat/completions` | Bearer | fast_inference, reasoning, coding | 1 |
| Gemini | `generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` | API Key (query param) | reasoning, coding, long_context, vision | 2 |
| OpenRouter | `openrouter.ai/api/v1/chat/completions` | Bearer + optional headers | reasoning, coding, long_context, vision | 5 |

### Provider Factory (Dynamic)

```python
# provider_factory.py — no hardcoded PROVIDER_MAP
def create_provider(provider_name: str, **kwargs) -> BaseProvider
    # 1. Check builtin adapters (GitHub Models, Groq, Gemini, OpenRouter)
    # 2. Check manifest registry for generic providers
    # 3. Fall back to env var discovery
    # Raises ValueError if unknown

def list_providers() -> list[str]
    # Returns all registered provider IDs from manifest registry

def get_provider_status() -> dict[str, dict]
    # Returns {name: {adapter, display_name, capabilities, priority, health, enabled}}
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

## 9. Dynamic Provider System

The manifest system enables zero-code-change provider additions.

### ProviderManifest

Each provider declares its capabilities via a `ProviderManifest`:

```python
@dataclass
class ProviderManifest:
    provider_id: str              # "groq", "together", etc.
    display_name: str             # "Groq", "Together AI"
    adapter: str                  # "builtin", "generic", or module path
    supported_models: list[str]   # ["llama-3.3-70b", "mixtral-8x7b"]
    capabilities: list[str]       # ["reasoning", "coding", "fast_inference"]
    priority: int                 # 1 (highest) → 10 (lowest)
    health: str                   # "healthy", "degraded", "unhealthy", "unknown"
    enabled: bool                 # True/False
    endpoint: str                 # "https://api.groq.com/..."
    default_model: str            # "llama-3.3-70b-versatile"
```

### Capability Constants

```python
CAPABILITY_REASONING = "reasoning"
CAPABILITY_CODING = "coding"
CAPABILITY_LONG_CONTEXT = "long_context"
CAPABILITY_VISION = "vision"
CAPABILITY_SEARCH = "search"
CAPABILITY_FAST_INFERENCE = "fast_inference"
CAPABILITY_LOW_COST = "low_cost"
```

### ManifestRegistry (Global Singleton)

```python
manifest_registry = ManifestRegistry()  # Global instance

# Query methods
manifest_registry.get(provider_id) → Optional[ProviderManifest]
manifest_registry.get_enabled() → dict[str, ProviderManifest]
manifest_registry.get_healthy() → dict[str, ProviderManifest]  # enabled + healthy/unknown
manifest_registry.get_by_capability(cap) → list[ProviderManifest]  # sorted by priority
manifest_registry.get_healthy_by_capability(cap) → list[ProviderManifest]

# Mutation methods
manifest_registry.register(manifest)
manifest_registry.update_health(provider_id, health)
manifest_registry.set_enabled(provider_id, enabled)

# Introspection
"provider_id" in manifest_registry  # __contains__
manifest_registry.list_provider_ids()
manifest_registry.list_capabilities()
```

### Capability-Based Routing

```python
# capability_router.py
class CapabilityRouter:
    def route_by_capability(capability, exclude_providers=None) → list[ProviderManifest]
    def get_healthy_provider_for_capability(capability, exclude=None) → Optional[ProviderManifest]
    def execute_with_capability_routing(capability, request_fn, exclude=None) → dict
```

### Fallback Chain (3-Phase)

```python
# fallback_chain.py
class FallbackChain:
    def execute_with_fallback(
        capability, request_fn, deterministic_fn=None,
        max_retries_per_provider=1, exclude_providers=None
    ) → FallbackResult

# Phase 1: Capability-matched providers (with retries + exponential backoff)
# Phase 2: All healthy providers (without capability filter)
# Phase 3: Deterministic fallback (caller-provided function)
```

### Key Loading (Dynamic)

```python
# startup/key_loader.py
def load_provider_keys(config: Config) → Config
    # Priority 1: AIKEYPOOL_PROVIDER_KEYS = '{"groq": ["key1"], "together": ["key2"]}'
    # Priority 2: AIKEYPOOL_PROVIDER_<NAME>_KEYS = "key1,key2" (additive/legacy)
    # Auto-registers new providers in manifest registry

def get_configured_providers() → list[str]
    # Returns sorted list of providers with at least one key configured
```

---

## 10. Multi-Agent Research

Research uses a 6-agent pipeline for comprehensive analysis.

### Agent Roles

| Role | Capabilities Required | Purpose |
|------|----------------------|---------|
| RESEARCHER | reasoning, coding | Gathers initial findings |
| EVIDENCE_COLLECTOR | reasoning | Validates and sources claims |
| VERIFIER | reasoning, coding | Cross-references findings |
| CONTRADICTION_DETECTOR | reasoning | Identifies conflicting information |
| CRITIC | reasoning | Quality control and bias detection |
| WRITER | reasoning, coding | Produces executive summary |

### Pipeline Flow

```
Raw Findings → RESEARCHER → EVIDENCE_COLLECTOR → VERIFIER
              → CONTRADICTION_DETECTOR → CRITIC → WRITER
              → Consolidated Results
```

Each agent:
1. Selects best provider via capability routing
2. Builds role-specific prompt
3. Executes via FallbackChain
4. Parses JSON response
5. Feeds output to next agent

### Output Structure

```python
{
    "agent_results": [...],           # Per-agent results
    "success_count": 5,
    "total_count": 6,
    "providers_used": ["groq", "openrouter"],
    "consolidated_findings": [...],
    "verified_claims": [...],
    "contradictions": [...],
    "open_questions": [...],
    "action_items": [...],
    "executive_summary": "..."
}
```

---

## 11. Provider Discovery

Automatic discovery of new AI providers from community sources.

### Discovery Sources

| Source | URL | Description |
|--------|-----|-------------|
| cool-ai-stuff | github.com/zukixa/cool-ai-stuff | Curated list of free AI APIs |
| free-llm-api-resources | github.com/cheahjs/free-llm-api-resources | Community-maintained free LLM APIs |

### Discovery Process

```
discover_providers(config)
│
├─ For each source:
│  ├─ Fetch README content
│  ├─ Parse for API endpoints (regex patterns)
│  ├─ Extract: provider names, models, free tier indicators
│  └─ Filter: exclude configured + blocklisted providers
│
├─ Deduplicate suggestions
│
├─ Save results to data/discovery_results.json
│
└─ Return: {
    "timestamp": "...",
    "sources_checked": 2,
    "sources_succeeded": 2,
    "total_suggestions": 15,
    "new_suggestions": 10,
    "configured_providers": ["groq"],
    "suggestions": [...]
}
```

### Blocklist

Non-OpenAI-compatible providers excluded:
- anthropic, google, meta, aws_bedrock, azure_ai, together_ai (when incompatible)
- cohere, mistral (when using native API)

---

## 12. Historical Intelligence

Track provider changes over time for trend analysis.

### Tracking Categories

| Category | Data Structure | Purpose |
|----------|---------------|---------|
| Provider History | first_seen, last_active, status, models | Track provider lifecycle |
| Model History | provider, released, status, first_seen | Track model availability |
| Rate Limit Changes | provider, date, change, old/new value | Monitor API limit changes |
| Free Tier Changes | provider, date, change | Track free tier modifications |
| Provider Outages | provider, start, end, reason | Incident tracking |
| Discoveries | provider, source, details | Discovery provenance |

### HistoryTracker

```python
# history_tracker.py
class HistoryTracker:
    def update_provider(provider_id, status, models, capabilities)
    def update_model(model_name, provider, status)
    def record_rate_limit_change(provider, change, old_value, new_value)
    def record_free_tier_change(provider, change)
    def record_outage(provider, reason, start, end)
    def record_discovery(provider, source, details)
    
    def get_changes_since(since_date) → dict
    def get_changes_since_last_report() → dict  # includes is_first_report
    def mark_report_generated()
    def format_changes_since_last_report() → str  # human-readable text
```

### Integration with Research

History informs research by:
1. Tracking which providers have been analyzed
2. Detecting changes since last report
3. Identifying trends (new providers, model deprecations)
4. Providing context for contradiction detection

---

## 13. Security Model

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
| Provider API keys (JSON) | `AIKEYPOOL_PROVIDER_KEYS` env var | Response bodies, logs, dashboard JSON |
| Provider API keys (legacy) | `AIKEYPOOL_PROVIDER_<NAME>_KEYS` env var | Response bodies, logs, dashboard JSON |
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

## 14. Configuration Model

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
│  ├─ AIKEYPOOL_PROVIDER_KEYS (JSON object, primary)
│  └─ AIKEYPOOL_PROVIDER_<NAME>_KEYS (comma-separated, legacy/additive)
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
| `AIKEYPOOL_PROVIDER_KEYS` | JSON | None | `{"provider": ["key1", "key2"]}` (primary) |
| `AIKEYPOOL_PROVIDER_*_KEYS` | string | None | Comma-separated API keys (legacy/additive) |
| `SMTP_HOST` | string | None | SMTP server hostname |
| `SMTP_PORT` | int | 587 | SMTP server port |
| `SMTP_USER` | string | None | SMTP username |
| `SMTP_PASSWORD` | string | None | SMTP password |
| `EMAIL_RECIPIENT` | string | None | Email recipient address |

---

## 15. GitHub Actions Workflows

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
│  │  ├─ AIKEYPOOL_PROVIDER_KEYS (JSON, primary)
│  │  ├─ AIKEYPOOL_PROVIDER_*_KEYS (legacy/additive)
│  │  └─ All other AIKEYPOOL_* config vars
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

## 16. Deployment Guide

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

## 17. Sequence Diagrams

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
  │                    │ STEP 0a: validate_config()              │                    │
  │                    │ STEP 0b: load_provider_keys()           │                    │
  │                    │ STEP 0c: sync_provider_keys()           │                    │
  │                    │ STEP 0d: list_providers()               │                    │
  │                    │                    │                    │                    │
  │                    │ STEP 1: Health     │                    │                    │
  │                    │ get_all_stats()    │                    │                    │
  │                    │─── OK ────────────│                    │                    │
  │                    │                    │                    │                    │
  │                    │ STEP 1b: Discovery │                    │                    │
  │                    │ discover_providers()                    │                    │
  │                    │ save_discovery_results()                │                    │
  │                    │                    │                    │                    │
  │                    │ STEP 1c: History   │                    │                    │
  │                    │ HistoryTracker     │                    │                    │
  │                    │                    │                    │                    │
  │                    │ STEP 2: Research   │                    │                    │
  │                    │ _run_research_loop()                    │                    │
  │                    │─────────────────>│                    │                    │
  │                    │                    │ MultiAgentOrch     │                    │
  │                    │                    │ FallbackChain      │                    │
  │                    │                    │ CapabilityRouter   │                    │
  │                    │                    │ (calls AI APIs)    │                    │
  │                    │ ◄─ findings ─────│                    │                    │
  │                    │                    │                    │                    │
  │                    │ STEP 3: Report     │                    │                    │
  │                    │ generate_final_report()                 │                    │
  │                    │                    │                    │                    │
  │                    │ STEP 4: Status     │                    │                    │
  │                    │ generate_status_json()                  │                    │
  │                    │──────────────────────────────────────>│                    │
  │                    │                    │                    │ write status.json  │
  │                    │ ◄─ OK ─────────────────────────────────│                    │
  │                    │                    │                    │                    │
  │                    │ STEP 5: Recs       │                    │                    │
  │                    │ generate_recommendations_json()         │                    │
  │                    │──────────────────────────────────────>│                    │
  │                    │                    │                    │ write recs.json    │
  │                    │ ◄─ OK ─────────────────────────────────│                    │
  │                    │                    │                    │                    │
  │                    │ STEP 6: Email      │                    │                    │
  │                    │ _do_send_email()   │                    │                    │
  │                    │─────────────────────────────────────────────────────────>│
  │                    │                    │                    │ SMTP send          │
  │                    │ ◄─ True/False ────────────────────────────────────────────│
  │                    │                    │                    │                    │
  │                    │ STEP 7: Archive    │                    │                    │
  │                    │ archive_cycle()    │                    │                    │
  │                    │                    │                    │                    │
  │ ◄─ JSON result     │                    │                    │                    │
  │                    │                    │                    │                    │
  │ git add dashboard/data/                │                    │                    │
  │ git commit + push  │                    │                    │                    │
  │ (triggers Pages deploy)                │                    │                    │
```

---

## 18. Extension Guide

### Adding a New Provider (Zero-Code for Generic OpenAI-Compatible)

For OpenAI-compatible providers, no code changes are needed:

```bash
# Just set the environment variable:
export AIKEYPOOL_PROVIDER_MY_PROVIDER_KEYS="key1,key2"

# Or add to AIKEYPOOL_PROVIDER_KEYS JSON:
export AIKEYPOOL_PROVIDER_KEYS='{"groq": ["key1"], "my_provider": ["key2"]}'
```

The system auto-detects and registers the provider as a generic OpenAI-compatible adapter.

### Adding a New Builtin Provider (Custom Adapter)

For providers with non-standard APIs:

**1. Create the adapter file:**

```python
# src/providers/my_provider.py

from .base_provider import BaseProvider
from .manifest import ProviderManifest

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

    def get_manifest(self) -> ProviderManifest:
        return ProviderManifest(
            provider_id="my_provider",
            display_name="My Provider",
            adapter="builtin",
            capabilities=["reasoning", "coding"],
            priority=5,
            endpoint=self.get_endpoint(),
            default_model="my-model-1",
        )
```

**2. Register in provider_factory.py:**

```python
# In _BUILTIN_PROVIDERS dict:
_BUILTIN_PROVIDERS = {
    "github_models": GitHubModelsProvider,
    "groq": GroqProvider,
    "openrouter": OpenRouterProvider,
    "my_provider": MyProvider,  # ← Add this
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

### Adding a New Capability

```python
# In manifest.py, add constant:
CAPABILITY_MY_FEATURE = "my_feature"

# In provider's get_manifest():
capabilities=["reasoning", "my_feature"]

# In agents.py, add to ROLE_CAPABILITIES:
ROLE_CAPABILITIES[AgentRole.MY_ROLE] = ["my_feature"]
```

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

## 19. Known Limitations

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

## 20. Future Roadmap

### v1.1.0 — Dynamic Provider System ✅
- [x] Provider manifest system with capabilities
- [x] Capability-based routing
- [x] Fallback chain with deterministic fallback
- [x] Dynamic key loading (AIKEYPOOL_PROVIDER_KEYS JSON)
- [x] Generic OpenAI-compatible adapter
- [x] Zero-code provider additions

### v1.2.0 — Intelligence System ✅
- [x] Multi-agent research (6 roles)
- [x] GitHub provider discovery
- [x] Historical intelligence tracking
- [x] Executive report with 12 sections
- [x] Smart recommendations (exclude configured providers)
- [x] Suggested providers from discovery

### v1.3.0 — Operational Improvements
- [ ] Anthropic native adapter (Messages API)
- [ ] OpenAI native adapter (Responses API)
- [ ] Configurable HTTP timeout per provider
- [ ] Connection pooling (shared `httpx.Client`)
- [ ] Use `ProviderError.error_type` directly in rotator (remove substring matching)

### v1.4.0 — Observability
- [ ] Consolidate `key_registry.json` and `key_health.json` into single file
- [ ] Rate limiting middleware for API
- [ ] Request logging middleware (latency, key used, provider)
- [ ] Health check endpoint for each provider (not just overall)
- [ ] Prometheus metrics export

### v1.5.0 — Dashboard Improvements
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

*This document was generated from the AI Key Pool v1.2.0 codebase.*
