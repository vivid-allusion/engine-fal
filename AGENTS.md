## Architecture: Engine SDK Wrapper

This repo is an **Engine** in the studiolot ecosystem. It wraps the
**Fal.ai** API and exposes a uniform interface that Vehicles call.

### The layers

```
studiolot (TUI) → Vehicle (script) → **Fal.ai Engine** → Provider API
```

Vehicles like Frame Composer and Motion Conductor are SDK-agnostic. They
discover this Engine, load it via `engine_loader.py`, and call
`engine.run(inputs)`. This Engine handles all **Fal.ai**-specific logic.

### Contract

- **`Engine.__init__` is PURE.** No network I/O, no SDK imports in the
  constructor. It only stores: profile dict, output_dir, api_key,
  on_progress callback.
- **`Engine.run(inputs: list[InputFile]) -> list[OutputFile]`** is the ONLY
  entry point Vehicles call. Returns ALL results — success and failure —
  as OutputFile objects. Never raises for per-bullet failures.
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
  these are seeded into Vehicle repos' `USER-FILES/02.STANDBY/`.

### Provider details

| Field | Value |
|-------|-------|
| **Platform** | `fal` |
| **API key env var** | `FAL_KEY` |
| **Key pattern** | `^[A-Za-z0-9-]{20,}$` |
| **Homepage** | https://fal.ai |

### Engine discovery

Vehicles find this Engine via `engine_loader.py`:
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
| `engine_fal/profiles/standby/` | Publishable YAML profiles |
| `tests/test_engine.py` | Unit tests |
| `pyproject.toml` | Package metadata, pip-installable |
| `requirements.txt` | Provider SDK + dependencies |

### Reference

Full contract: `~/Nextcloud/00-DEVELOPMENT/MISC_DEV_TOOLS/studiolot/docs/architecture/ENGINE_CONTRACT.md`

### Session History

- 2026-08-24 — Logs written as each file is saved: `ProgressEvent` gained `saved_path` + `api_payload` fields; `_save_results()` emits them on "💾 Saved" so vehicles can write per-file logs in real time (parity with engine-replicate).
- 2026-08-24 — API payload for per-file run logs: `run()` now emits `📦 Payload: {fal_input}` via `_emit()` and attaches `metadata={"api_payload": fal_input}` to every successful `OutputFile` (parity with engine-replicate).
- 2026-08-05 — Created AGENTS.md, .env.example, .gitignore
