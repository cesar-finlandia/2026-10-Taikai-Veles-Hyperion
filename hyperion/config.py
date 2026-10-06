"""Runtime settings, read once from the environment (and .env when running locally)."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal, Mapping

HitlMode = Literal["strict", "destructive"]


@dataclass(frozen=True)
class Settings:
    """All tunables of the service. Every field has an environment variable (see ENV_MAP)."""
    api_key: str = ""
    llm_base_url: str = "https://legion1.di.uoa.gr/v1"
    llm_model: str = "llama3.1"
    embed_model: str = "nomic-embed-text"
    ide_backend_url: str = "http://localhost:3001/api"
    llm_concurrency: int = 3
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 4
    llm_backoff_base_s: float = 0.6
    turn_llm_budget: int = 6
    turn_deadline_s: float = 90.0
    max_text_chars: int = 4000
    session_ttl_s: int = 21600
    session_max: int = 1000
    pending_ttl_s: int = 900
    hitl_mode: HitlMode = "strict"
    landing_timeout_s: float = 6.0
    landing_interval_s: float = 0.4
    repair_rounds: int = 2
    max_actions_per_turn: int = 5
    max_content_chars: int = 60000
    auto_create_parents: bool = True
    index_path: Path = Path("data/index.json")
    data_dir: Path = Path("data")
    rag_top_k: int = 5
    rag_dense_min: float = 0.50
    rag_lexical_min: float = 0.34
    rag_bm25_min: float = 2.0
    debug_endpoints: bool = True
    log_level: str = "INFO"
    feature_rag: bool = True
    feature_guard: bool = True
    feature_memory: bool = True
    feature_templates: bool = True
    feature_repair: bool = True
    feature_hitl: bool = True


ENV_MAP: dict[str, str] = {
    "api_key": "API_KEY",
    "llm_base_url": "LLM_BASE_URL",
    "llm_model": "LLM_MODEL",
    "embed_model": "EMBED_MODEL",
    "ide_backend_url": "IDE_BACKEND_URL",
    "llm_concurrency": "LLM_CONCURRENCY",
    "llm_timeout_s": "LLM_TIMEOUT_S",
    "llm_max_retries": "LLM_MAX_RETRIES",
    "turn_llm_budget": "TURN_LLM_BUDGET",
    "turn_deadline_s": "TURN_DEADLINE_S",
    "pending_ttl_s": "PENDING_TTL_S",
    "hitl_mode": "HITL_MODE",
    "landing_timeout_s": "LANDING_TIMEOUT_S",
    "index_path": "INDEX_PATH",
    "data_dir": "DATA_DIR",
    "rag_dense_min": "RAG_DENSE_MIN",
    "rag_lexical_min": "RAG_LEXICAL_MIN",
    "rag_bm25_min": "RAG_BM25_MIN",
    "debug_endpoints": "DEBUG_ENDPOINTS",
    "log_level": "LOG_LEVEL",
    "feature_rag": "FEATURE_RAG",
    "feature_guard": "FEATURE_GUARD",
    "feature_memory": "FEATURE_MEMORY",
    "feature_templates": "FEATURE_TEMPLATES",
    "feature_repair": "FEATURE_REPAIR",
    "feature_hitl": "FEATURE_HITL",
}

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _strip_key(raw: str) -> str:
    s = raw.strip()
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        s = s[1:-1].strip()
    return s


def _parse_bool(raw: str, default: bool) -> bool:
    low = raw.strip().lower()
    if low in _TRUE_VALUES:
        return True
    if low in _FALSE_VALUES:
        return False
    return default


def _parse_int(raw: str, default: int, *, minimum: int | None = None) -> int:
    try:
        v = int(raw.strip())
    except (ValueError, AttributeError):
        return default
    if minimum is not None and v < minimum:
        return default
    return v


def _parse_float(raw: str, default: float, *, minimum: float | None = None,
                 strict_positive: bool = False) -> float:
    try:
        v = float(raw.strip())
    except (ValueError, AttributeError):
        return default
    if strict_positive and not v > 0:
        return default
    if minimum is not None and v < minimum:
        return default
    return v


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from `env`; when env is None read os.environ after load_dotenv()."""
    if env is None:
        try:
            import dotenv
            dotenv.load_dotenv(override=False)
        except Exception:
            pass
        env = os.environ

    d = Settings()

    def get(var: str) -> str | None:
        v = env.get(var)
        if v is None:
            return None
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    values: dict[str, object] = {}

    raw = get("API_KEY")
    values["api_key"] = _strip_key(raw) if raw is not None else d.api_key

    raw = get("LLM_BASE_URL")
    values["llm_base_url"] = raw.strip().rstrip("/") if raw is not None else d.llm_base_url

    raw = get("LLM_MODEL")
    values["llm_model"] = raw if raw is not None else d.llm_model

    raw = get("EMBED_MODEL")
    values["embed_model"] = raw if raw is not None else d.embed_model

    raw = get("IDE_BACKEND_URL")
    values["ide_backend_url"] = raw.strip().rstrip("/") if raw is not None else d.ide_backend_url

    raw = get("LLM_CONCURRENCY")
    values["llm_concurrency"] = _parse_int(raw, d.llm_concurrency, minimum=1) if raw is not None else d.llm_concurrency

    raw = get("LLM_TIMEOUT_S")
    values["llm_timeout_s"] = _parse_float(raw, d.llm_timeout_s, strict_positive=True) if raw is not None else d.llm_timeout_s

    raw = get("LLM_MAX_RETRIES")
    values["llm_max_retries"] = _parse_int(raw, d.llm_max_retries, minimum=0) if raw is not None else d.llm_max_retries

    raw = get("TURN_LLM_BUDGET")
    values["turn_llm_budget"] = _parse_int(raw, d.turn_llm_budget, minimum=1) if raw is not None else d.turn_llm_budget

    raw = get("TURN_DEADLINE_S")
    values["turn_deadline_s"] = _parse_float(raw, d.turn_deadline_s, strict_positive=True) if raw is not None else d.turn_deadline_s

    raw = get("PENDING_TTL_S")
    values["pending_ttl_s"] = _parse_int(raw, d.pending_ttl_s, minimum=1) if raw is not None else d.pending_ttl_s

    raw = get("HITL_MODE")
    if raw is not None:
        v = raw.strip().lower()
        values["hitl_mode"] = v if v in ("strict", "destructive") else "strict"
    else:
        values["hitl_mode"] = d.hitl_mode

    raw = get("LANDING_TIMEOUT_S")
    values["landing_timeout_s"] = _parse_float(raw, d.landing_timeout_s, minimum=0.0) if raw is not None else d.landing_timeout_s

    raw = get("INDEX_PATH")
    values["index_path"] = Path(raw.strip()) if raw is not None else d.index_path

    raw = get("DATA_DIR")
    values["data_dir"] = Path(raw.strip()) if raw is not None else d.data_dir

    raw = get("RAG_DENSE_MIN")
    values["rag_dense_min"] = _parse_float(raw, d.rag_dense_min) if raw is not None else d.rag_dense_min

    raw = get("RAG_LEXICAL_MIN")
    values["rag_lexical_min"] = _parse_float(raw, d.rag_lexical_min) if raw is not None else d.rag_lexical_min

    raw = get("RAG_BM25_MIN")
    values["rag_bm25_min"] = _parse_float(raw, d.rag_bm25_min) if raw is not None else d.rag_bm25_min

    raw = get("DEBUG_ENDPOINTS")
    values["debug_endpoints"] = _parse_bool(raw, d.debug_endpoints) if raw is not None else d.debug_endpoints

    raw = get("LOG_LEVEL")
    values["log_level"] = raw.strip().upper() if raw is not None else d.log_level

    for fld, var in (("feature_rag", "FEATURE_RAG"), ("feature_guard", "FEATURE_GUARD"),
                     ("feature_memory", "FEATURE_MEMORY"), ("feature_templates", "FEATURE_TEMPLATES"),
                     ("feature_repair", "FEATURE_REPAIR"), ("feature_hitl", "FEATURE_HITL")):
        r = get(var)
        values[fld] = _parse_bool(r, getattr(d, fld)) if r is not None else getattr(d, fld)

    return Settings(**values)  # type: ignore[arg-type]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached Settings for the running process."""
    return load_settings()


def reset_settings_cache() -> None:
    """Clear get_settings() cache (tests only)."""
    get_settings.cache_clear()
