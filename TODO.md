# engine-fal Sync with engine-replicate

## Tasks

- [x] 1. **datatypes.py** — rename `bullet_path` → `source_path`; add `current`/`total` to `ProgressEvent`
- [x] 2. **__init__.py** — add `list_standby_profiles()`, `EngineError` to exports
- [x] 3. **engine.py** — fix SDK (`fal_client`), result parsing, granular emits, timestamp naming, `ProgressEvent` objects
- [x] 4. **requirements.txt** — `fal>=1.0` → `fal-client>=1.0`
- [x] 5. **tests/test_engine.py** — update mocks, field names, result format, add `current`/`total` assertions
- [x] 6. **profiles/standby/** — create directory if missing
