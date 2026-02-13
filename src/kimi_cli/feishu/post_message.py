"""富文本（Post）消息处理模块.

支持接收用户发送的富文本消息，包含文字、图片、文件等混合内容。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger


class PostMessageParser:
    """Parser for Feishu post (rich text) messages."""
    
    @staticmethod
    def parse(content: dict) -> dict[str, Any]:
        """Parse post message content.
        
        Args:
            content: The message.content dict from Feishu event
            
        Returns:
            Parsed result with keys: title, text, images, files
        """
        result = {
            "title": "",
            "text": "",
            "images": [],  # List of dict with image_key, width, height
            "files": [],   # List of dict with file_key, file_name
        }
        
        post_data = content.get("post", {})
        
        # Support multiple languages, prefer zh_cn > zh_CN > en_us > first available
        lang_content = None
        for key in ["zh_cn", "zh_CN", "en_us", "en_US"]:
            if key in post_data:
                lang_content = post_data[key]
                break
        
        # If no specific lang found, use first available
        if lang_content is None and post_data:
            lang_content = list(post_data.values())[0]
        
        if not lang_content:
            return result
        
        # Extract title
        result["title"] = lang_content.get("title", "")
        
        # Extract content blocks
        content_blocks = lang_content.get("content", [])
        
        text_parts = []
        
        for block in content_blocks:
            # Each block is a list of elements
            if not isinstance(block, list):
                continue
                
            for element in block:
                if not isinstance(element, dict):
                    continue
                    
                tag = element.get("tag", "")
                
                if tag == "text":
                    text = element.get("text", "")
                    if text:
                        text_parts.append(text)
                        
                elif tag == "img":
                    image_key = element.get("image_key", "")
                    if image_key:
                        result["images"].append({
                            "image_key": image_key,
                            "width": element.get("width"),
                            "height": element.get("height"),
                        })
                        
                elif tag == "file":
                    file_key = element.get("file_key", "")
                    file_name = element.get("file_name", "unknown")
                    if file_key:
                        result["files"].append({
                            "file_key": file_key,
                            "file_name": file_name,
                        })
                        
                elif tag == "a":
                    # Link
                    href = element.get("href", "")
                    link_text = element.get("text", href)
                    if href:
                        text_parts.append(f"[{link_text}]({href})")
                        
                elif tag == "at":
                    # Mention
                    user_name = element.get("user_name", "")
                    if user_name:
                        text_parts.append(f"@{user_name}")
                        
                elif tag == "media":
                    # Video/Audio
                    file_key = element.get("file_key", "")
                    file_name = element.get("file_name", "media")
                    if file_key:
                        result["files"].append({
                            "file_key": file_key,
                            "file_name": file_name,
                            "type": "media",
                        })
        
        result["text"] = "\n".join(text_parts)
        return result


async def handle_post_message(
    handler: Any,
    client: Any,
    chat_id: str,
    content: dict,
    message_id: str | None,
    work_dir: str,
) -> str | None:
    """Handle post (rich text) message from Feishu.
    
    Args:
        handler: The SDKMessageHandler instance
        client: The FeishuSDKClient instance
        chat_id: Chat ID
        content: Message content dict
        message_id: Message ID for downloading resources
        work_dir: Working directory to save files
        
    Returns:
        Formatted text for Kimi, or None if failed
    """
    import asyncio
    import os
    
    parser = PostMessageParser()
    parsed = parser.parse(content)
    
    print(f"[POST] Parsed post message:")
    print(f"  Title: {parsed['title'][:50] if parsed['title'] else '(none)'}")
    print(f"  Text: {parsed['text'][:100] if parsed['text'] else '(none)'}...")
    print(f"  Images: {len(parsed['images'])}")
    print(f"  Files: {len(parsed['files'])}")
    
    # Send acknowledgment
    summary_parts = ["📄 收到富文本消息"]
    if parsed["title"]:
        summary_parts.append(f"标题: {parsed['title']}")
    if parsed["text"]:
        summary_parts.append(f"文字: {len(parsed['text'])} 字符")
    if parsed["images"]:
        summary_parts.append(f"图片: {len(parsed['images'])} 张")
    if parsed["files"]:
        summary_parts.append(f"文件: {len(parsed['files'])} 个")
    
    await asyncio.to_thread(
        client.send_text_message,
        chat_id,
        "\n".join(summary_parts),
    )
    
    # Process images
    saved_images = []
    for img in parsed["images"]:
        image_key = img["image_key"]
        print(f"[POST] Downloading image: {image_key}")
        
        result = await asyncio.to_thread(
            client.download_image,
            image_key,
            message_id,
        )
        
        if result:
            image_content, image_name = result
            save_path = os.path.join(work_dir, f"received_{image_name}")
            try:
                with open(save_path, "wb") as f:
                    f.write(image_content)
                saved_images.append(save_path)
                print(f"[POST] Image saved: {save_path}")
            except Exception as e:
                print(f"[POST ERROR] Failed to save image: {e}")
    
    # Process files
    saved_files = []
    for file_info in parsed["files"]:
        file_key = file_info["file_key"]
        file_name = file_info["file_name"]
        print(f"[POST] Downloading file: {file_name}")
        
        result = await asyncio.to_thread(
            client.download_file,
            file_key,
            message_id,
        )
        
        if result:
            file_content, actual_name = result
            save_path = os.path.join(work_dir, file_name)
            try:
                with open(save_path, "wb") as f:
                    f.write(file_content)
                saved_files.append({"path": save_path, "name": file_name})
                print(f"[POST] File saved: {save_path}")
            except Exception as e:
                print(f"[POST ERROR] Failed to save file: {e}")
    
    # Format for Kimi
    parts = []
    
    if parsed["title"]:
        parts.append(f"【{parsed['title']}】")
    
    if parsed["text"]:
        parts.append(f"用户说明: {parsed['text']}")
    
    for img_path in saved_images:
        parts.append(f"[图片已保存: {img_path}]")
    
    for file_info in saved_files:
        parts.append(f"[文件已保存: {file_info['name']} - {file_info['path']}]")
    
    return "\n\n".join(parts) if parts else None
