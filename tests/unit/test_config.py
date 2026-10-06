"""Settings parsing tests."""
from hyperion.config import Settings, load_settings


def test_defaults():
    s = load_settings({})
    d = Settings()
    assert s == d
    assert s.llm_base_url == "https://legion1.di.uoa.gr/v1"
    assert s.llm_concurrency == 3


def test_env_overrides_types():
    s = load_settings({
        "LLM_CONCURRENCY": "7",
        "LLM_TIMEOUT_S": "12.5",
        "LLM_MAX_RETRIES": "1",
        "TURN_LLM_BUDGET": "2",
        "TURN_DEADLINE_S": "10",
        "PENDING_TTL_S": "60",
        "LANDING_TIMEOUT_S": "2.5",
        "DEBUG_ENDPOINTS": "0",
        "LOG_LEVEL": "debug",
    })
    assert s.llm_concurrency == 7
    assert s.llm_timeout_s == 12.5
    assert s.llm_max_retries == 1
    assert s.turn_llm_budget == 2
    assert s.turn_deadline_s == 10.0
    assert s.pending_ttl_s == 60
    assert s.landing_timeout_s == 2.5
    assert s.debug_endpoints is False
    assert s.log_level == "DEBUG"


def test_bool_parsing():
    for truthy in ("1", "true", "YES", "On"):
        assert load_settings({"FEATURE_RAG": truthy}).feature_rag is True
    for falsy in ("0", "false", "NO", "off"):
        assert load_settings({"FEATURE_GUARD": falsy}).feature_guard is False


def test_invalid_values_fall_back():
    d = Settings()
    s = load_settings({
        "LLM_CONCURRENCY": "banana",
        "LLM_TIMEOUT_S": "-5",
        "LLM_MAX_RETRIES": "-1",
        "TURN_LLM_BUDGET": "0",
        "DEBUG_ENDPOINTS": "maybe",
        "FEATURE_RAG": "perhaps",
    })
    assert s.llm_concurrency == d.llm_concurrency
    assert s.llm_timeout_s == d.llm_timeout_s
    assert s.llm_max_retries == d.llm_max_retries
    assert s.turn_llm_budget == d.turn_llm_budget
    assert s.debug_endpoints == d.debug_endpoints
    assert s.feature_rag == d.feature_rag


def test_hitl_mode_invalid_defaults_strict():
    assert load_settings({"HITL_MODE": "whatever"}).hitl_mode == "strict"
    assert load_settings({"HITL_MODE": "destructive"}).hitl_mode == "destructive"


def test_trailing_slash_stripped():
    s = load_settings({"LLM_BASE_URL": "http://x.test/v1///", "IDE_BACKEND_URL": "http://ide:9/api/"})
    assert s.llm_base_url == "http://x.test/v1"
    assert s.ide_backend_url == "http://ide:9/api"
    # whitespace/quote stripping on key
    assert load_settings({"API_KEY": '  "abc123"  '}).api_key == "abc123"
