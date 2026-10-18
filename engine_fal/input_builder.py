"""Fal request payload construction and schema shaping.
"""

import json
import urllib.parse
import urllib.request
from typing import Any

from .datatypes import EngineError, InputFile

_MODELS_API = "https://api.fal.ai/v1/models"


class FalInputBuilder:
    """Build provider inputs from profiles and markdown metadata."""

    def _build_fal_input(
        self, params: dict, prompt: str, item: InputFile, media_type: str
    ) -> dict:
        props = self._input_props()
        fal_input = {k: self._coerce_param_value(k, v, props) for k, v in params.items()}
        fal_input.pop("prompt_prefix", None)
        fal_input.pop("prompt_suffix", None)
        # fps is generator metadata (frame rate), never a provider input.
        fal_input.pop("fps", None)

        fal_input["prompt"] = prompt
        ref_key = self._reference_key()
        if item.reference_urls:
            if self._profile.get("reference_param_multi", False):
                fal_input[ref_key] = list(item.reference_urls)
            else:
                fal_input[ref_key] = self._coerce_media_value(ref_key, item.reference_urls)

        slot_scalar = set(self._profile.get("slot_scalar", []) or [])
        for slot, urls in item.references.items():
            if not urls and slot not in self._required_slots():
                continue
            fal_input[slot] = self._shape_slot_value(slot, urls, slot_scalar)

        duration = item.metadata.get("duration")
        if duration is not None:
            key = str(self._profile.get("duration_param_name", "duration"))
            fal_input[key] = self._coerce_param_value(key, duration, props)

        return fal_input

    def _coerce_media_value(self, key: str, urls: list[str]) -> str | list[str]:
        """Shape URLs for a media parameter against the model's schema.

        String-typed params (image_url, start_image_url, ...) receive a single
        URL; array-typed params (image_urls, ...) receive the full list. When
        the schema is unknown, a single URL degrades to a string and multiple
        URLs stay a list.

        Raises:
            EngineError: multiple URLs routed to a string-typed param — the
                provider can only accept one, so the extra media must never
                be silently dropped.
        """
        if not urls:
            return []
        param = self._input_props().get(key, {})
        param_type = param.get("type") if isinstance(param, dict) else None
        if param_type == "array":
            return urls
        if param_type == "string":
            if len(urls) > 1:
                raise EngineError(
                    f"Param '{key}' accepts a single media URL but {len(urls)} "
                    f"were provided — fix the markdown: {urls}"
                )
            return urls[0]
        return urls[0] if len(urls) == 1 else urls

    def _coerce_param_value(self, key: str, value: Any, props: dict) -> Any:
        """Coerce a scalar parameter against the model's schema type.

        Integer-typed params (duration, fps, ...) receive an int when the
        profile ships a numeric string; number/boolean params coerce
        likewise. Non-convertible values (e.g. token durations like
        ``auto``) pass through verbatim, as do all values when the schema
        is unavailable.
        """
        param = props.get(key, {})
        param_type = param.get("type") if isinstance(param, dict) else None
        if not isinstance(value, str):
            return value
        if param_type == "integer":
            try:
                return int(value)
            except ValueError:
                return value
        if param_type == "number":
            try:
                return float(value)
            except ValueError:
                return value
        if param_type == "boolean":
            lowered = value.strip().lower()
            if lowered in {"true", "yes", "1", "on"}:
                return True
            if lowered in {"false", "no", "0", "off"}:
                return False
        return value

    def _fetch_input_props(self) -> dict:
        """Fetch the endpoint's OpenAPI schema from the fal models API.

        ``GET /v1/models?endpoint_id=<endpoint>&expand=openapi-3.0`` returns the
        model's OpenAPI document; its POST request body schema names the Input
        properties. The API key is optional but sent when present.
        """
        endpoint = str(self._profile.get("endpoint", ""))
        if not endpoint:
            return {}
        url = f"{_MODELS_API}?" + urllib.parse.urlencode(
            {"endpoint_id": endpoint, "expand": "openapi-3.0"}
        )
        request = urllib.request.Request(url)
        key = self._resolve_api_key()
        if key:
            request.add_header("Authorization", f"Key {key}")
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = payload.get("models") or []
        if not models:
            return {}
        return self._parse_input_props(models[0].get("openapi") or {})

    def _input_props(self) -> dict:
        """Best-effort fetch of the endpoint's Input JSON-schema properties.

        Used to shape media params (single URL vs list) and scalar params
        (int vs float vs string) against the model schema. ``$ref``/``allOf``
        fragments are resolved to concrete type dicts. Cached per Engine
        instance; returns {} when the schema is unavailable.
        """
        if self._input_props_cache is not None:
            return self._input_props_cache
        try:
            props = self._fetch_input_props()
        except Exception:
            props = {}
        if not isinstance(props, dict):
            props = {}
        self._input_props_cache = props
        return props

    def _parse_input_props(self, schema: dict) -> dict:
        """Extract Input properties from a fal OpenAPI document."""
        components = (schema.get("components") or {}).get("schemas") or {}
        raw: dict = {}
        ref = self._request_body_ref(schema)
        if ref and isinstance(components.get(ref), dict):
            raw = components[ref].get("properties") or {}
        if not raw:
            for name, body in components.items():
                if name.endswith("Input") and isinstance(body, dict):
                    raw = body.get("properties") or {}
                    break
        return {
            key: self._resolve_schema_refs(value, schema)
            for key, value in raw.items()
            if isinstance(value, dict)
        }

    def _reference_key(self) -> str:
        """Primary input key: profile's image_url_param, else reference_param,
        else the provider default image_url."""
        for key in ("image_url_param", "reference_param"):
            value = self._profile.get(key)
            if value:
                return str(value)
        return "image_url"

    @staticmethod

    def _request_body_ref(schema: dict) -> str | None:
        """Return the schema name referenced by the POST request body, if any."""
        paths = schema.get("paths") or {}
        for operations in paths.values():
            if not isinstance(operations, dict):
                continue
            post = operations.get("post")
            if not isinstance(post, dict):
                continue
            content = (post.get("requestBody") or {}).get("content") or {}
            for body in content.values():
                ref = ((body or {}).get("schema") or {}).get("$ref")
                if isinstance(ref, str):
                    return ref.lstrip("#/").split("/")[-1]
        return None

    def _required_slots(self) -> set[str]:
        return set(self._profile.get("required_slots") or [])

    def _resolve_schema_refs(self, node: Any, root: dict) -> Any:
        """Resolve ``$ref``/``allOf`` fragments into a concrete schema dict."""
        if not isinstance(node, dict):
            return node
        resolved: dict = {}
        if "$ref" in node and isinstance(node["$ref"], str):
            target: Any = root
            for part in node["$ref"].lstrip("#/").split("/"):
                target = target.get(part) if isinstance(target, dict) else None
                if target is None:
                    break
            if isinstance(target, dict):
                resolved.update(self._resolve_schema_refs(target, root))
        resolved.update({k: v for k, v in node.items() if k not in ("$ref", "allOf")})
        for fragment in node.get("allOf", []):
            merged = self._resolve_schema_refs(fragment, root)
            if isinstance(merged, dict):
                for k, v in merged.items():
                    resolved.setdefault(k, v)
        return resolved

    def _shape_slot_value(self, key: str, urls: list[str], slot_scalar: set[str]):
        """Shape a named secondary-media slot.

        String-valued slots (``slot_scalar`` in the profile, or a schema that
        says ``string``) receive a single URL; every other named slot is an
        array and receives the full list. Falls back to a list when neither the
        profile nor the live schema says otherwise.
        """
        if not urls:
            return []
        if key in slot_scalar:
            return urls[0]
        param = self._input_props().get(key, {})
        param_type = param.get("type") if isinstance(param, dict) else None
        if param_type == "string":
            return urls[0]
        return list(urls)
