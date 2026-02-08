"""Configuration for Feishu integration."""

from __future__ import annotations

from pathlib import Path

import tomlkit
from pydantic import BaseModel, Field, SecretStr
from pydantic_core import PydanticSerializationError

from kimi_cli.share import get_share_dir


class FeishuAccountConfig(BaseModel):
    """Configuration for a single Feishu account."""
    
    app_id: str = Field(..., description="Feishu app ID")
    app_secret: SecretStr = Field(..., description="Feishu app secret")
    
    # Access control
    allowed_users: list[str] = Field(default_factory=list, description="List of allowed user IDs")
    allowed_chats: list[str] = Field(default_factory=list, description="List of allowed chat IDs")
    
    # Behavior settings
    auto_approve: bool = Field(False, description="Auto-approve tool calls without confirmation")
    show_tool_calls: bool = Field(True, description="Show tool calls in messages")
    show_thinking: bool = Field(True, description="Show thinking process")
    
    # ASR (Speech Recognition) settings - GLM-ASR-2512 only
    asr_api_key: SecretStr | None = Field(None, description="Zhipu AI API key for GLM-ASR-2512 (defaults to ZHIPU_API_KEY env var)")
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "app_id": "cli_xxxxxxxx",
                    "app_secret": "xxxxxxxx",
                    "show_tool_calls": True,
                    "asr_provider": "auto",
                }
            ]
        }
    }


class FeishuConfig(BaseModel):
    """Configuration for Feishu integration."""
    
    # Gateway settings (for status monitoring)
    host: str = Field("127.0.0.1", description="Gateway host for status monitoring")
    port: int = Field(18789, description="Gateway port for status monitoring")
    
    # Working directory for sessions
    work_dir: str | None = Field(None, description="Default working directory for Feishu sessions")
    
    # Skills directory
    skills_dir: str | None = Field(None, description="Skills directory for Feishu sessions (default: auto-discover)")
    
    # Account configurations
    accounts: dict[str, FeishuAccountConfig] = Field(
        default_factory=dict,
        description="Feishu account configurations keyed by account name"
    )
    
    # Default account
    default_account: str | None = Field(None, description="Default account to use")
    
    @classmethod
    def load(cls, path: Path | None = None) -> FeishuConfig:
        """Load configuration from file.
        
        Args:
            path: Path to config file. If None, uses default path.
            
        Returns:
            FeishuConfig instance
        """
        if path is None:
            path = get_share_dir() / "feishu.toml"
        
        if not path.exists():
            return cls()
        
        try:
            data = tomlkit.parse(path.read_text(encoding="utf-8"))
            return cls.model_validate(dict(data))
        except Exception as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            return cls()
    
    def save(self, path: Path | None = None) -> None:
        """Save configuration to file.
        
        Args:
            path: Path to config file. If None, uses default path.
        """
        if path is None:
            path = get_share_dir() / "feishu.toml"
        
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # Build dict with actual secret values (not masked)
        data = self._build_save_dict()
        
        def remove_nulls(obj):
            if isinstance(obj, dict):
                return {k: remove_nulls(v) for k, v in obj.items() if v is not None}
            elif isinstance(obj, list):
                return [remove_nulls(v) for v in obj]
            return obj
        
        clean_data = remove_nulls(data)
        path.write_text(tomlkit.dumps(clean_data), encoding="utf-8")
    
    def _build_save_dict(self) -> dict:
        """Build a dict representation with actual secret values (not masked)."""
        from pydantic import SecretStr
        
        def extract_value(v):
            """Extract value, handling SecretStr."""
            if isinstance(v, SecretStr):
                return v.get_secret_value() if v else None
            elif isinstance(v, dict):
                return {kk: extract_value(vv) for kk, vv in v.items()}
            elif hasattr(v, '__dict__') and hasattr(v, 'model_fields'):
                # It's a Pydantic model
                return extract_model(v)
            return v
        
        def extract_model(model):
            """Extract dict from Pydantic model."""
            result = {}
            for key, value in model:
                result[key] = extract_value(value)
            return result
        
        # Build the data dict
        data = {
            "host": self.host,
            "port": self.port,
            "work_dir": self.work_dir,
            "skills_dir": self.skills_dir,
            "default_account": self.default_account,
            "accounts": {
                name: extract_model(account)
                for name, account in self.accounts.items()
            }
        }
        return data
        
        clean_data = remove_nulls(data)
        path.write_text(tomlkit.dumps(clean_data), encoding="utf-8")
    
    def get_account(self, name: str | None = None) -> FeishuAccountConfig | None:
        """Get account configuration by name.
        
        Args:
            name: Account name. If None, uses default account.
            
        Returns:
            Account configuration or None if not found
        """
        if name is None:
            name = self.default_account
        if name is None:
            # Return first account if no default is set
            if self.accounts:
                return next(iter(self.accounts.values()))
            return None
        return self.accounts.get(name)
