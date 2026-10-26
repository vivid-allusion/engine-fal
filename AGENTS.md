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
- Discovery order: local clone in `00_APPLICATIONS/ENGINES/` first, pip-installed package as fallback

### Source File Map

| File | Purpose |
|------|---------|
| `engine_fal/engine.py` | Engine implementation — all SDK calls live here |
| `engine_fal/datatypes.py` | InputFile, OutputFile, ProgressEvent, EngineError |
| `engine_fal/metadata.py` | Zero-dependency identity constants (studiolot reads this) |
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

Full contract: `~/Nextcloud/00-DEVELOPMENT/MISC_DEV_TOOLS/studiolot/docs/architecture/ENGINE_CONTRACT.md`

### Session History

- 2026-10-26 — Gold parity: live schema gate + catalog swept to zero drift
  - **New gate** `scripts/check_schema_sync.py` — the fal twin of the gold
    engine's checker, key-free (the models API publishes each endpoint's queue
    OpenAPI through `expand=openapi-3.0`), spaced and retried on 429/network
    errors, with `--cache-dir` for pinned or offline runs. It enforces
    coverage, no invention, enum/range/default equality, media-vs-param
    placement, required-vs-optional, `duration_param_name` existence, and an
    exact `# UNRENDERABLE:` set for structured inputs the wizard cannot show.
    Offline logic covered by `tests/test_schema_sync.py` (24 tests).
  - **Catalog swept**: 109 violations across 17 of 21 endpoints → 0 across 20.
    flux-pro, flux-schnell, ideogram-v2, recraft-v3 and sdxl lost the invented
    `aspect_ratio`/`resolution` pair and gained the real `image_size` presets
    (plus `output_format`, `seed`, `sync_mode`, `guidance_scale`,
    `num_inference_steps`, `enable_safety_checker`, `style`, `expand_prompt`,
    `negative_prompt`, `prompt_optimizer`, `acceleration`,
    `safety_tolerance`, `enhance_prompt` where the schema has them); the
    nano-banana-2/-pro families and their edit endpoints gained the thinking,
    sampling and history controls they were hiding; h3-max-turbo gained
    `target_audio_url` as a slot and `sync_mode`; seedance-2.5 (all three)
    gained `codec` and `draft`, `task` gained the server's `auto`, and the
    duration default now equals the server's `auto` (each standby profile
    still pins `5` explicitly, so profile cost stays deterministic);
    ltx-video and minimax-video lost `image_url`, `aspect_ratio` and
    `duration` — both are prompt-only text-to-video on fal. 15 standby
    profiles were repointed from `aspect_ratio`/`resolution` to `image_size`
    (and `output_format: png` where the model has it).
  - **Removed** `endpoints/VID-Models/mochi-1.toml`: `fal-ai/mochi-v1` is
    `deprecated` and publishes no schema. Breaking catalog change, recorded
    here; no standby profile referenced it.
  - **Retry policy split out** into `engine_fal/retry_policy.py` (mixin, as in
    the gold engine) — `engine.py` is 234 lines. `_retry_after_seconds` is now
    the gold's hardened version plus fal's reality: headers may live on
    `exc.response.headers`, numeric values may arrive as int/float, header
    names are matched case-insensitively, and every malformed shape (None,
    non-mapping, unparseable, bad HTTP-date) degrades to "no hint" instead of
    raising.
  - **Ported suites**: `tests/test_concurrency_key.py` (absent `concurrency` ≡
    1, never sent to the provider, invalid values raise, fatal identity) and
    the full Retry-After matrix. Suite: 148 green.
  - **UI consequences, stated plainly**: h3-max-turbo `duration` is a numeric
    field because the server accepts fractional seconds (0.92–15), not a
    1–15 select; `prompt_expansion_mode` is free text because fal publishes
    documented examples (`disabled|balanced|quality`), not an enum — the
    note names them; flux/recraft/sdxl aspect control is now
    `image_size` presets (`landscape_16_9` …) because that is what those
    endpoints accept.
  - **Known follow-up (filed, not fixed)**: there is no profile-level gate
    asserting that every `parameters:` key in a standby YAML exists in its
    endpoint TOML. The 2026-10-26 drift (`aspect_ratio` on flux-pro) would
    have been caught by one. PyYAML is not on the dev box's system python, so
    the gate needs a parser decision first.
- 2026-10-15 — Always-on per-item error logs: any failed item now writes a
  verbose `<base>-<idx>_ERROR.log` beside the media's would-be destination —
  provider exceptions, empty-prompt skips, "no output returned" responses, and
  save/download/`.part`-reveal failures — independent of Generator logging
  flags. The log holds when/item/source/dest/expected/platform/endpoint, the
  profile dict and exact api payload as pretty JSON, duration (raw/effective/
  fps), media (primary URLs + every reference slot), the exact final prompt,
  error stage (`build_input`/`provider_call`/`save`/`no_output`/`empty_prompt`),
  error text, full traceback (or n/a), and the office/MEMORY note. The written
  path is recorded on the error `OutputFile` as `metadata["error_log"]` for the
  Generator's run-level summary. Secrets are masked (`_mask_secrets`);
  environment data is never dumped.
- 2026-10-10 — Nano Banana 2.1 catalog added to the IMG shelf
  - Two endpoint TOMLs mirror the live fal OpenAPI schemas (verified, 0 drift):
    `nano-banana-2.1` and `nano-banana-2.1/edit`. Both are token-billed — text
    and thinking $1.764/1M in, $8.823/1M out; image $1.764/1M in, $35.294/1M
    out. fal's tests put a typical 1K image at ≈ $0.04 (16:9: $0.0396 at 1K,
    $0.0593 at 2K, $0.1335 at 4K; a reference edit adds ≈ $0.002).
  - Resolution is 1K/2K/4K (0.5K is dropped from v2) and `thinking_level` lists
    `medium` (the live default). The edit endpoint carries `image_url_param =
    "image_urls"` and `slots` for `video_url`/`pdf_url` (no audio URL).
  - Seven standby profiles added under `profiles/standby/IMG/` (1:1 / 16:9 /
    9:16 per endpoint plus the 21:9 reframe).
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
