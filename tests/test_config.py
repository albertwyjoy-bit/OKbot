from inline_snapshot import snapshot
from pydantic import SecretStr

from kimi_cli.config import (
    Config,
    LLMModel,
    LLMProvider,
    MoonshotSearchConfig,
    Services,
    get_default_config,
)


def test_default_config():
    config = get_default_config()
    assert config == snapshot(
        Config(
            default_model="kimi-k2-turbo-preview",
            models={
                "kimi-k2-turbo-preview": LLMModel(provider="kimi", model="kimi-k2-turbo-preview")
            },
            providers={
                "kimi": LLMProvider(
                    type="kimi",
                    base_url="https://api.moonshot.cn/v1",
                    api_key=SecretStr(""),
                )
            },
            services=Services(moonshot_search=MoonshotSearchConfig(api_key=SecretStr(""))),
        )
    )


def test_default_config_dump():
    config = get_default_config()
    assert config.model_dump_json(indent=2, exclude_none=True) == snapshot(
        """\
{
  "default_model": "kimi-k2-turbo-preview",
  "models": {
    "kimi-k2-turbo-preview": {
      "provider": "kimi",
      "model": "kimi-k2-turbo-preview",
      "max_context_size": 200000
    }
  },
  "providers": {
    "kimi": {
      "type": "kimi",
      "base_url": "https://api.moonshot.cn/v1",
      "api_key": ""
    }
  },
  "loop_control": {
    "max_steps_per_run": 100,
    "max_retries_per_step": 3
  },
  "services": {
    "moonshot_search": {
      "api_key": ""
    }
  }
}\
"""
    )
