"""Automatic Speech Recognition (ASR) using GLM-ASR-2512.

This module provides speech-to-text functionality using Zhipu AI's GLM-ASR-2512 model.
https://docs.bigmodel.cn/cn/guide/models/sound-and-video/glm-asr-2512
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    pass


class GLMASR2512:
    """GLM-ASR-2512 speech recognition provider.
    
    GLM-ASR-2512 是智谱新一代语音识别模型，支持将语音实时转换为高质量文字。
    支持中英文语境、数字与单位组合、方言识别、低音量语音处理等。
    
    API Documentation: https://docs.bigmodel.cn/cn/guide/models/sound-and-video/glm-asr-2512
    """
    
    API_URL = "https://open.bigmodel.cn/api/paas/v4/audio/transcriptions"
    MODEL = "glm-asr-2512"
    
    def __init__(self, api_key: str | None = None):
        """Initialize GLM-ASR-2512.
        
        Args:
            api_key: Zhipu AI API key (defaults to ZHIPU_API_KEY env var)
        """
        self.api_key = (api_key or os.environ.get("ZHIPU_API_KEY") or "").strip()
        
        if not self.api_key:
            raise ValueError(
                "Zhipu API key is required for GLM-ASR-2512. "
                "Please set ZHIPU_API_KEY environment variable or provide api_key parameter."
            )
    
    def transcribe(self, audio_file_path: str | Path) -> str:
        """Transcribe audio file to text using GLM-ASR-2512.
        
        Args:
            audio_file_path: Path to the audio file
            
        Returns:
            Transcribed text
        """
        import requests
        
        audio_file_path = Path(audio_file_path)
        if not audio_file_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_file_path}")
        
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        
        with open(audio_file_path, "rb") as audio_file:
            files = {
                "file": (audio_file_path.name, audio_file),
            }
            data = {
                "model": self.MODEL,
                "stream": "false",
            }
            
            try:
                logger.info(f"Sending audio to GLM-ASR-2512: {audio_file_path}")
                response = requests.post(
                    self.API_URL,
                    headers=headers,
                    files=files,
                    data=data,
                    timeout=120  # ASR may take longer for large files
                )
                response.raise_for_status()
                result = response.json()
                
                # Extract text from response
                # Response format: {"text": "transcribed text"}
                text = result.get("text", "")
                if not text and "data" in result:
                    text = result["data"].get("text", "")
                
                logger.info(f"GLM-ASR-2512 transcription complete: {len(text)} chars")
                return text
                
            except requests.exceptions.HTTPError as e:
                error_msg = f"GLM-ASR-2512 API error: {e}"
                try:
                    error_data = response.json()
                    error_msg = f"GLM-ASR-2512 error: {error_data.get('error', {}).get('message', str(e))}"
                except:
                    pass
                logger.error(error_msg)
                raise RuntimeError(error_msg)
                
            except Exception as e:
                error_msg = f"GLM-ASR-2512 transcription failed: {e}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)


def transcribe_audio(audio_file_path: str | Path, api_key: str | None = None) -> str:
    """Transcribe audio file to text using GLM-ASR-2512.
    
    This is a convenience function that creates a GLMASR2512 instance
    and performs transcription.
    
    Args:
        audio_file_path: Path to the audio file
        api_key: Optional Zhipu API key. If not provided, uses ZHIPU_API_KEY env var.
        
    Returns:
        Transcribed text
        
    Raises:
        FileNotFoundError: If audio file doesn't exist
        RuntimeError: If transcription fails
        ValueError: If ZHIPU_API_KEY is not set and no api_key provided
    """
    asr = GLMASR2512(api_key=api_key)
    return asr.transcribe(audio_file_path)
