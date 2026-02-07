"""Feishu SDK client using lark-oapi.

This module provides a client wrapper around the official Feishu Python SDK.
Uses sync API to avoid httpx compatibility issues.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import lark_oapi as lark
import requests
from loguru import logger

from kimi_cli.feishu.config import FeishuAccountConfig


class FeishuSDKClient:
    """Feishu client using official SDK (sync version for compatibility)."""
    
    def __init__(self, config: FeishuAccountConfig):
        self.config = config
        self.app_id = config.app_id
        self.app_secret = config.app_secret.get_secret_value()
        self._token = None
        self._current_chat_id: str | None = None
        self._work_dir: str | None = None
        
        # Initialize SDK client (sync version)
        self._client = lark.Client.builder() \
            .app_id(self.app_id) \
            .app_secret(self.app_secret) \
            .app_type(lark.AppType.SELF) \
            .build()
        
        # Get token for direct API calls
        self._refresh_token()
        
        logger.info(f"Feishu SDK client initialized for app: {self.app_id}")
    
    @property
    def current_chat_id(self) -> str | None:
        """Get the current chat ID for tool calls."""
        return self._current_chat_id
    
    def set_current_chat_id(self, chat_id: str | None) -> None:
        """Set the current chat ID for tool calls."""
        self._current_chat_id = chat_id
        if chat_id:
            logger.debug(f"Current chat ID set to: {chat_id}")
    
    @property
    def work_dir(self) -> str | None:
        """Get the working directory for file operations."""
        return self._work_dir
    
    def set_work_dir(self, work_dir: str | None) -> None:
        """Set the working directory for file operations.
        
        This is used by tools to resolve relative file paths.
        """
        self._work_dir = work_dir
        if work_dir:
            logger.debug(f"Work directory set to: {work_dir}")
    
    def _refresh_token(self) -> bool:
        """Refresh tenant access token."""
        try:
            url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
            resp = requests.post(
                url,
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                timeout=10
            )
            data = resp.json()
            if data.get("code") == 0:
                self._token = data.get("tenant_access_token")
                return True
            else:
                logger.error(f"Failed to get token: {data}")
                return False
        except Exception as e:
            logger.error(f"Error refreshing token: {e}")
            return False
    
    def _api_call(self, method: str, endpoint: str, **kwargs) -> dict | None:
        """Make a direct API call."""
        if not self._token:
            if not self._refresh_token():
                return None
        
        url = f"https://open.feishu.cn/open-apis{endpoint}"
        headers = {"Authorization": f"Bearer {self._token}"}
        if "headers" in kwargs:
            headers.update(kwargs.pop("headers"))
        
        try:
            resp = requests.request(method, url, headers=headers, timeout=10, **kwargs)
            data = resp.json()
            if data.get("code") == 99991663:  # Token expired
                if self._refresh_token():
                    headers["Authorization"] = f"Bearer {self._token}"
                    resp = requests.request(method, url, headers=headers, timeout=10, **kwargs)
                    data = resp.json()
            return data
        except Exception as e:
            logger.error(f"API call error: {e}")
            return None
    
    def send_text_message(
        self,
        chat_id: str,
        text: str,
        msg_type: str = "chat_id",
    ) -> str | None:
        """Send a text message (sync)."""
        try:
            request = lark.im.v1.CreateMessageRequest.builder() \
                .receive_id_type(msg_type) \
                .request_body(
                    lark.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(json.dumps({"text": text}))
                    .build()
                ) \
                .build()
            
            response = self._client.im.v1.message.create(request)
            
            if response.success():
                data = response.data or {}
                logger.info(f"Text message sent: {data.message_id}")
                return data.message_id
            else:
                logger.error(f"Failed to send text: {response.code} - {response.msg}")
                return None
                
        except Exception as e:
            logger.exception(f"Exception sending text: {e}")
            return None
    
    def send_interactive_card(
        self,
        chat_id: str,
        card: dict[str, Any],
        msg_type: str = "chat_id",
    ) -> str | None:
        """Send an interactive card message (sync)."""
        try:
            import json
            card_json = json.dumps(card)
            print(f"[SDK] Sending card to {chat_id}, size: {len(card_json)} bytes")
            
            request = lark.im.v1.CreateMessageRequest.builder() \
                .receive_id_type(msg_type) \
                .request_body(
                    lark.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("interactive")
                    .content(card_json)  # Use content, not card
                    .build()
                ) \
                .build()
            
            response = self._client.im.v1.message.create(request)
            
            if response.success():
                data = response.data or {}
                print(f"[SDK] ✅ Card sent: {data.message_id}")
                logger.info(f"Card sent: {data.message_id}")
                return data.message_id
            else:
                print(f"[SDK] ❌ Failed to send card: {response.code} - {response.msg}")
                logger.error(f"Failed to send card: {response.code} - {response.msg}")
                return None
                
        except Exception as e:
            print(f"[SDK] ❌ Exception sending card: {e}")
            logger.exception(f"Exception sending card: {e}")
            return None
    
    def update_interactive_card(
        self,
        message_id: str,
        card: dict[str, Any],
    ) -> bool:
        """Update an existing interactive card (sync)."""
        try:
            request = lark.im.v1.PatchMessageRequest.builder() \
                .message_id(message_id) \
                .request_body(
                    lark.im.v1.PatchMessageRequestBody.builder()
                    .content(json.dumps(card))
                    .build()
                ) \
                .build()
            
            response = self._client.im.v1.message.patch(request)
            
            if response.success():
                logger.debug(f"Card updated: {message_id}")
                return True
            else:
                logger.error(f"Failed to update card: {response.code} - {response.msg}")
                return False
                
        except Exception as e:
            logger.exception(f"Exception updating card: {e}")
            return False
    
    def get_bot_info(self) -> dict[str, Any] | None:
        """Get bot information using direct API call (sync)."""
        data = self._api_call("GET", "/bot/v3/info")
        if data and data.get("code") == 0:
            bot = data.get("bot", {})
            return {
                "app_name": bot.get("app_name"),
                "open_id": bot.get("open_id"),
                "activate_status": bot.get("activate_status"),
            }
        return None
    
    def download_file(self, file_key: str, message_id: str | None = None) -> tuple[bytes, str] | None:
        """Download a file from Feishu by file_key.
        
        For files sent by users, we need to use the message resources API.
        
        Args:
            file_key: The file key from message content
            message_id: Optional message ID for user-sent files
            
        Returns:
            Tuple of (file_content, file_name) or None if failed
        """
        try:
            print(f"[SDK] Downloading file: {file_key}")
            
            # Try SDK method first (works for bot-uploaded files)
            request = lark.im.v1.GetFileRequest.builder() \
                .file_key(file_key) \
                .build()
            
            response = self._client.im.v1.file.get(request)
            
            if response.success():
                # Get file content and name from response
                file_content = response.data.content if hasattr(response.data, 'content') else None
                file_name = response.data.name if hasattr(response.data, 'name') else f"file_{file_key}"
                
                if file_content:
                    print(f"[SDK] ✅ File downloaded: {file_name} ({len(file_content)} bytes)")
                    return (file_content, file_name)
                else:
                    print(f"[SDK] ⚠️ File content is empty")
                    return None
            else:
                error_code = response.code if hasattr(response, 'code') else 'unknown'
                error_msg = response.msg if hasattr(response, 'msg') else str(response)
                print(f"[SDK] ❌ Failed to download file: {error_code} - {error_msg}")
                
                # If permission error, try alternative method
                if error_code == 234008 and message_id:
                    print(f"[SDK] Trying alternative download method via message resources...")
                    return self._download_file_via_message(message_id, file_key)
                
                return None
                
        except Exception as e:
            print(f"[SDK] ❌ Exception downloading file: {e}")
            logger.exception(f"Exception downloading file: {e}")
            return None
    
    def _download_file_via_message(self, message_id: str, file_key: str) -> tuple[bytes, str] | None:
        """Download file using message resources API.
        
        API: GET /open-apis/im/v1/messages/{message_id}/resources/{file_key}?type=file
        """
        try:
            print(f"[SDK] Downloading via message API: message_id={message_id}, file_key={file_key}")
            
            # Use direct HTTP request for binary data
            if not self._token:
                if not self._refresh_token():
                    return None
            
            url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{file_key}?type=file"
            headers = {"Authorization": f"Bearer {self._token}"}
            
            import requests
            resp = requests.get(url, headers=headers, timeout=30)
            
            # Check for token expiration and retry
            if resp.status_code == 400:
                try:
                    data = resp.json()
                    if data.get("code") == 99991663:  # Token expired
                        print("[SDK] Token expired, refreshing...")
                        if self._refresh_token():
                            headers["Authorization"] = f"Bearer {self._token}"
                            resp = requests.get(url, headers=headers, timeout=30)
                        else:
                            print("[SDK] Failed to refresh token")
                            return None
                except:
                    pass
            
            if resp.status_code == 200:
                # Check if response is JSON (error) or binary (file)
                content_type = resp.headers.get('Content-Type', '')
                if 'application/json' in content_type:
                    # Error response
                    data = resp.json()
                    error_code = data.get("code", "unknown")
                    error_msg = data.get("msg", "Unknown error")
                    print(f"[SDK] ❌ Message API returned error: {error_code} - {error_msg}")
                    return None
                else:
                    # Binary file content
                    file_content = resp.content
                    print(f"[SDK] ✅ File downloaded via message API: {len(file_content)} bytes")
                    return (file_content, f"file_{file_key}")
            else:
                print(f"[SDK] ❌ Message API HTTP error: {resp.status_code}")
                try:
                    data = resp.json()
                    print(f"[SDK] Error details: {data}")
                except:
                    pass
                return None
                
        except Exception as e:
            print(f"[SDK] ❌ Exception in message API download: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def download_image(self, image_key: str, message_id: str | None = None) -> tuple[bytes, str] | None:
        """Download an image from Feishu by image_key.
        
        Similar to file download but for images.
        
        Returns:
            Tuple of (image_content, image_name) or None if failed
        """
        try:
            print(f"[SDK] Downloading image: {image_key}")
            
            # Use direct HTTP request as primary method
            # The SDK method has issues with parameter validation
            if not self._token:
                if not self._refresh_token():
                    return None
            
            # Try message resources API first if message_id is available
            if message_id:
                result = self._download_image_via_message(message_id, image_key)
                if result:
                    return result
            
            # Fallback to direct image API
            url = f"https://open.feishu.cn/open-apis/im/v1/images/{image_key}"
            headers = {"Authorization": f"Bearer {self._token}"}
            
            resp = requests.get(url, headers=headers, timeout=30)
            
            # Check for token expiration and retry
            if resp.status_code == 400:
                try:
                    data = resp.json()
                    if data.get("code") == 99991663:  # Token expired
                        print("[SDK] Token expired, refreshing...")
                        if self._refresh_token():
                            headers["Authorization"] = f"Bearer {self._token}"
                            resp = requests.get(url, headers=headers, timeout=30)
                        else:
                            print("[SDK] Failed to refresh token")
                            return None
                except:
                    pass
            
            if resp.status_code == 200:
                # Check if response is JSON (error) or binary (image)
                content_type = resp.headers.get('Content-Type', '')
                if 'application/json' in content_type:
                    # Error response
                    data = resp.json()
                    error_code = data.get("code", "unknown")
                    error_msg = data.get("msg", "Unknown error")
                    print(f"[SDK] ❌ Image API returned error: {error_code} - {error_msg}")
                    return None
                else:
                    # Binary image content
                    image_content = resp.content
                    # Try to determine extension from content type
                    ext = self._get_image_extension(content_type)
                    image_name = f"image_{image_key[:20]}{ext}"
                    print(f"[SDK] ✅ Image downloaded via API: {len(image_content)} bytes")
                    return (image_content, image_name)
            else:
                print(f"[SDK] ❌ HTTP error: {resp.status_code}")
                return None
                
        except Exception as e:
            print(f"[SDK] ❌ Exception downloading image: {e}")
            logger.exception(f"Exception downloading image: {e}")
            return None
    
    def _get_image_extension(self, content_type: str) -> str:
        """Get file extension from content type."""
        content_type = content_type.lower()
        if 'png' in content_type:
            return '.png'
        elif 'jpeg' in content_type or 'jpg' in content_type:
            return '.jpg'
        elif 'gif' in content_type:
            return '.gif'
        elif 'webp' in content_type:
            return '.webp'
        elif 'bmp' in content_type:
            return '.bmp'
        else:
            return '.png'  # Default to png
    
    def _download_image_via_message(self, message_id: str, image_key: str) -> tuple[bytes, str] | None:
        """Download image using message resources API.
        
        API: GET /open-apis/im/v1/messages/{message_id}/resources/{image_key}?type=image
        """
        try:
            print(f"[SDK] Downloading image via message API: message_id={message_id}, image_key={image_key}")
            
            # Use direct HTTP request for binary data
            if not self._token:
                if not self._refresh_token():
                    return None
            
            url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{image_key}?type=image"
            headers = {"Authorization": f"Bearer {self._token}"}
            
            import requests
            resp = requests.get(url, headers=headers, timeout=30)
            
            # Check for token expiration and retry
            if resp.status_code == 400:
                try:
                    data = resp.json()
                    if data.get("code") == 99991663:  # Token expired
                        print("[SDK] Token expired, refreshing...")
                        if self._refresh_token():
                            headers["Authorization"] = f"Bearer {self._token}"
                            resp = requests.get(url, headers=headers, timeout=30)
                        else:
                            print("[SDK] Failed to refresh token")
                            return None
                except:
                    pass
            
            if resp.status_code == 200:
                # Check if response is JSON (error) or binary (image)
                content_type = resp.headers.get('Content-Type', '')
                if 'application/json' in content_type:
                    # Error response
                    data = resp.json()
                    error_code = data.get("code", "unknown")
                    error_msg = data.get("msg", "Unknown error")
                    print(f"[SDK] ❌ Message API returned error: {error_code} - {error_msg}")
                    return None
                else:
                    # Binary image content
                    image_content = resp.content
                    ext = self._get_image_extension(content_type)
                    image_name = f"image_{image_key[:20]}{ext}"
                    print(f"[SDK] ✅ Image downloaded via message API: {len(image_content)} bytes")
                    return (image_content, image_name)
            else:
                print(f"[SDK] ❌ Message API HTTP error: {resp.status_code}")
                try:
                    data = resp.json()
                    print(f"[SDK] Error details: {data}")
                except:
                    pass
                return None
                
        except Exception as e:
            print(f"[SDK] ❌ Exception in message API download: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def download_audio(self, file_key: str, message_id: str | None = None) -> tuple[bytes, str] | None:
        """Download an audio/voice file from Feishu.
        
        Voice messages in Feishu are treated as files with audio type.
        
        Args:
            file_key: The file key from message content
            message_id: Optional message ID for user-sent files
            
        Returns:
            Tuple of (audio_content, file_name) or None if failed
        """
        try:
            print(f"[SDK] Downloading audio: {file_key}")
            
            # Use direct HTTP request for binary data
            if not self._token:
                if not self._refresh_token():
                    return None
            
            # Try message resources API first if message_id is available
            if message_id:
                url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{file_key}?type=file"
                headers = {"Authorization": f"Bearer {self._token}"}
                
                import requests
                resp = requests.get(url, headers=headers, timeout=30)
                
                # Check for token expiration and retry
                if resp.status_code == 400:
                    try:
                        data = resp.json()
                        if data.get("code") == 99991663:  # Token expired
                            print("[SDK] Token expired, refreshing...")
                            if self._refresh_token():
                                headers["Authorization"] = f"Bearer {self._token}"
                                resp = requests.get(url, headers=headers, timeout=30)
                            else:
                                print("[SDK] Failed to refresh token")
                                return None
                    except:
                        pass
                
                if resp.status_code == 200:
                    # Check if response is JSON (error) or binary (audio)
                    content_type = resp.headers.get('Content-Type', '')
                    if 'application/json' in content_type:
                        # Error response
                        data = resp.json()
                        error_code = data.get("code", "unknown")
                        error_msg = data.get("msg", "Unknown error")
                        print(f"[SDK] ❌ Message API returned error: {error_code} - {error_msg}")
                        return None
                    else:
                        # Binary audio content
                        audio_content = resp.content
                        # Try to determine extension from content type
                        ext = self._get_audio_extension(content_type)
                        file_name = f"audio_{file_key[:20]}{ext}"
                        print(f"[SDK] ✅ Audio downloaded via message API: {len(audio_content)} bytes")
                        return (audio_content, file_name)
                else:
                    print(f"[SDK] ❌ Message API HTTP error: {resp.status_code}")
                    try:
                        data = resp.json()
                        print(f"[SDK] Error details: {data}")
                    except:
                        pass
                    return None
            
            # Fallback: try SDK file download method
            print(f"[SDK] Trying SDK file download method...")
            return self.download_file(file_key, message_id)
                
        except Exception as e:
            print(f"[SDK] ❌ Exception downloading audio: {e}")
            logger.exception(f"Exception downloading audio: {e}")
            return None
    
    def _get_audio_extension(self, content_type: str) -> str:
        """Get file extension from content type for audio files."""
        content_type = content_type.lower()
        if 'mp3' in content_type or 'mpeg' in content_type:
            return '.mp3'
        elif 'wav' in content_type or 'wave' in content_type:
            return '.wav'
        elif 'ogg' in content_type:
            return '.ogg'
        elif 'aac' in content_type:
            return '.aac'
        elif 'm4a' in content_type or 'mp4' in content_type:
            return '.m4a'
        elif 'amr' in content_type:
            return '.amr'  # Common for voice messages
        else:
            return '.mp3'  # Default to mp3
    
    def upload_image(self, image_content: bytes, image_name: str = "image.png") -> str | None:
        """Upload an image to Feishu.
        
        Uses direct HTTP API with multipart/form-data instead of SDK to avoid
        encoding issues with binary content.
        
        Args:
            image_content: Image binary content
            image_name: Image file name
            
        Returns:
            image_key if successful, None otherwise
        """
        try:
            print(f"[SDK] Uploading image: {image_name} ({len(image_content)} bytes)")
            
            # Ensure we have a valid token
            if not self._token:
                if not self._refresh_token():
                    print("[SDK] ❌ Failed to get access token")
                    return None
            
            # Use direct HTTP API with multipart/form-data
            url = "https://open.feishu.cn/open-apis/im/v1/images"
            headers = {
                "Authorization": f"Bearer {self._token}",
            }
            
            # Build multipart/form-data manually to ensure proper binary handling
            boundary = "----FormBoundary7MA4YWxkTrZu0gW"
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            
            # Build body parts
            body_parts = []
            
            # image_type field
            body_parts.append(f"--{boundary}\r\n".encode())
            body_parts.append(f'Content-Disposition: form-data; name="image_type"\r\n\r\n'.encode())
            body_parts.append(b"message\r\n")
            
            # image content (binary)
            body_parts.append(f"--{boundary}\r\n".encode())
            body_parts.append(f'Content-Disposition: form-data; name="image"; filename="{image_name}"\r\n'.encode())
            body_parts.append(b"Content-Type: application/octet-stream\r\n\r\n")
            body_parts.append(image_content)
            body_parts.append(b"\r\n")
            
            # End boundary
            body_parts.append(f"--{boundary}--\r\n".encode())
            
            # Combine all parts
            body = b"".join(body_parts)
            
            response = requests.post(url, headers=headers, data=body, timeout=60)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("code") == 0:
                    image_key = data.get("data", {}).get("image_key")
                    print(f"[SDK] ✅ Image uploaded: {image_key}")
                    return image_key
                else:
                    error_code = data.get("code")
                    error_msg = data.get("msg", "Unknown error")
                    print(f"[SDK] ❌ API error: {error_code} - {error_msg}")
                    return None
            else:
                print(f"[SDK] ❌ HTTP error: {response.status_code}")
                try:
                    print(f"[SDK] Response: {response.text[:200]}")
                except:
                    pass
                return None
                
        except Exception as e:
            print(f"[SDK] ❌ Exception uploading image: {e}")
            logger.exception(f"Exception uploading image: {e}")
            return None
    
    def send_image_message(self, chat_id: str, image_key: str) -> str | None:
        """Send an image message.
        
        Args:
            chat_id: Chat ID to send to
            image_key: Image key from upload_image
            
        Returns:
            message_id if successful, None otherwise
        """
        try:
            print(f"[SDK] Sending image message: {image_key}")
            
            request = lark.im.v1.CreateMessageRequest.builder() \
                .receive_id_type("chat_id") \
                .request_body(
                    lark.im.v1.CreateMessageRequestBody.builder()
                        .receive_id(chat_id)
                        .msg_type("image")
                        .content(json.dumps({"image_key": image_key}))
                        .build()
                ) \
                .build()
            
            response = self._client.im.v1.message.create(request)
            
            if response.success():
                msg_id = response.data.message_id if response.data else None
                print(f"[SDK] ✅ Image message sent: {msg_id}")
                return msg_id
            else:
                print(f"[SDK] ❌ Failed to send image message: {response.code} - {response.msg}")
                return None
                
        except Exception as e:
            print(f"[SDK] ❌ Exception sending image message: {e}")
            logger.exception(f"Exception sending image message: {e}")
            return None
    
    def upload_file(self, file_content: bytes, file_name: str, file_type: str = "stream") -> str | None:
        """Upload a file to Feishu.
        
        Uses direct HTTP API with multipart/form-data instead of SDK to avoid
        encoding issues with binary content.
        
        Args:
            file_content: File binary content
            file_name: File name
            file_type: File type (stream, image, etc.)
            
        Returns:
            file_key if successful, None otherwise
        """
        try:
            print(f"[SDK] Uploading file: {file_name} ({len(file_content)} bytes)")
            
            # Ensure we have a valid token
            if not self._token:
                if not self._refresh_token():
                    print("[SDK] ❌ Failed to get access token")
                    return None
            
            # For text files, add UTF-8 BOM to help Feishu recognize encoding
            file_content = self._ensure_utf8_encoding(file_content, file_name)
            
            # Use direct HTTP API with multipart/form-data
            url = "https://open.feishu.cn/open-apis/im/v1/files"
            headers = {
                "Authorization": f"Bearer {self._token}",
            }
            
            # Build multipart/form-data manually to ensure proper binary handling
            boundary = "----FormBoundary7MA4YWxkTrZu0gW"
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            
            # Build body parts
            body_parts = []
            
            # file_type field
            body_parts.append(f"--{boundary}\r\n".encode())
            body_parts.append(f'Content-Disposition: form-data; name="file_type"\r\n\r\n'.encode())
            body_parts.append(f"{file_type}\r\n".encode())
            
            # file_name field
            body_parts.append(f"--{boundary}\r\n".encode())
            body_parts.append(f'Content-Disposition: form-data; name="file_name"\r\n\r\n'.encode())
            body_parts.append(f"{file_name}\r\n".encode())
            
            # file content (binary)
            body_parts.append(f"--{boundary}\r\n".encode())
            body_parts.append(f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'.encode())
            body_parts.append(b"Content-Type: application/octet-stream\r\n\r\n")
            body_parts.append(file_content)
            body_parts.append(b"\r\n")
            
            # End boundary
            body_parts.append(f"--{boundary}--\r\n".encode())
            
            # Combine all parts
            body = b"".join(body_parts)
            
            response = requests.post(url, headers=headers, data=body, timeout=60)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("code") == 0:
                    file_key = data.get("data", {}).get("file_key")
                    print(f"[SDK] ✅ File uploaded: {file_key}")
                    return file_key
                else:
                    error_code = data.get("code")
                    error_msg = data.get("msg", "Unknown error")
                    print(f"[SDK] ❌ API error: {error_code} - {error_msg}")
                    return None
            else:
                print(f"[SDK] ❌ HTTP error: {response.status_code}")
                try:
                    print(f"[SDK] Response: {response.text[:200]}")
                except:
                    pass
                return None
                
        except Exception as e:
            print(f"[SDK] ❌ Exception uploading file: {e}")
            logger.exception(f"Exception uploading file: {e}")
            return None
    
    def _ensure_utf8_encoding(self, file_content: bytes, file_name: str) -> bytes:
        """Ensure text files have UTF-8 BOM for proper encoding recognition.
        
        Feishu may not correctly identify UTF-8 encoded text files without BOM,
        leading to garbled characters (especially for Markdown, JSON, etc.).
        
        Args:
            file_content: Original file content
            file_name: File name (used to determine file type)
            
        Returns:
            File content with UTF-8 BOM added if it's a text file
        """
        # Text file extensions that benefit from BOM
        text_extensions = {
            '.md', '.markdown', '.txt', '.json', '.xml', '.yaml', '.yml',
            '.csv', '.tsv', '.ini', '.conf', '.config', '.log', '.sh',
            '.py', '.js', '.ts', '.html', '.htm', '.css', '.sql',
        }
        
        ext = Path(file_name).suffix.lower()
        if ext not in text_extensions:
            return file_content
        
        # Check if content already has BOM
        utf8_bom = b'\xef\xbb\xbf'
        if file_content.startswith(utf8_bom):
            return file_content
        
        # Try to detect if it's valid UTF-8
        try:
            # If we can decode as UTF-8, it's likely a text file
            file_content.decode('utf-8')
            # Add BOM to help Feishu recognize encoding
            print(f"[SDK] Adding UTF-8 BOM for text file: {file_name}")
            return utf8_bom + file_content
        except (UnicodeDecodeError, AttributeError):
            # Not valid UTF-8 or binary content, return as-is
            return file_content
    
    def send_file_message(self, chat_id: str, file_key: str) -> str | None:
        """Send a file message.
        
        Args:
            chat_id: Chat ID to send to
            file_key: File key from upload_file
            
        Returns:
            message_id if successful, None otherwise
        """
        try:
            print(f"[SDK] Sending file message: {file_key}")
            
            request = lark.im.v1.CreateMessageRequest.builder() \
                .receive_id_type("chat_id") \
                .request_body(
                    lark.im.v1.CreateMessageRequestBody.builder()
                        .receive_id(chat_id)
                        .msg_type("file")
                        .content(json.dumps({"file_key": file_key}))
                        .build()
                ) \
                .build()
            
            response = self._client.im.v1.message.create(request)
            
            if response.success():
                msg_id = response.data.message_id if response.data else None
                print(f"[SDK] ✅ File message sent: {msg_id}")
                return msg_id
            else:
                print(f"[SDK] ❌ Failed to send file message: {response.code} - {response.msg}")
                return None
                
        except Exception as e:
            print(f"[SDK] ❌ Exception sending file message: {e}")
            logger.exception(f"Exception sending file message: {e}")
            return None
    
    def add_message_reaction(self, message_id: str, emoji_code: str = "OK") -> bool:
        """Add a reaction emoji to a message.
        
        Args:
            message_id: The message ID to add reaction to
            emoji_code: Emoji code (default: "OK" for 👌)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            print(f"[SDK] Adding reaction '{emoji_code}' to message: {message_id}")
            
            # Ensure we have a valid token
            if not self._token:
                if not self._refresh_token():
                    print("[SDK] ❌ Failed to get access token")
                    return False
            
            # Use direct HTTP API
            url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reactions"
            headers = {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
            
            payload = {
                "reaction_type": {
                    "emoji_type": emoji_code
                }
            }
            
            resp = requests.post(url, headers=headers, json=payload, timeout=10)
            
            # Check for token expiration and retry
            if resp.status_code == 400:
                try:
                    data = resp.json()
                    if data.get("code") == 99991663:  # Token expired
                        print("[SDK] Token expired, refreshing...")
                        if self._refresh_token():
                            headers["Authorization"] = f"Bearer {self._token}"
                            resp = requests.post(url, headers=headers, json=payload, timeout=10)
                        else:
                            print("[SDK] Failed to refresh token")
                            return False
                except:
                    pass
            
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    print(f"[SDK] ✅ Reaction '{emoji_code}' added successfully")
                    return True
                else:
                    error_code = data.get("code")
                    error_msg = data.get("msg", "Unknown error")
                    print(f"[SDK] ❌ API error: {error_code} - {error_msg}")
                    return False
            else:
                print(f"[SDK] ❌ HTTP error: {resp.status_code}")
                try:
                    print(f"[SDK] Response: {resp.text[:200]}")
                except:
                    pass
                return False
                
        except Exception as e:
            print(f"[SDK] ❌ Exception adding reaction: {e}")
            logger.exception(f"Exception adding reaction: {e}")
            return False
