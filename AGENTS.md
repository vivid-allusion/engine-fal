## Architecture: Engine SDK Wrapper

This repo is an **Engine** in the Helmer ecosystem. It wraps the
**Fal.ai** API and exposes a uniform interface that Generators call.

### The layers

```
Helmer (Transceiver) → Generator (script) → **Fal.ai Engine** → Provider API
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
- **`metadata.py`** is zero-dependency (stdlib only). the Transceiver imports this
  (NOT engine.py) to read PROVIDER_NAME for the provider label. This avoids
  pulling in the **Fal.ai** SDK transitively.
- **`endpoints/`** TOMLs are the canonical model catalog. One file per model,
  defining valid parameter ranges.
- **`profiles/standby/`** contains publishable YAML profiles. When installed,
  these are seeded into Generator repos' `USER-FILES/02.STANDBY/`.

## NON-NEGOTIABLE: Endpoint TOMLs Mirror the Live Schema

**The endpoint TOML catalog is NOT a "curated subset".** Every input the
model accepts server-side MUST be declared in the TOML — no exceptions, no
omissions. A missing param is a **bug**, not an editorial choice: a profile
that cannot express the model's real inputs sends either an incomplete or an
invented payload, and fal answers with a 422 the user only sees as a failure.

The rules, hard:

1. **Every server-side Input property** (except `prompt`) is declared in the
   TOML — as `[general] image_url_param` (the primary media input), a
   `[general] slots` entry (every other `*_url`/`*_urls` input), or a
   `[params.*]` entry (every scalar).
2. **Nothing undeclared is invented.** No `[params.*]` key may exist that is
   not in the live schema; no slot may point at a key the server does not
   accept. The 2026-10-26 sweep removed `aspect_ratio`/`resolution` from
   flux-pro, flux-schnell, recraft-v3, sdxl and ideogram-v2, `duration` from
   ltx-video and minimax-video, and the `image_url` start-frame keys from the
   two prompt-only video endpoints — all of them had been sent to endpoints
   that never accepted them.
3. **Options and ranges mirror the server exactly.** `select` options equal
   the server enum (no dropped values, no invented values), `integer`/`number`
   min/max equal the server bounds, and defaults equal the server default
   wherever the server declares one.
4. **Unrenderable inputs are exempt, and exactly that set is exempt.** fal's
   schemas carry structured inputs the wizard has no widget for (`history`,
   `context`, `ip_adapter`, `controlnet`, `loras`, `colors`). They are listed
   in a single checked comment, `# UNRENDERABLE: …`, and the gate fails when
   the comment and the schema disagree.
5. **Verified by `scripts/check_schema_sync.py`.** It fetches the live queue
   OpenAPI for every endpoint and diffs it against the TOML catalog. The
   models API needs no key. Run it before committing any TOML change and after
   any model bump:

   ```
   python scripts/check_schema_sync.py                  # live
   python scripts/check_schema_sync.py --cache-dir DIR   # pinned/offline
   ```

   Exit code 1 = drift = **do not merge**; exit code 2 = an endpoint could
   not be fetched (provider blip — re-run, not a verdict); exit code 0 = clean.
   A model the API reports as gone is drift, never a fetch blip. A clean run is
   a precondition for shipping endpoint TOMLs. The offline logic is unit-tested
   (`tests/test_schema_sync.py`) so regressions in the checker itself are
   caught without network access.
6. **A deprecated endpoint leaves the catalog.** fal stopped publishing an
   OpenAPI schema for `fal-ai/mochi-v1` (status `deprecated`); its TOML was
   removed rather than kept pointing at a dead row.

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
- Discovery order: local clone in `.00_TOOLS/GENERATORS/ENGINES/` first, pip-installed package as fallback

### Source File Map

| File | Purpose |
|------|---------|
| `engine_fal/engine.py` | Engine implementation — all SDK calls live here |
| `engine_fal/datatypes.py` | InputFile, OutputFile, ProgressEvent, EngineError |
| `engine_fal/metadata.py` | Zero-dependency identity constants (the Transceiver reads this) |
| `engine_fal/__init__.py` | Re-exports Engine + all datatypes + `list_standby_profiles(media_type)` shelf selector |
| `engine_fal/input_builder.py` | Payload building + live-schema shaping/coercion (mixin) |
| `engine_fal/output_handler.py` | Result normalization, stream sniffing, atomic save (mixin) |
| `engine_fal/retry_policy.py` | Backoff, Retry-After, error classification (mixin) |
| `engine_fal/endpoints/` | TOML model catalog (IMG/VID/TXT/Vision) |
| `engine_fal/profiles/standby/` | Publishable YAML profiles by category (IMG/VID) |
| `scripts/check_schema_sync.py` | Live schema ↔ TOML drift checker (NON-NEGOTIABLE gate) |
| `tests/test_engine.py` | Unit tests |
| `tests/test_schema_sync.py` | Offline checker-logic tests |
| `tests/test_concurrency_key.py` | `concurrency` key regression gate |
| `tests/test_retry_after.py` | Retry-After policy proofs (stub-only) |
| `tests/conftest.py` | Offline guard — stubs the live schema fetch so tests never call the fal models API |
| `pyproject.toml` | Package metadata, pip-installable |
| `requirements.txt` | Provider SDK + dependencies |

### Reference

Full contract: `docs/architecture/ENGINE_CONTRACT.md` in the `helmer-transceiver` repo

Session history: [docs/HISTORY.md](docs/HISTORY.md)
