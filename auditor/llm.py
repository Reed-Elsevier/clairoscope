"""LLM providers. The auditor needs one capability: 'give me JSON that matches this schema'.

Core provider: Claude on Amazon Bedrock. Configure with environment variables (see .env.example):

  AUDITOR_LLM            bedrock (default when AWS settings are present) | claude | openai | off
  AWS_REGION             required for Bedrock (no silent default)
  AUDITOR_BEDROCK_MODEL  default anthropic.claude-opus-5-5
  AUDITOR_BEDROCK_API    mantle | invoke | auto (default auto: inference-profile / ARN / versioned IDs -> invoke)
  Auth                   AWS credential chain (env keys, AWS_PROFILE/SSO, instance or task role)
                         or a Bedrock bearer token in AWS_BEARER_TOKEN_BEDROCK

Bedrock's Messages endpoint does not support structured outputs, so for Bedrock the schema is put in the
prompt and every reply is validated here (with one repair round-trip). The Claude API path uses native
structured outputs. Run `python scripts/check_llm.py` to verify a setup.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Protocol


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    name: str

    def json(self, system: str, user: str, schema: dict) -> dict: ...


# ---------------------------------------------------------------- JSON helpers
def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise LLMError("Model did not return JSON")
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError as e:
            raise LLMError(f"Model returned malformed JSON: {e}") from e


_PY_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def validate(obj, schema: dict, path: str = "$") -> list[str]:
    """Minimal JSON-Schema check (type, required, enum, items, properties) for the auditor's schemas."""
    errors: list[str] = []
    types = schema.get("type")
    types = types if isinstance(types, list) else [types] if types else []
    if types:
        def matches(t: str) -> bool:
            if t in ("number", "integer"):
                return isinstance(obj, (int, float)) and not isinstance(obj, bool)
            return isinstance(obj, _PY_TYPES[t])
        if not any(matches(t) for t in types):
            return [f"{path}: expected {'/'.join(types)}, got {type(obj).__name__}"]
    if "enum" in schema and obj not in schema["enum"]:
        errors.append(f"{path}: {obj!r} not in {schema['enum']}")
    if isinstance(obj, dict):
        for key in schema.get("required", []):
            if key not in obj:
                errors.append(f"{path}: missing '{key}'")
        for key, sub in schema.get("properties", {}).items():
            if key in obj:
                errors += validate(obj[key], sub, f"{path}.{key}")
    if isinstance(obj, list) and "items" in schema:
        for i, item in enumerate(obj):
            errors += validate(item, schema["items"], f"{path}[{i}]")
    return errors


def _schema_instruction(schema: dict) -> str:
    return ("\n\nRespond with ONLY one JSON object: no prose, no code fences. It must conform to this JSON "
            "Schema:\n" + json.dumps(schema))


# ---------------------------------------------------------------- Claude (API or Bedrock)
class ClaudeLLM:
    """Claude through the Anthropic SDK: first-party API, or Amazon Bedrock (Messages endpoint or InvokeModel)."""

    INVOKE_PREFIXES = ("global.", "us.", "eu.", "apac.", "jp.", "au.", "arn:")

    def __init__(self, bedrock: bool = False) -> None:
        import anthropic

        self._a = anthropic
        self.bedrock = bedrock
        self.effort: str | None = os.environ.get("AUDITOR_EFFORT", "medium") or None
        self.fallback_model = os.environ.get("AUDITOR_FALLBACK_MODEL") or None
        if bedrock:
            self.region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
            if not self.region:
                raise LLMError("Set AWS_REGION (e.g. us-east-1) for Bedrock.")
            self.model = os.environ.get("AUDITOR_BEDROCK_MODEL", "anthropic.claude-opus-5-5")
            api = os.environ.get("AUDITOR_BEDROCK_API", "auto").lower()
            if api == "auto":
                api = "invoke" if (self.model.startswith(self.INVOKE_PREFIXES) or ":" in self.model) else "mantle"
            self.api = api
            token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
            self.auth = "bearer token" if token else _aws_auth_source()
            if api == "mantle":
                if token:  # bearer tokens go to the Mantle endpoint through the standard client
                    self.client = anthropic.Anthropic(
                        base_url=f"https://bedrock-mantle.{self.region}.api.aws/anthropic", api_key=token)
                else:
                    self.client = anthropic.AnthropicBedrockMantle(aws_region=self.region)
            elif api == "invoke":
                self.client = (anthropic.AnthropicBedrock(api_key=token, aws_region=self.region) if token
                               else anthropic.AnthropicBedrock(aws_region=self.region))
            else:
                raise LLMError(f"AUDITOR_BEDROCK_API must be mantle, invoke or auto (got '{api}')")
            self.structured = False  # not supported on Bedrock's Messages endpoint; validate locally
            self.name = f"Claude on Bedrock · {self.model} · {self.region} · {api} · {self.auth}"
        else:
            self.client = anthropic.Anthropic()
            self.model = os.environ.get("AUDITOR_CLAUDE_MODEL", "claude-opus-5-5")
            self.structured = True
            self.api = "messages"
            self.auth = "API key"
            self.name = f"Claude API · {self.model}"
        self.last_latency_s: float | None = None

    # -- one request, with graceful degradation for optional parameters
    def _create(self, system: str, messages: list, schema: dict | None, model: str | None = None):
        a = self._a
        kwargs = dict(model=model or self.model, max_tokens=16000, system=system, messages=messages)
        oc = {}
        if self.effort:
            oc["effort"] = self.effort
        if schema is not None and self.structured:
            oc["format"] = {"type": "json_schema", "schema": schema}
        if oc:
            kwargs["output_config"] = oc
        for _ in range(3):
            try:
                if not self.bedrock and "output_config" in kwargs:
                    try:  # Anthropic server-side refusal fallback; plain call if the account lacks the beta
                        return self.client.beta.messages.create(
                            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
                    except a.BadRequestError as e:
                        if "fallback" not in str(e).lower():
                            raise
                return self.client.messages.create(**kwargs)
            except a.BadRequestError as e:
                msg = str(e).lower()
                if "output_config" in kwargs and ("effort" in msg or "output_config" in msg):
                    kwargs.pop("output_config")  # older model or endpoint: drop optional knobs, keep going
                    self.effort, self.structured = None, False
                    continue
                raise LLMError(f"Bad request: {e.message}") from e
            except a.AuthenticationError as e:
                raise LLMError(f"Authentication failed ({self.auth}): {e.message}") from e
            except a.PermissionDeniedError as e:
                raise LLMError(f"Access denied to {kwargs['model']}: enable model access in the Bedrock console "
                               f"and check IAM (bedrock-mantle:CreateInference / bedrock:InvokeModel). {e.message}") from e
            except a.NotFoundError as e:
                raise LLMError(f"Model '{kwargs['model']}' not found in {getattr(self, 'region', 'this account')}.") from e
            except a.RateLimitError as e:
                raise LLMError("Rate limited; retry in a minute.") from e
            except a.APIStatusError as e:
                raise LLMError(f"API error {e.status_code}: {e.message}") from e
            except a.APIConnectionError as e:
                raise LLMError("Cannot reach the endpoint (network / region).") from e
        raise LLMError("Request failed after removing optional parameters.")

    def _text(self, system: str, messages: list, schema: dict | None) -> str:
        t0 = time.time()
        resp = self._create(system, messages, schema)
        if resp.stop_reason == "refusal" and self.fallback_model:  # client-side fallback (Bedrock has no server-side)
            resp = self._create(system, messages, schema, model=self.fallback_model)
        self.last_latency_s = round(time.time() - t0, 1)
        if resp.stop_reason == "refusal":
            raise LLMError("The model declined this request.")
        if resp.stop_reason == "max_tokens":
            raise LLMError("The model ran out of output tokens.")
        return "".join(b.text for b in resp.content if b.type == "text")

    def json(self, system: str, user: str, schema: dict) -> dict:
        content = user if self.structured else user + _schema_instruction(schema)
        messages = [{"role": "user", "content": content}]
        text = self._text(system, messages, schema)
        try:
            obj = _extract_json(text)
            errors = validate(obj, schema)
        except LLMError as e:
            obj, errors = None, [str(e)]
        if errors:  # one repair round-trip
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": "That reply failed validation: " + "; ".join(errors[:8])
                          + ". Return the corrected JSON object only."}]
            obj = _extract_json(self._text(system, messages, schema))
            errors = validate(obj, schema)
            if errors:
                raise LLMError("JSON failed schema validation: " + "; ".join(errors[:5]))
        return obj


def _aws_auth_source() -> str:
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        return "AWS keys (env)"
    if os.environ.get("AWS_PROFILE"):
        return f"AWS profile '{os.environ['AWS_PROFILE']}'"
    if (Path.home() / ".aws" / "credentials").exists() or (Path.home() / ".aws" / "config").exists():
        return "AWS default profile"
    return "AWS credential chain (role)"


# ---------------------------------------------------------------- OpenAI (optional)
class OpenAILLM:
    def __init__(self) -> None:
        from openai import OpenAI

        self.client = OpenAI()
        self.model = os.environ.get("AUDITOR_OPENAI_MODEL", "gpt-4.1")
        self.name = f"OpenAI · {self.model}"

    def json(self, system: str, user: str, schema: dict) -> dict:
        try:
            resp = self.client.chat.completions.create(
                model=self.model, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user + _schema_instruction(schema)}])
        except Exception as e:  # surface any provider error to the UI
            raise LLMError(f"OpenAI error: {e}") from e
        obj = _extract_json(resp.choices[0].message.content or "")
        errors = validate(obj, schema)
        if errors:
            raise LLMError("JSON failed schema validation: " + "; ".join(errors[:5]))
        return obj


# ---------------------------------------------------------------- factory
def configured_provider() -> str:
    choice = os.environ.get("AUDITOR_LLM", "").lower()
    if choice:
        return choice
    aws = os.environ.get("AWS_BEARER_TOKEN_BEDROCK") or os.environ.get("AWS_ACCESS_KEY_ID") \
        or os.environ.get("AWS_PROFILE")
    if aws and (os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")):
        return "bedrock"
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "claude"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "off"


def get_llm() -> LLM | None:
    choice = configured_provider()
    if choice == "off":
        return None
    if choice == "bedrock":
        return ClaudeLLM(bedrock=True)
    if choice == "claude":
        return ClaudeLLM()
    if choice == "openai":
        return OpenAILLM()
    raise LLMError(f"Unknown AUDITOR_LLM '{choice}' (use bedrock, claude, openai or off)")
