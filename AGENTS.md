## Architecture: Engine SDK Wrapper

This repo is an **Engine** in the studiolot ecosystem. It wraps the
**Fal.ai** API and exposes a uniform interface that Generators call.

### The layers

```
studiolot (TUI) → Generator (script) → **Fal.ai Engine** → Provider API
```

Generators like Image Generator and Video Generator are SDK-agnostic. They
discover this Engine, load it via `engine_loader.py`, and call
`engine.run(inputs)`. This Engine handles all **Fal.ai**-specific logic.

### Contract

- **`Engine.__init__` is PURE.** No network I/O, no SDK imports in the
  constructor. It only stores: profile dict, output_dir, api_key,
  on_progress callback.
- **`Engine.run(inputs: list[InputFile]) -> list[OutputFile]`** is the ONLY
  entry point Generators call. Returns ALL results — success and failure —
  as OutputFile objects. Never raises for per-Markdown-file failures.
- **`EngineError`** is for unrecoverable pre-flight failures only: missing
  API key, invalid profile, provider auth rejection.
- **`datatypes.py`** defines InputFile, OutputFile, ProgressEvent,
  EngineError. Single source of truth per Engine.
- **`metadata.py`** is zero-dependency (stdlib only). studiolot imports this
  (NOT engine.py) to read PROVIDER_NAME for the TUI label. This avoids
  pulling in the **Fal.ai** SDK transitively.
- **`endpoints/`** TOMLs are the canonical model catalog. One file per model,
  defining valid parameter ranges.
- **`profiles/standby/`** contains publishable YAML profiles. When installed,
  these are seeded into Generator repos' `USER-FILES/02.STANDBY/`.

### Provider details

| Field | Value |
|-------|-------|
| **Platform** | `fal` |
| **API key env var** | `FAL_KEY` |
| **Key pattern** | `^[A-Za-z0-9-]{20,}$` |
| **Homepage** | https://fal.ai |

### Engine discovery

Generators find this Engine via `engine_loader.py`:
- Repo directory: `engine-fal` 
- Python package: `engine_fal`
- `engine_loader.py` handles the hyphen→underscore mapping
- Discovery order: local clone in `00_APPLICATIONS/ENGINES/` first, pip-installed package as fallback

### Source File Map

| File | Purpose |
|------|---------|
| `engine_fal/engine.py` | Engine implementation — all SDK calls live here |
| `engine_fal/datatypes.py` | InputFile, OutputFile, ProgressEvent, EngineError |
| `engine_fal/metadata.py` | Zero-dependency identity constants (studiolot reads this) |
| `engine_fal/__init__.py` | Re-exports Engine + all datatypes |
| `engine_fal/endpoints/` | TOML model catalog (IMG/VID/TXT/Vision) |
| `engine_fal/profiles/standby/IMG/` | Publishable YAML profiles by media category, mirroring the endpoint catalog |
| `tests/test_engine.py` | Unit tests |
| `tests/conftest.py` | Offline guard — stubs the live schema fetch so tests never call the fal models API |
| `pyproject.toml` | Package metadata, pip-installable |
| `requirements.txt` | Provider SDK + dependencies |

### Reference

Full contract: `~/Nextcloud/00-DEVELOPMENT/MISC_DEV_TOOLS/studiolot/docs/architecture/ENGINE_CONTRACT.md`

### Session History

- 2026-09-29 — Nano Banana family added to the IMG catalog
  - Six endpoint TOMLs mirror the live fal OpenAPI schemas (verified with a
    live schema diff, 0 drift): `nano-banana`, `nano-banana-edit`,
    `nano-banana-2`, `nano-banana-2-edit`, `nano-banana-pro`,
    `nano-banana-pro-edit`. fal splits text-to-image and image-to-image into
    separate endpoints, so both are declared; the edit TOMLs carry
    `image_url_param = "image_urls"` and — for nano-banana-2/edit — media
    `slots` (`video_url`, `pdf_url`, `audio_url`) with `slot_scalar`.
  - Pricing taken from fal's own model data (nano-banana $0.039, nano-banana-2
    $0.08, nano-banana-pro $0.15 per image).
  - 18 standby profiles added under `profiles/standby/IMG/` (1:1 / 16:9 / 9:16
    per model); the existing `nano-banana-2_21x9.yaml` now points at the new
    TOML and uses `image_url_param`.
- 2026-09-29 — Interface/behaviour parity with the gold engine (engine-replicate)
  - `datatypes.py` — `InputFile` gained `references: dict[str, list[str]]`
    (named secondary media slots); `OutputFile` gained `expected_path: Path | None`.
  - `engine.py` — ported the gold behaviour: every error `OutputFile` now
    carries the precomputed `expected_path`; error progress events carry
    `api_payload`; transient failures retry with exponential backoff
    (`retry_on_429`, `retry_base_delay`), fatal billing/auth errors abort the
    batch, a circuit breaker (`circuit_breaker_max`) stops churn on sustained
    rate limits; `delay_between_requests`; `📝 Markdown:` progress line; stream
    outputs sniff their real extension from magic bytes; schema-driven scalar
    coercion and media shaping (`integer`/`number`/`boolean`, array vs string)
    from the endpoint's live openapi Input schema. The primary media key
    resolves `image_url_param` → `reference_param` → `image_url`; named
    `references` slots route to their own keys as lists, `slot_scalar` (or a
    string schema) makes a slot a single URL, and the generator-only `fps` key is
    stripped from the provider payload.
  - `engine.py` — `_fetch_input_props()` reads the fal models API
    (`GET /v1/models?endpoint_id=…&expand=openapi-3.0`), fully guarded: a failed
    fetch degrades to pass-through params, never an error.
  - `__init__.py` — `list_standby_profiles(media_type)` shelf selector (IMG/VID);
    standby profiles moved into `profiles/standby/IMG/`.
  - Tests ported from the gold suite (86 green): datatypes, expected_path,
    stream sniffing, slot routing/schema coercion, retry/fatal/circuit breaker,
    URL validation, shelf selector, and openapi parsing (offline via conftest).
  - Known gap (out of this change): the endpoint TOML catalog still drifts from
    the live fal schemas (undeclared server params, invented keys). A
    schema-sync checker + catalog rebuild is required — see the engine-mgmt
    mapping gate before regenerating TOMLs.
- 2026-09-15 — H3 Max Turbo catalog: added `endpoints/VID-Models/h3-max-turbo-txt.toml` and `h3-max-turbo-img.toml` for `minimax/h3-max-turbo/text-to-video` and `.../image-to-video`, plus standby profiles `profiles/standby/h3-max-turbo_txt_21x9.yaml` and `h3-max-turbo-img_i2v.yaml`. TOML params verified against the live fal OpenAPI queue schema (zero drift).
- 2026-09-15 — Scalar param coercion: wizard-composed profiles serialize numeric params as strings (`duration: "5"`), which fal rejects with 422 against integer fields. `_build_fal_input()` now coerces string params against the endpoint's live OpenAPI schema (`_input_props()` fetches the public queue schema by `endpoint_id`, resolves `$ref`/`allOf`, caches per instance; best-effort — values pass verbatim when the schema is unavailable). Non-convertible tokens (`auto`) pass verbatim.
- 2026-09-15 — Seedance 2.5 catalog + slot-aware payloads: three endpoint TOMLs (`seedance-2.5-img`, `-txt`, `-ref`) and three standby profiles (`seedance-2.5_i2v`, `_t2v`, `_r2v`) mirror the live fal schemas (params, enums, defaults verified against the endpoint openapi). `InputFile` gained an additive `references: dict[str, list[str]]` field; `_build_fal_input()` now resolves the primary key from `image_url_param` (falling back to `reference_param`, then `image_url`), routes named slots to their own keys as lists by default, honors `slot_scalar` for string-valued slots (e.g. `end_image_url`), and strips the generator-only `fps` key from the provider payload. Engine tests 41 green.
- 2026-08-24 — Logs written as each file is saved: `ProgressEvent` gained `saved_path` + `api_payload` fields; `_save_results()` emits them on "💾 Saved" so generators can write per-file logs in real time (parity with engine-replicate).
- 2026-08-24 — API payload for per-file run logs: `run()` now emits `📦 Payload: {fal_input}` via `_emit()` and attaches `metadata={"api_payload": fal_input}` to every successful `OutputFile` (parity with engine-replicate).
- 2026-08-05 — Created AGENTS.md, .env.example, .gitignore
