"""Persistent multi-provider LLM configuration and credential storage."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError, field_validator


class LLMConfigError(RuntimeError):
    code = "llm_configuration_error"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code


class LLMConfigurationRequired(LLMConfigError):
    code = "llm_configuration_required"


class ModelSelection(BaseModel):
    provider_id: str
    model_id: str


class ModelCapabilities(BaseModel):
    tool_calling: bool | None = None
    vision: bool | None = None
    reasoning: bool | None = None
    context_window: int | None = None
    verification: Literal["unknown", "verified", "unsupported"] = "unknown"


class ModelRef(BaseModel):
    id: str
    provider_id: str | None = None
    model_id: str | None = None
    display_name: str | None = None
    api_mode: Literal["chat_completions", "responses"] | None = None
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    source: Literal["manual", "discovered", "catalog"] = "manual"

    @field_validator("id")
    @classmethod
    def validate_model_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("model id cannot be empty")
        return value


class CredentialRef(BaseModel):
    source: Literal["stored", "env"]
    env_var: str | None = None


class ProviderProfile(BaseModel):
    id: str
    name: str
    preset: str
    enabled: bool = True
    connection: dict[str, Any] = Field(default_factory=dict)
    credential_refs: dict[str, CredentialRef] = Field(default_factory=dict)
    models: list[ModelRef] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value):
            raise ValueError("provider id must use letters, numbers, dots, underscores, or dashes")
        return value

    @field_validator("name", "preset")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value cannot be empty")
        return value


class LLMDefaults(BaseModel):
    default_model: ModelSelection | None = None
    fast_model: ModelSelection | None = None


class ResolvedModel(BaseModel):
    provider_id: str
    provider_name: str
    preset: str
    model_id: str
    litellm_model: str
    api_mode: Literal["chat_completions", "responses"] = "chat_completions"
    connection: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)


def _field(field_id: str, label: str, *, secret: bool = False, required: bool = False, kind: str = "text"):
    return {"id": field_id, "label": label, "secret": secret, "required": required, "kind": kind}


_API_KEY = _field("api_key", "API key", secret=True, required=True, kind="password")
_BASE_URL = _field("base_url", "Base URL", required=True, kind="url")
_ADVANCED_FIELDS = [
    _field("headers", "Headers (JSON)", kind="json"),
    _field("secret_headers", "Secret headers (JSON)", secret=True, kind="textarea"),
    _field("query_parameters", "Query parameters (JSON)", kind="json"),
    _field("timeout", "Timeout (seconds)", kind="number"),
    _field("max_retries", "Max retries", kind="number"),
]


def _preset(
    preset_id: str,
    name: str,
    category: str,
    prefix: str,
    fields: list[dict[str, Any]] | None = None,
    *,
    base_url: str | None = None,
    api_mode: str = "chat_completions",
    discovery: str | None = None,
    metadata_prefix: str | None = None,
    discovered_model_prefixes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": preset_id,
        "name": name,
        "category": category,
        "litellm_prefix": prefix,
        "fields": fields if fields is not None else [_API_KEY],
        "advanced_fields": _ADVANCED_FIELDS,
        "default_base_url": base_url,
        "api_mode": api_mode,
        "discovery": discovery,
        "metadata_prefix": metadata_prefix or prefix,
        "discovered_model_prefixes": discovered_model_prefixes or [],
    }


PROVIDER_CATALOG = [
    _preset("openai", "OpenAI", "global", "openai", discovery="openai"),
    _preset("anthropic", "Anthropic", "global", "anthropic", discovery="anthropic"),
    _preset("gemini", "Google AI Studio", "global", "gemini", discovery="gemini"),
    _preset("xai", "xAI", "global", "xai"),
    _preset("mistral", "Mistral", "global", "mistral"),
    _preset("cohere", "Cohere", "global", "cohere"),
    _preset("deepseek", "DeepSeek", "global", "deepseek"),
    _preset("openrouter", "OpenRouter", "global", "openrouter", discovery="openai"),
    _preset("groq", "Groq", "global", "groq", discovery="openai"),
    _preset("together", "Together AI", "global", "together_ai", discovery="openai"),
    _preset("fireworks", "Fireworks AI", "global", "fireworks_ai"),
    _preset("cerebras", "Cerebras", "global", "cerebras", discovery="openai"),
    _preset("perplexity", "Perplexity", "global", "perplexity"),
    _preset("nvidia_nim", "NVIDIA NIM", "global", "nvidia_nim", [_API_KEY, _field("base_url", "Base URL", kind="url")]),
    _preset(
        "huggingface",
        "Hugging Face",
        "global",
        "huggingface",
        [_API_KEY, _field("base_url", "Endpoint URL", kind="url")],
    ),
    _preset("vercel_ai_gateway", "Vercel AI Gateway", "global", "vercel_ai_gateway"),
    _preset("novita", "Novita AI", "global", "novita"),
    _preset(
        "minimax",
        "MiniMax Global",
        "global",
        "openai",
        [_API_KEY, _field("base_url", "Base URL", kind="url")],
        base_url="https://api.minimax.io/v1",
        discovery="openai",
        metadata_prefix="minimax",
        discovered_model_prefixes=["MiniMax-M"],
    ),
    _preset(
        "minimaxi",
        "MiniMax 中国 (minimaxi.com)",
        "china",
        "openai",
        [_API_KEY, _field("base_url", "Base URL", kind="url")],
        base_url="https://api.minimaxi.com/v1",
        discovery="openai",
        metadata_prefix="minimax",
        discovered_model_prefixes=["MiniMax-M"],
    ),
    _preset("moonshot", "Moonshot / Kimi", "china", "moonshot"),
    _preset("dashscope", "Alibaba DashScope / Qwen", "china", "dashscope"),
    _preset("volcengine", "Volcengine / Doubao", "china", "volcengine"),
    _preset("zhipu", "Zhipu / GLM", "china", "zai"),
    _preset("siliconflow", "SiliconFlow", "china", "openai", [_API_KEY, _BASE_URL], discovery="openai"),
    _preset(
        "azure",
        "Azure OpenAI / Microsoft Foundry",
        "enterprise",
        "azure",
        [
            _API_KEY,
            _BASE_URL,
            _field("api_version", "API version"),
        ],
    ),
    _preset(
        "bedrock",
        "Amazon Bedrock",
        "enterprise",
        "bedrock",
        [
            _field("aws_access_key_id", "AWS access key", secret=True, kind="password"),
            _field("aws_secret_access_key", "AWS secret key", secret=True, kind="password"),
            _field("aws_session_token", "AWS session token", secret=True, kind="password"),
            _field("region", "AWS region", required=True),
            _field("profile", "AWS profile"),
        ],
    ),
    _preset(
        "vertex_ai",
        "Google Vertex AI",
        "enterprise",
        "vertex_ai",
        [
            _field("project", "Project ID", required=True),
            _field("region", "Region", required=True),
            _field("service_account_json", "Service account JSON", secret=True, kind="textarea"),
        ],
    ),
    _preset("databricks", "Databricks", "enterprise", "databricks", [_API_KEY, _BASE_URL]),
    _preset(
        "watsonx",
        "IBM watsonx",
        "enterprise",
        "watsonx",
        [
            _API_KEY,
            _field("project", "Project ID", required=True),
            _field("base_url", "Base URL", kind="url"),
        ],
    ),
    _preset("cloudflare", "Cloudflare AI Gateway", "enterprise", "openai", [_API_KEY, _BASE_URL], discovery="openai"),
    _preset(
        "ollama",
        "Ollama",
        "local",
        "ollama",
        [_field("base_url", "Base URL", kind="url")],
        base_url="http://localhost:11434",
        discovery="ollama",
    ),
    _preset(
        "lmstudio",
        "LM Studio",
        "local",
        "openai",
        [_field("base_url", "Base URL", kind="url")],
        base_url="http://localhost:1234/v1",
        discovery="openai",
    ),
    _preset(
        "vllm",
        "vLLM",
        "local",
        "openai",
        [_field("base_url", "Base URL", kind="url")],
        base_url="http://localhost:8000/v1",
        discovery="openai",
    ),
    _preset(
        "llamacpp",
        "llama.cpp",
        "local",
        "openai",
        [_field("base_url", "Base URL", kind="url")],
        base_url="http://localhost:8080/v1",
        discovery="openai",
    ),
    _preset(
        "tgi", "Hugging Face TGI", "local", "openai", [_field("base_url", "Base URL", kind="url")], discovery="openai"
    ),
    _preset(
        "xinference", "Xinference", "local", "openai", [_field("base_url", "Base URL", kind="url")], discovery="openai"
    ),
    _preset(
        "openai_compatible", "OpenAI-compatible Chat", "generic", "openai", [_API_KEY, _BASE_URL], discovery="openai"
    ),
    _preset(
        "openai_responses",
        "OpenAI-compatible Responses",
        "generic",
        "openai",
        [_API_KEY, _BASE_URL],
        api_mode="responses",
        discovery="openai",
    ),
    _preset("anthropic_compatible", "Anthropic-compatible", "generic", "anthropic", [_API_KEY, _BASE_URL]),
    _preset("litellm_proxy", "LiteLLM Proxy", "generic", "openai", [_API_KEY, _BASE_URL], discovery="openai"),
    _preset(
        "custom_litellm",
        "Custom LiteLLM provider",
        "generic",
        "",
        [_API_KEY, _field("base_url", "Base URL", kind="url")],
    ),
]

_CATALOG_BY_ID = {item["id"]: item for item in PROVIDER_CATALOG}
_CONFIG_VERSION = 3


class LLMConfigStore:
    """Store non-secret profiles separately from write-only credentials."""

    def __init__(self, workspace_dir: str):
        self.workspace_dir = Path(workspace_dir)
        self.llm_dir = self.workspace_dir / "llm"
        self.config_path = self.llm_dir / "config.json"
        self.secrets_path = self.llm_dir / "secrets.json"
        self.llm_dir.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self._write_json(self.config_path, self._empty_config(), secret=False)
        self._migrate_config()
        if not self.secrets_path.exists():
            self._write_json(self.secrets_path, {}, secret=True)
        else:
            os.chmod(self.secrets_path, 0o600)

    @staticmethod
    def _empty_config() -> dict[str, Any]:
        return {"version": _CONFIG_VERSION, "providers": [], "settings": LLMDefaults().model_dump(mode="json")}

    def _migrate_config(self) -> None:
        config = self._load_config()
        try:
            version = int(config.get("version", 1))
        except (TypeError, ValueError):
            version = 1
        if version >= _CONFIG_VERSION:
            return
        if version < 2:
            for profile in config.get("providers", []):
                if profile.get("preset") != "minimax":
                    continue
                base_url = str((profile.get("connection") or {}).get("base_url") or "").lower()
                if "api.minimax.io" not in base_url:
                    profile["preset"] = "minimaxi"
        if version < 3:
            selected_models = {
                (selection.get("provider_id"), selection.get("model_id"))
                for selection in (config.get("settings") or {}).values()
                if isinstance(selection, dict)
            }
            for profile in config.get("providers", []):
                if profile.get("preset") not in {"minimax", "minimaxi"}:
                    continue
                provider_id = profile.get("id")
                profile["models"] = [
                    model
                    for model in profile.get("models", [])
                    if model.get("source") == "manual"
                    or str(model.get("id", "")).startswith("MiniMax-M")
                    or (provider_id, model.get("id")) in selected_models
                ]
        config["version"] = _CONFIG_VERSION
        self._write_json(self.config_path, config, secret=False)

    @staticmethod
    def _write_json(path: Path, data: Any, *, secret: bool) -> None:
        if path.is_symlink():
            raise LLMConfigError(f"refusing to write symlink: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            mode = 0o600 if secret else 0o644
            os.fchmod(fd, mode)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
            os.chmod(path, mode)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _load_config(self) -> dict[str, Any]:
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else self._empty_config()
        except (OSError, json.JSONDecodeError):
            return self._empty_config()

    def _load_secrets(self) -> dict[str, str]:
        try:
            data = json.loads(self.secrets_path.read_text(encoding="utf-8"))
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def catalog(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in item.items() if k != "litellm_prefix"} for item in PROVIDER_CATALOG]

    def _profiles(self) -> list[ProviderProfile]:
        profiles = []
        for raw in self._load_config().get("providers", []):
            try:
                profiles.append(ProviderProfile.model_validate(raw))
            except Exception:
                continue
        return profiles

    def get_provider(self, provider_id: str) -> ProviderProfile:
        profile = next((item for item in self._profiles() if item.id == provider_id), None)
        if not profile:
            raise LLMConfigError("Provider not found", code="llm_provider_not_found")
        return profile

    def provider_runtime(self, provider_id: str) -> tuple[ProviderProfile, dict[str, Any], dict[str, str]]:
        """Return a profile, its preset descriptor, and resolved write-only credentials."""
        profile = self.get_provider(provider_id)
        preset = _CATALOG_BY_ID[profile.preset]
        secrets = self._load_secrets()
        credentials: dict[str, str] = {}
        for name, ref in profile.credential_refs.items():
            value = secrets.get(f"{profile.id}:{name}") if ref.source == "stored" else os.getenv(ref.env_var or "")
            if value:
                credentials[name] = value
        return profile, preset, credentials

    def list_providers(self) -> list[dict[str, Any]]:
        return [self._public_profile(profile) for profile in self._profiles()]

    def create_provider(self, profile_data: dict[str, Any], credentials: dict[str, Any]) -> dict[str, Any]:
        try:
            profile = ProviderProfile.model_validate(profile_data)
        except ValidationError as exc:
            raise LLMConfigError("Invalid provider profile", code="llm_invalid_configuration") from exc
        if profile.preset not in _CATALOG_BY_ID:
            raise LLMConfigError("Unknown provider preset", code="llm_preset_not_found")
        if any(item.id == profile.id for item in self._profiles()):
            raise LLMConfigError("Provider id already exists", code="llm_provider_exists")
        self._apply_defaults_and_validate(profile)
        self._apply_credentials(profile, credentials or {})
        config = self._load_config()
        config.setdefault("providers", []).append(profile.model_dump(mode="json"))
        self._write_json(self.config_path, config, secret=False)
        return self._public_profile(profile)

    def update_provider(
        self, provider_id: str, changes: dict[str, Any], credentials: dict[str, Any] | None
    ) -> dict[str, Any]:
        config = self._load_config()
        raw_profiles = config.get("providers", [])
        index = next((i for i, item in enumerate(raw_profiles) if item.get("id") == provider_id), None)
        if index is None:
            raise LLMConfigError("Provider not found", code="llm_provider_not_found")
        if "models" in (changes or {}):
            previous_ids = {str(item.get("id")) for item in raw_profiles[index].get("models", [])}
            next_ids = {str(item.get("id")) for item in (changes.get("models") or [])}
            removed_ids = previous_ids - next_ids
            defaults = LLMDefaults.model_validate(config.get("settings", {}))
            for selection in (defaults.default_model, defaults.fast_model):
                if selection and selection.provider_id == provider_id and selection.model_id in removed_ids:
                    raise LLMConfigError("Model is referenced by LLM settings", code="llm_model_in_use")
        merged = {**raw_profiles[index], **(changes or {}), "id": provider_id}
        if "credential_refs" not in changes:
            merged["credential_refs"] = raw_profiles[index].get("credential_refs", {})
        try:
            profile = ProviderProfile.model_validate(merged)
        except ValidationError as exc:
            raise LLMConfigError("Invalid provider profile", code="llm_invalid_configuration") from exc
        if profile.preset not in _CATALOG_BY_ID:
            raise LLMConfigError("Unknown provider preset", code="llm_preset_not_found")
        self._apply_defaults_and_validate(profile)
        if credentials is not None:
            self._apply_credentials(profile, credentials)
        raw_profiles[index] = profile.model_dump(mode="json")
        self._write_json(self.config_path, config, secret=False)
        return self._public_profile(profile)

    def delete_provider(self, provider_id: str) -> None:
        config = self._load_config()
        defaults = LLMDefaults.model_validate(config.get("settings", {}))
        for selection in (defaults.default_model, defaults.fast_model):
            if selection and selection.provider_id == provider_id:
                raise LLMConfigError("Provider is referenced by LLM settings", code="llm_provider_in_use")
        profiles = config.get("providers", [])
        remaining = [item for item in profiles if item.get("id") != provider_id]
        if len(remaining) == len(profiles):
            raise LLMConfigError("Provider not found", code="llm_provider_not_found")
        config["providers"] = remaining
        self._write_json(self.config_path, config, secret=False)
        secrets = self._load_secrets()
        secrets = {key: value for key, value in secrets.items() if not key.startswith(f"{provider_id}:")}
        self._write_json(self.secrets_path, secrets, secret=True)

    def get_settings(self) -> dict[str, Any]:
        return LLMDefaults.model_validate(self._load_config().get("settings", {})).model_dump(mode="json")

    def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        config = self._load_config()
        merged = {**config.get("settings", {}), **(changes or {})}
        try:
            settings = LLMDefaults.model_validate(merged)
        except ValidationError as exc:
            raise LLMConfigError("Invalid LLM settings", code="llm_invalid_configuration") from exc
        for selection in (settings.default_model, settings.fast_model):
            if selection:
                self.resolve(selection)
        config["settings"] = settings.model_dump(mode="json")
        self._write_json(self.config_path, config, secret=False)
        return settings.model_dump(mode="json")

    def resolve_default(self) -> ResolvedModel:
        selection = LLMDefaults.model_validate(self._load_config().get("settings", {})).default_model
        if not selection:
            raise LLMConfigurationRequired("Configure a default LLM before sending messages")
        return self.resolve(selection)

    def resolve_fast(self, fallback: ModelSelection | None = None) -> ResolvedModel:
        settings = LLMDefaults.model_validate(self._load_config().get("settings", {}))
        selection = settings.fast_model or fallback or settings.default_model
        if not selection:
            raise LLMConfigurationRequired("Configure an LLM before sending messages")
        return self.resolve(selection)

    def resolve(self, selection: ModelSelection | dict[str, Any]) -> ResolvedModel:
        selection = ModelSelection.model_validate(selection)
        profile, preset, credentials = self.provider_runtime(selection.provider_id)
        if not profile.enabled:
            raise LLMConfigError("Provider is disabled", code="llm_provider_unavailable")
        model = next((item for item in profile.models if item.id == selection.model_id), None)
        if not model:
            raise LLMConfigError("Model is not configured for this provider", code="llm_model_not_found")
        prefix = preset["litellm_prefix"]
        litellm_model = model.id if not prefix or model.id.startswith(f"{prefix}/") else f"{prefix}/{model.id}"
        connection = dict(profile.connection)
        if preset.get("default_base_url") and not connection.get("base_url"):
            connection["base_url"] = preset["default_base_url"]
        return ResolvedModel(
            provider_id=profile.id,
            provider_name=profile.name,
            preset=profile.preset,
            model_id=model.id,
            litellm_model=litellm_model,
            api_mode=model.api_mode or preset.get("api_mode", "chat_completions"),
            connection=connection,
            credentials=credentials,
            capabilities=model.capabilities,
        )

    def _apply_defaults_and_validate(self, profile: ProviderProfile) -> None:
        preset = _CATALOG_BY_ID[profile.preset]
        if preset.get("default_base_url") and not profile.connection.get("base_url"):
            profile.connection["base_url"] = preset["default_base_url"]
        base_url = profile.connection.get("base_url")
        if base_url:
            parsed = urlparse(str(base_url))
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise LLMConfigError("Base URL must be an HTTP(S) URL", code="llm_invalid_base_url")
        for key in ("headers", "query_parameters"):
            value = profile.connection.get(key)
            if value is not None and not isinstance(value, dict):
                raise LLMConfigError(f"{key} must be an object", code="llm_invalid_configuration")
        try:
            if profile.connection.get("timeout") is not None and float(profile.connection["timeout"]) <= 0:
                raise LLMConfigError("timeout must be greater than zero", code="llm_invalid_configuration")
            if profile.connection.get("max_retries") is not None and int(profile.connection["max_retries"]) < 0:
                raise LLMConfigError("max_retries cannot be negative", code="llm_invalid_configuration")
        except (TypeError, ValueError) as exc:
            raise LLMConfigError("timeout and max_retries must be numbers", code="llm_invalid_configuration") from exc
        seen: set[str] = set()
        unique_models: list[ModelRef] = []
        for model in profile.models:
            if model.id not in seen:
                model.provider_id = profile.id
                model.model_id = model.id
                seen.add(model.id)
                unique_models.append(model)
        profile.models = unique_models

    def _apply_credentials(self, profile: ProviderProfile, updates: dict[str, Any]) -> None:
        secrets = self._load_secrets()
        changed = False
        for name, raw in updates.items():
            raw = raw or {}
            key = f"{profile.id}:{name}"
            if raw.get("clear"):
                profile.credential_refs.pop(name, None)
                changed = secrets.pop(key, None) is not None or changed
                continue
            source = raw.get("source")
            if source == "stored":
                value = str(raw.get("value", ""))
                if not value:
                    raise LLMConfigError(f"Stored credential {name} cannot be empty")
                profile.credential_refs[name] = CredentialRef(source="stored")
                secrets[key] = value
                changed = True
            elif source == "env":
                env_var = str(raw.get("env_var", "")).strip()
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_var):
                    raise LLMConfigError(f"Invalid environment variable for {name}")
                profile.credential_refs[name] = CredentialRef(source="env", env_var=env_var)
                changed = secrets.pop(key, None) is not None or changed
            elif source is not None:
                raise LLMConfigError(f"Unsupported credential source for {name}")
        if changed:
            self._write_json(self.secrets_path, secrets, secret=True)

    def _public_profile(self, profile: ProviderProfile) -> dict[str, Any]:
        data = profile.model_dump(mode="json", exclude={"credential_refs"})
        secrets = self._load_secrets()
        public_credentials: dict[str, Any] = {}
        for name, ref in profile.credential_refs.items():
            if ref.source == "stored":
                value = secrets.get(f"{profile.id}:{name}", "")
                public_credentials[name] = {
                    "source": "stored",
                    "configured": bool(value),
                    "masked": f"••••{value[-4:]}" if value else "",
                }
            else:
                public_credentials[name] = {
                    "source": "env",
                    "env_var": ref.env_var,
                    "configured": bool(ref.env_var and os.getenv(ref.env_var)),
                    "masked": "environment" if ref.env_var and os.getenv(ref.env_var) else "",
                }
        data["credentials"] = public_credentials
        return data
