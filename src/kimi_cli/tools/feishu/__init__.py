"""Feishu file transfer tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, override

from kosong.tooling import CallableTool2
from kosong.tooling import ToolReturnValue
from pydantic import BaseModel, Field

from kimi_cli.tools.utils import ToolResultBuilder

# Global reference to Feishu SDK client, set by sdk_server when initialized
_feishu_client: Any | None = None


def set_feishu_client(client: Any) -> None:
    """Set the global Feishu SDK client."""
    global _feishu_client
    _feishu_client = client


def get_feishu_client() -> Any | None:
    """Get the global Feishu SDK client."""
    return _feishu_client


class SendFileParams(BaseModel):
    file_path: str = Field(
        description="The path to the file to send. Can be absolute or relative to current working directory."
    )
    message: str = Field(
        description="Optional message to send with the file.",
        default="",
    )


class FeishuSendFile(CallableTool2[SendFileParams]):
    """Send a file to the current Feishu chat."""

    name: str = "FeishuSendFile"
    params: type[SendFileParams] = SendFileParams
    description: str = (
        "Send a file to the current Feishu (Lark) chat session. "
        "Use this when the user wants to upload or send a file through Feishu. "
        "The file_path can be absolute or relative to the current working directory. "
        "Supported file types: any file (documents, images, archives, etc.)."
    )

    @override
    async def __call__(self, params: SendFileParams) -> ToolReturnValue:
        builder = ToolResultBuilder()
        client = get_feishu_client()

        if client is None:
            return builder.error(
                "Feishu client is not available. Make sure you are in a Feishu chat session.",
                brief="Feishu not connected",
            )

        # Resolve file path using work_dir if available
        file_path = params.file_path
        if not os.path.isabs(file_path):
            # Use client's work_dir if available, otherwise fall back to cwd
            base_dir = client.work_dir if client.work_dir else os.getcwd()
            file_path = os.path.join(base_dir, file_path)
        file_path = os.path.expanduser(file_path)

        if not os.path.exists(file_path):
            return builder.error(
                f"File not found: {params.file_path}",
                brief=f"File not found: {os.path.basename(params.file_path)}",
            )

        try:
            file_name = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)

            # Read file content
            with open(file_path, "rb") as f:
                file_content = f.read()

            # Determine if it's an image
            ext = Path(file_name).suffix.lower()
            is_image = ext in [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]

            if is_image:
                # Upload as image
                file_key = client.upload_image(file_content, file_name)
                if file_key:
                    client.send_image_message(client.current_chat_id, file_key)
                    return builder.ok(
                        f"Image sent successfully: {file_name} ({file_size} bytes)"
                    )
                else:
                    return builder.error(
                        f"Failed to upload image: {file_name}",
                        brief="Image upload failed",
                    )
            else:
                # Upload as file
                file_type = "stream"
                file_key = client.upload_file(file_content, file_name, file_type)
                if file_key:
                    client.send_file_message(client.current_chat_id, file_key)
                    return builder.ok(
                        f"File sent successfully: {file_name} ({file_size} bytes)"
                    )
                else:
                    return builder.error(
                        f"Failed to upload file: {file_name}",
                        brief="File upload failed",
                    )

        except Exception as e:
            return builder.error(
                f"Error sending file: {str(e)}",
                brief=f"Send failed: {str(e)[:50]}",
            )


class SendMessageParams(BaseModel):
    message: str = Field(
        description="The message text to send to Feishu chat."
    )


class FeishuSendMessage(CallableTool2[SendMessageParams]):
    """Send a text message to the current Feishu chat."""

    name: str = "FeishuSendMessage"
    params: type[SendMessageParams] = SendMessageParams
    description: str = (
        "Send a text message to the current Feishu (Lark) chat session. "
        "Use this when you need to send a notification or message to the user through Feishu."
    )

    @override
    async def __call__(self, params: SendMessageParams) -> ToolReturnValue:
        builder = ToolResultBuilder()
        client = get_feishu_client()

        if client is None:
            return builder.error(
                "Feishu client is not available. Make sure you are in a Feishu chat session.",
                brief="Feishu not connected",
            )

        try:
            client.send_text_message(client.current_chat_id, params.message)
            return builder.ok(f"Message sent successfully ({len(params.message)} chars)")
        except Exception as e:
            return builder.error(
                f"Error sending message: {str(e)}",
                brief=f"Send failed: {str(e)[:50]}",
            )
