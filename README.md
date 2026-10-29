# engine-fal — Fal.ai Engine for studiolot

Engine wrapping the [Fal.ai](https://fal.ai) API behind studiolot's uniform
Engine interface. Generators (studio applications like Image Generator and
Video Generator) call this Engine instead of importing the Fal SDK directly.

## Quick Start

```bash
# Clone into a studiolot project
git clone https://github.com/vivid-allusion/engine-fal.git \
  00_APPLICATIONS/ENGINES/engine-fal/

# Or pip install
pip install git+https://github.com/vivid-allusion/engine-fal.git

# Or for local development
cd engine-fal && pip install -e .
```

## Usage

```python
from engine_fal import Engine, InputFile
from pathlib import Path

profile = {
    "platform": "fal",
    "endpoint": "fal-ai/flux/schnell",
    "media_type": "image",
    "parameters": {"image_size": "landscape_16_9"},
}

engine = Engine(profile, "/tmp/output", api_key="your_fal_key")
results = engine.run([InputFile(path=Path("shot.md"), prompt="a cat")])

for r in results:
    print(r.status, r.path)
```

## API Key

Set `FAL_KEY` in your environment or `.env` file:

```bash
export FAL_KEY=your_key_here
```

Get a key at https://fal.ai/dashboard

## Repo Structure

```
engine-fal/
├── engine_fal/
│   ├── engine.py         ← Engine class wrapping the fal SDK
│   ├── input_builder.py  ← payload building + live-schema shaping (mixin)
│   ├── output_handler.py ← result normalization, stream sniffing, atomic save (mixin)
│   ├── retry_policy.py   ← backoff, Retry-After, error classification (mixin)
│   ├── datatypes.py      ← InputFile, OutputFile, ProgressEvent, EngineError
│   ├── metadata.py       ← Zero-dependency identity constants
│   ├── __init__.py       ← Re-exports + list_standby_profiles(media_type)
│   ├── endpoints/
│   │   ├── IMG-Models/    ← image model TOMLs (flux, ideogram, sdxl, recraft, nano-banana family)
│   │   ├── VID-Models/    ← video model TOMLs (ltx, minimax, h3-max-turbo, seedance-2.5)
│   │   ├── TXT-Models/    ← .gitkeep (fal.ai is image/video only)
│   │   └── Vision-Models/ ← .gitkeep (fal.ai is image/video only)
│   └── profiles/standby/{IMG,VID}/ ← publishable standby YAML profiles by media type
├── scripts/
│   └── check_schema_sync.py ← live schema ↔ TOML drift gate
├── pyproject.toml      ← pip install definition
├── requirements.txt    ← fal-client>=1.0
└── tests/
    ├── conftest.py     ← offline guard (no live schema fetch in tests)
    ├── test_engine.py  ← unit tests
    ├── test_schema_sync.py ← offline tests for the drift gate
    ├── test_concurrency_key.py ← concurrency-key regression gate
    └── test_retry_after.py ← Retry-After policy proofs
```

The engine mirrors the behaviour of the gold engine (`engine-replicate`):
error results carry the destination filename they would have used, transient
provider failures retry with backoff, billing/auth failures abort the batch,
streamed outputs are saved under the extension their bytes claim, and scalar
parameters are coerced against the endpoint's live openapi Input schema.
Named reference slots (Seedance/H3) route to their own keys, `slot_scalar`
marks string-valued slots, and the generator-only `fps` key never reaches the
provider.

## Catalog gate

The endpoint TOMLs mirror fal's live queue OpenAPI exactly — every accepted
input is declared, nothing invented, enums/ranges/defaults equal. Verify
before committing any TOML change (no API key needed):

```bash
python scripts/check_schema_sync.py
```

Exit code 1 means drift: fix the TOML, do not merge. Exit code 2 means fal
could not be reached for that endpoint — re-run; it is not a verdict.

## Contract

See [ENGINE_CONTRACT.md](https://github.com/vivid-allusion/studiolot/blob/main/docs/architecture/ENGINE_CONTRACT.md)
in the studiolot repo for the full Engine specification.

## License

MIT, see [LICENSE](LICENSE).
