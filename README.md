# engine-fal — Fal.ai Engine for studiolot

Engine wrapping the [Fal.ai](https://fal.ai) API behind studiolot's uniform
Engine interface. Vehicles (studio applications like Frame Composer and
Motion Conductor) call this Engine instead of importing the Fal SDK directly.

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
├── engine.py          ← Engine class wrapping fal SDK
├── datatypes.py       ← InputFile, OutputFile, ProgressEvent, EngineError
├── metadata.py        ← Zero-dependency identity constants
├── __init__.py        ← Re-exports
├── pyproject.toml     ← pip install definition
├── requirements.txt   ← fal>=1.0
├── endpoints/
│   ├── IMG-Models/    ← 5 image models (flux, ideogram, sdxl, recraft)
│   ├── VID-Models/    ← 3 video models (mochi, ltx, minimax)
│   ├── TXT-Models/    ← .gitkeep (fal.ai is image/video only)
│   └── Vision-Models/ ← .gitkeep (fal.ai is image/video only)
└── tests/
    └── test_engine.py ← 20 unit tests
```

## Contract

See [ENGINE_CONTRACT.md](https://github.com/vivid-allusion/studiolot/blob/main/docs/architecture/ENGINE_CONTRACT.md)
in the studiolot repo for the full Engine specification.
