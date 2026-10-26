#!/usr/bin/env python3
"""Endpoint TOML ↔ live fal schema sync checker.

THE ENDPOINT TOML CATALOG IS THE SINGLE SOURCE OF TRUTH FOR A MODEL'S
INPUTS. Every input the model accepts server-side MUST be declared in the
TOML, and every TOML declaration MUST match the live OpenAPI schema.

Run:

    python scripts/check_schema_sync.py                  # live (no key needed)
    python scripts/check_schema_sync.py --cache-dir DIR   # pinned/offline run

Exit codes: 0 = clean, 1 = catalog drift (do not merge), 2 = an endpoint
could not be fetched (provider/network problem — re-run, it is not a verdict
on the catalog). A model the API reports as gone (HTTP 404, no model, no
schema) is drift, not a fetch problem: a dead row must fail the gate.

The fal catalog is readable without a key: the models API returns each
endpoint's queue OpenAPI document with ``expand=openapi-3.0``. Requests are
spaced and retried on 429 so a full sweep does not trip the rate limit.

Coverage rules (per endpoint):
- The model exists, is ``active`` (not ``deprecated``), and publishes an
  OpenAPI document with an Input schema.
- Every server-side Input property except ``prompt`` is declared as
  ``[general] image_url_param``, a ``[general] slots`` entry, or a
  ``[params.*]`` entry — unless it has no renderable shape (see below).
- Every declared slot exists server-side and is a media (URL) input.
- ``[params.*]`` mirrors the server schema: ``select`` options equal the
  server enum, ``integer``/``number`` min/max equal the server range, and
  defaults equal the server default when the server declares one.
- Nothing undeclared is invented: no ``[params.*]`` entry may name a key the
  schema does not accept, and a required property may not be marked optional.

Unrenderable properties: a property whose only shapes are arrays or objects
has no widget in the studiolot wizard (e.g. ``history``, ``loras``,
``controlnet``). Those are exempt from coverage, and *exactly* that set is
exempt — it is written in the TOML as a single ``# UNRENDERABLE:`` comment and
the checker fails when the comment and the schema disagree. A property whose
schema offers a renderable branch (e.g. ``image_size`` accepts a preset enum
or a ``{width, height}`` object) is renderable and must be declared.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
ENDPOINTS_DIR = REPO_ROOT / "engine_fal" / "endpoints"

MODELS_API = "https://api.fal.ai/v1/models"
TIMEOUT = 30.0
RETRY_ATTEMPTS = 6
REQUEST_SPACING = 0.7
USER_AGENT = "engine-fal-check-schema-sync/0.1 (+https://github.com/vivid-allusion/engine-fal)"

#: Suffixes that mark a schema property as a media (URL) input. fal does not
#: tag media properties with ``format: uri``; the endpoint contracts name them.
MEDIA_SUFFIXES = ("_url", "_urls")

RENDERABLE_TYPES = ("integer", "number", "boolean", "string")


@dataclass
class ModelInput:
    """One model's Input schema, split into the shapes the catalog needs."""

    props: dict[str, dict] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    media: set[str] = field(default_factory=set)
    unrenderable: set[str] = field(default_factory=set)
    shapes: dict[str, dict] = field(default_factory=dict)


def _flatten(node: dict, root: dict, seen: set[str]) -> dict:
    """Resolve one ``$ref``/``allOf``/``anyOf`` fragment into a flat dict."""
    if not isinstance(node, dict):
        return {}
    out: dict = {}
    ref = node.get("$ref")
    if isinstance(ref, str) and ref not in seen:
        target: Any = root
        for part in ref.lstrip("#/").split("/"):
            target = target.get(part) if isinstance(target, dict) else None
            if target is None:
                break
        if isinstance(target, dict):
            out.update(_flatten(target, root, seen | {ref}))
    out.update(
        {
            key: value
            for key, value in node.items()
            if key not in ("$ref", "allOf", "anyOf", "oneOf", "examples")
        }
    )
    for key in ("allOf", "anyOf", "oneOf"):
        for fragment in node.get(key) or []:
            for name, value in _flatten(fragment, root, seen).items():
                out.setdefault(name, value)
    return out


def resolve_refs(node: Any, root: dict) -> dict:
    """Resolve ``$ref``/``allOf``/``anyOf`` fragments into a concrete dict."""
    if not isinstance(node, dict):
        return {}
    return _flatten(node, root, set())


def renderable_branch(node: Any, root: dict) -> dict:
    """Return the concrete shape the wizard can render, or ``{}``.

    ``anyOf``/``oneOf`` unions often offer one renderable shape (a string with
    an enum) beside structured ones (an object with width/height). The
    renderable branch wins; a property with no renderable branch is
    unrenderable and is exempt from coverage.
    """
    resolved = resolve_refs(node, root)
    if resolved.get("type") in RENDERABLE_TYPES:
        return resolved
    if isinstance(node, dict):
        for key in ("anyOf", "oneOf"):
            for fragment in node.get(key) or []:
                candidate = renderable_branch(fragment, root)
                if candidate:
                    for name in ("default", "title", "description"):
                        if name in node:
                            candidate.setdefault(name, node[name])
                    return candidate
    return {}


def is_media(key: str, spec: dict) -> bool:
    """True for URL-valued inputs, scalar or list (``*_url``/``*_urls``)."""
    if not key.endswith(MEDIA_SUFFIXES):
        return False
    if spec.get("type") == "string":
        return True
    if spec.get("type") == "array":
        items = spec.get("items") or {}
        return isinstance(items, dict) and items.get("type") == "string"
    return False


def _input_body(schema: dict) -> dict:
    """Return the Input schema object of a fal queue OpenAPI document."""
    components = (schema.get("components") or {}).get("schemas") or {}
    for operations in (schema.get("paths") or {}).values():
        if not isinstance(operations, dict):
            continue
        post = operations.get("post")
        if not isinstance(post, dict):
            continue
        for content in ((post.get("requestBody") or {}).get("content") or {}).values():
            ref = ((content or {}).get("schema") or {}).get("$ref")
            if isinstance(ref, str):
                candidate = components.get(ref.lstrip("#/").split("/")[-1])
                if isinstance(candidate, dict):
                    return candidate
    for name, value in components.items():
        if name.endswith("Input") and isinstance(value, dict):
            return value
    return {}


def extract_input(schema: dict) -> ModelInput:
    """Split a model's Input properties into renderable, media, unrenderable."""
    body = _input_body(schema)
    raw = body.get("properties") or {}
    model = ModelInput(required=list(body.get("required") or []))
    for key, node in raw.items():
        if not isinstance(node, dict):
            continue
        resolved = resolve_refs(node, schema)
        model.props[key] = resolved
        if is_media(key, resolved):
            model.media.add(key)
            continue
        shape = renderable_branch(node, schema)
        if shape:
            model.shapes[key] = shape
        else:
            model.unrenderable.add(key)
    return model


def _param_type(spec: dict) -> str:
    """The TOML widget type that mirrors a concrete schema shape."""
    ptype = spec.get("type")
    if ptype in ("integer", "number", "string") and spec.get("enum"):
        return "select"
    if ptype == "boolean":
        return "boolean"
    if ptype == "integer":
        return "integer"
    if ptype == "number":
        return "number"
    return "text"


def _as_text(value: Any) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def check_param(key: str, toml_param: dict, spec: dict, errors: list[str]) -> None:
    """Verify a single [params.*] entry against its server shape."""
    want = _param_type(spec)
    if toml_param.get("type") != want:
        errors.append(
            f"param '{key}': server type {want}, TOML type {toml_param.get('type')!r}"
        )
        return
    if want == "select":
        values = [str(option.get("value")) for option in toml_param.get("options", [])]
        if len(values) != len(set(values)):
            errors.append(f"param '{key}': duplicate option values in {values}")
        server = [_as_text(value) for value in spec.get("enum") or []]
        if set(values) != set(server):
            errors.append(f"param '{key}': options {values} != server enum {server}")
    if "default" in spec:
        declared = toml_param.get("default")
        expected = _as_text(spec["default"]) if want == "select" else spec["default"]
        if (str(declared) if want == "select" else declared) != expected:
            errors.append(
                f"param '{key}': default {declared!r} != server default {spec['default']!r}"
            )
    for schema_key, toml_key in (("minimum", "min"), ("maximum", "max")):
        if schema_key not in spec:
            continue
        declared = toml_param.get(toml_key)
        if declared != spec[schema_key]:
            errors.append(
                f"param '{key}': {toml_key} {declared!r} != "
                f"server {schema_key} {spec[schema_key]!r}"
            )


def declared_unrenderable(toml_path: Path) -> set[str]:
    """Read the ``# UNRENDERABLE:`` comment out of a TOML file."""
    found: set[str] = set()
    for line in toml_path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("# UNRENDERABLE:"):
            found |= {token for token in stripped.split(":", 1)[1].split() if token}
    return found


def check_toml(toml_path: Path, model: ModelInput) -> list[str]:
    """Verify one endpoint TOML against its live schema. Returns violations."""
    errors: list[str] = []
    data = tomllib.loads(toml_path.read_text())
    general = data.get("general", {})
    params = data.get("params", {})
    primary = str(general.get("image_url_param") or "")
    slots = [str(slot) for slot in general.get("slots") or []]

    if len(slots) != len(set(slots)):
        errors.append("duplicate keys in [general] slots")
    if primary and primary in slots:
        errors.append(f"'{primary}' is both image_url_param and a slot")

    if primary:
        if primary not in model.props:
            errors.append(f"image_url_param '{primary}' does not exist server-side")
        elif primary not in model.media:
            errors.append(f"image_url_param '{primary}' is not a media (URL) input")
    for slot in slots:
        if slot not in model.props:
            errors.append(f"slot '{slot}' does not exist server-side")
        elif slot not in model.media:
            errors.append(f"slot '{slot}' is not a media (URL) input")

    undeclared = sorted(
        key
        for key in model.props
        if key != "prompt"
        and key != primary
        and key not in slots
        and key not in params
        and key not in model.unrenderable
    )
    if undeclared:
        errors.append(f"server param(s) not declared: {undeclared}")

    declared = declared_unrenderable(toml_path)
    if declared != model.unrenderable:
        missing = sorted(model.unrenderable - declared)
        invented = sorted(declared - model.unrenderable)
        errors.append(
            f"UNRENDERABLE comment {sorted(declared)} != the schema's unrenderable "
            f"properties {sorted(model.unrenderable)}"
            + (f" (missing {missing})" if missing else "")
            + (f" (invented {invented})" if invented else "")
        )

    for key, toml_param in params.items():
        if key not in model.props:
            errors.append(f"param '{key}' does not exist server-side")
            continue
        if key in model.media:
            errors.append(
                f"param '{key}' is a media (URL) input — declare it in [general] instead"
            )
            continue
        if key in model.unrenderable:
            errors.append(f"param '{key}' has no renderable shape — list it as UNRENDERABLE")
            continue
        check_param(key, toml_param, model.shapes[key], errors)
        if key in model.required and toml_param.get("optional"):
            errors.append(f"param '{key}' is required server-side but marked optional")

    duration_key = str(general.get("duration_param_name") or "")
    if duration_key and duration_key not in model.props:
        errors.append(f"duration_param_name '{duration_key}' does not exist server-side")
    return errors


def load_schema(
    endpoint: str, cache_dir: Path | None
) -> tuple[dict, str, bool]:
    """Fetch (or read from cache) a model's OpenAPI document and status.

    The third value is True when the failure was the provider being
    unreachable rather than the endpoint being gone: callers must not report
    a network blip as catalog drift.
    """
    if cache_dir is not None:
        cached = cache_dir / (endpoint.replace("/", "__") + ".json")
        if not cached.is_file():
            return {}, f"no cached schema at {cached}", False
        payload = json.loads(cached.read_text())
        return payload.get("openapi") or {}, payload.get("status", "active"), False
    params = urllib.parse.urlencode({"endpoint_id": endpoint, "expand": "openapi-3.0"})
    request = urllib.request.Request(
        f"{MODELS_API}?{params}", headers={"User-Agent": USER_AGENT}
    )
    for attempt in range(RETRY_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                payload = json.loads(response.read().decode("utf-8"))
            models = payload.get("models") or []
            if not models:
                if attempt + 1 < RETRY_ATTEMPTS:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                return {}, "the models API returned no model", False
            model = models[0]
            time.sleep(REQUEST_SPACING)
            status = str((model.get("metadata") or {}).get("status") or "")
            if not model.get("openapi"):
                return (
                    {},
                    f"the model is {status or 'unlisted'} and publishes no OpenAPI schema",
                    False,
                )
            return model["openapi"], status, False
        except urllib.error.HTTPError as exc:
            # 429 and 5xx are the provider's transient answers, not drift; a
            # 404 (or any other 4xx) means the endpoint itself is gone.
            if exc.code == 429 or exc.code >= 500 or exc.code == 408:
                if attempt + 1 < RETRY_ATTEMPTS:
                    time.sleep(2.0 * (attempt + 1))
                    continue
            return {}, f"HTTP {exc.code} from the models API", exc.code >= 500
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            if attempt + 1 < RETRY_ATTEMPTS:
                time.sleep(1.5 * (attempt + 1))
                continue
            return {}, f"network error: {reason}", True
    return {}, "rate limited by the models API", True


def read_endpoints() -> list[tuple[Path, dict]]:
    return [
        (path, tomllib.loads(path.read_text()))
        for path in sorted(ENDPOINTS_DIR.rglob("*.toml"))
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--endpoint", help="check only this endpoint (e.g. fal-ai/nano-banana-2.1)"
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="read pinned <endpoint>.json payloads instead of the live API",
    )
    args = parser.parse_args(argv)

    endpoints = read_endpoints()
    if not endpoints:
        print("No endpoint TOMLs found — nothing to check.", file=sys.stderr)
        return 1

    failures = 0
    unreachable = 0
    checked = 0
    for toml_path, data in endpoints:
        endpoint = str(data.get("endpoint") or "")
        if args.endpoint and endpoint != args.endpoint:
            continue
        checked += 1
        schema, status, blip = load_schema(endpoint, args.cache_dir)
        if not schema:
            if blip:
                print(f"RETRY {toml_path.name} ({endpoint}): {status} — not a verdict")
                unreachable += 1
            else:
                print(f"FAIL {toml_path.name} ({endpoint}): {status}")
                failures += 1
            continue
        if args.cache_dir is None and status and status != "active":
            print(f"FAIL {toml_path.name} ({endpoint}): status is {status!r}, not 'active'")
            failures += 1
            continue
        model = extract_input(schema)
        violations = check_toml(toml_path, model)
        if violations:
            failures += 1
            print(f"FAIL {toml_path.name} ({endpoint}):")
            for violation in violations:
                print(f"    - {violation}")
        else:
            print(f"OK   {toml_path.name} ({endpoint}) — {len(model.props)} server inputs")

    print(f"\n{checked} endpoint(s) checked, {failures} drifted")
    if unreachable:
        print(
            f"{unreachable} endpoint(s) could not be fetched — re-run; "
            "that is the provider, not the catalog",
            file=sys.stderr,
        )
    if failures:
        return 1
    return 2 if unreachable else 0


if __name__ == "__main__":
    sys.exit(main())
