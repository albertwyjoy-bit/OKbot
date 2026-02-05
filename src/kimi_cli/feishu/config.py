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
    auto_approve: bool = Field(False, description="Auto-approve tool calls")
    show_tool_calls: bool = Field(True, description="Show tool calls in messages")
    show_thinking: bool = Field(True, description="Show thinking process")
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "app_id": "cli_xxxxxxxx",
                    "app_secret": "xxxxxxxx",
                    "auto_approve": False,
                    "show_tool_calls": True,
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
        
        # Convert to dict and remove None values for TOML compatibility
        try:
            # Use model_dump with context to handle SecretStr
            data = self.model_dump(mode="json")
        except PydanticSerializationError:
            # Fallback to regular dump
            data = self.model_dump()
        
        def remove_nulls(obj):
            if isinstance(obj, dict):
                return {k: remove_nulls(v) for k, v in obj.items() if v is not None}
            elif isinstance(obj, list):
                return [remove_nulls(v) for v in obj]
            return obj
        
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
