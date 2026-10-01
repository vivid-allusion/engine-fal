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
    "parameters": {"aspect_ratio": "16:9"},
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
│   ├── engine.py       ← Engine class wrapping the fal SDK
│   ├── datatypes.py    ← InputFile, OutputFile, ProgressEvent, EngineError
│   ├── metadata.py     ← Zero-dependency identity constants
│   ├── __init__.py     ← Re-exports + list_standby_profiles(media_type)
│   ├── endpoints/
│   │   ├── IMG-Models/    ← image model TOMLs (flux, ideogram, sdxl, recraft, nano-banana family)
│   │   ├── VID-Models/    ← video model TOMLs (mochi, ltx, minimax, h3-max-turbo, seedance-2.5)
│   │   ├── TXT-Models/    ← .gitkeep (fal.ai is image/video only)
│   │   └── Vision-Models/ ← .gitkeep (fal.ai is image/video only)
│   └── profiles/standby/{IMG,VID}/ ← publishable standby YAML profiles by media type
├── pyproject.toml      ← pip install definition
├── requirements.txt    ← fal-client>=1.0
└── tests/
    ├── conftest.py     ← offline guard (no live schema fetch in tests)
    └── test_engine.py  ← unit tests
```

The engine mirrors the behaviour of the gold engine (`engine-replicate`):
error results carry the destination filename they would have used, transient
provider failures retry with backoff, billing/auth failures abort the batch,
streamed outputs are saved under the extension their bytes claim, and scalar
parameters are coerced against the endpoint's live openapi Input schema.
Named reference slots (Seedance/H3) route to their own keys, `slot_scalar`
marks string-valued slots, and the generator-only `fps` key never reaches the
provider.

## Contract

See [ENGINE_CONTRACT.md](https://github.com/vivid-allusion/studiolot/blob/main/docs/architecture/ENGINE_CONTRACT.md)
in the studiolot repo for the full Engine specification.
