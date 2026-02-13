"""Automatic Speech Recognition (ASR) using GLM-ASR-2512.

This module provides speech-to-text functionality using Zhipu AI's GLM-ASR-2512 model.
https://docs.bigmodel.cn/cn/guide/models/sound-and-video/glm-asr-2512

Supports long audio segmentation with overlapping windows to handle audio longer than 30 seconds.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    pass

# Maximum duration per segment in milliseconds (25s to leave buffer for 30s limit)
MAX_SEGMENT_MS = 25_000
# Overlap duration between segments in milliseconds
OVERLAP_MS = 2_000


class GLMASR2512:
    """GLM-ASR-2512 speech recognition provider.
    
    GLM-ASR-2512 是智谱新一代语音识别模型，支持将语音实时转换为高质量文字。
    支持中英文语境、数字与单位组合、方言识别、低音量语音处理等。
    
    Supports long audio segmentation with overlapping windows to handle audio longer than 30s.
    
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
    
    def _transcribe_single(self, audio_file_path: Path) -> str:
        """Transcribe a single audio file segment.
        
        Args:
            audio_file_path: Path to the audio file segment
            
        Returns:
            Transcribed text for this segment
        """
        import requests
        
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
                response = requests.post(
                    self.API_URL,
                    headers=headers,
                    files=files,
                    data=data,
                    timeout=120
                )
                response.raise_for_status()
                result = response.json()
                
                # Extract text from response
                text = result.get("text", "")
                if not text and "data" in result:
                    text = result["data"].get("text", "")
                
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
    
    def _get_audio_duration_ms(self, audio_file_path: Path) -> int:
        """Get audio duration in milliseconds.
        
        Args:
            audio_file_path: Path to the audio file
            
        Returns:
            Duration in milliseconds
        """
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_file(str(audio_file_path))
            return len(audio)
        except ImportError:
            logger.warning("pydub not installed, cannot determine audio duration")
            return 0
        except Exception as e:
            logger.warning(f"Failed to get audio duration: {e}")
            return 0
    
    def _split_audio(self, audio_file_path: Path) -> list[Path]:
        """Split audio into segments with overlap.
        
        Args:
            audio_file_path: Path to the original audio file
            
        Returns:
            List of paths to temporary segment files
            
        Raises:
            RuntimeError: If ffmpeg is not installed (required by pydub)
        """
        try:
            from pydub import AudioSegment
        except ImportError as e:
            raise RuntimeError(
                "pydub is required for long audio processing. "
                "Please install it: pip install pydub"
            ) from e
        
        try:
            audio = AudioSegment.from_file(str(audio_file_path))
        except Exception as e:
            # Check if it's an ffmpeg-related error
            error_str = str(e).lower()
            if "ffmpeg" in error_str or "avconv" in error_str or "not found" in error_str:
                raise RuntimeError(
                    "ffmpeg is required for audio processing but not found. "
                    "Please install ffmpeg:\n"
                    "  macOS: brew install ffmpeg\n"
                    "  Ubuntu/Debian: sudo apt-get install ffmpeg\n"
                    "  Windows: download from https://ffmpeg.org/download.html"
                ) from e
            raise
        duration_ms = len(audio)
        
        # If audio is short enough, no need to split
        if duration_ms <= MAX_SEGMENT_MS:
            return [audio_file_path]
        
        segments = []
        temp_dir = tempfile.mkdtemp(prefix="asr_segments_")
        
        # Calculate segment boundaries with overlap
        # segment i: [start_i, end_i] where end_i - start_i = MAX_SEGMENT_MS
        # next segment starts at: start_i + (MAX_SEGMENT_MS - OVERLAP_MS)
        step = MAX_SEGMENT_MS - OVERLAP_MS
        
        start = 0
        segment_index = 0
        
        while start < duration_ms:
            end = min(start + MAX_SEGMENT_MS, duration_ms)
            
            # Extract segment
            segment = audio[start:end]
            
            # Export to temporary file
            segment_path = Path(temp_dir) / f"segment_{segment_index:03d}.mp3"
            segment.export(str(segment_path), format="mp3")
            segments.append(segment_path)
            
            logger.debug(f"Created segment {segment_index}: {start}ms - {end}ms ({len(segment)}ms)")
            
            # Move to next segment
            start += step
            segment_index += 1
            
            # If remaining audio is less than overlap, include it in current segment
            if duration_ms - start < OVERLAP_MS and start < duration_ms:
                break
        
        logger.info(f"Split audio ({duration_ms}ms) into {len(segments)} segments with {OVERLAP_MS}ms overlap")
        return segments
    
    def _merge_transcriptions(self, texts: list[str]) -> str:
        """Merge transcription segments, removing duplicate content from overlaps.
        
        Uses a simple approach: for segments after the first, skip the first part
        that likely overlaps with previous segment.
        
        Args:
            texts: List of transcribed texts from each segment
            
        Returns:
            Merged text
        """
        if not texts:
            return ""
        
        if len(texts) == 1:
            return texts[0].strip()
        
        # Simple merging: just concatenate with spaces
        # Since ASR results are text and overlap content should be similar,
        # we rely on the fact that duplicate words in speech are rare
        # A more sophisticated approach would use fuzzy matching
        merged_parts = [texts[0].strip()]
        
        for i in range(1, len(texts)):
            current = texts[i].strip()
            previous = texts[i - 1].strip()
            
            if not current:
                continue
            
            # Try to find overlap by checking the end of previous and start of current
            # Look for common suffix/prefix of reasonable length (3-10 chars)
            overlap_found = False
            
            for overlap_len in range(min(10, len(previous), len(current)), 2, -1):
                prev_end = previous[-overlap_len:].replace(" ", "")
                curr_start = current[:overlap_len].replace(" ", "")
                
                # Compare without spaces for Chinese text
                if prev_end == curr_start:
                    # Found overlap, skip it in current
                    words = current.split()
                    char_count = 0
                    skip_words = 0
                    for word in words:
                        char_count += len(word)
                        skip_words += 1
                        if char_count >= overlap_len:
                            break
                    
                    remaining = " ".join(words[skip_words:])
                    if remaining:
                        merged_parts.append(remaining)
                    overlap_found = True
                    break
            
            if not overlap_found:
                merged_parts.append(current)
        
        return " ".join(merged_parts)
    
    def transcribe(self, audio_file_path: str | Path) -> str:
        """Transcribe audio file to text using GLM-ASR-2512.
        
        Automatically splits long audio into segments with overlap to handle
        audio longer than 30 seconds.
        
        Args:
            audio_file_path: Path to the audio file
            
        Returns:
            Transcribed text
        """
        audio_file_path = Path(audio_file_path)
        if not audio_file_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_file_path}")
        
        # Check audio duration
        duration_ms = self._get_audio_duration_ms(audio_file_path)
        
        # If audio is short or pydub not available, transcribe directly
        if duration_ms == 0 or duration_ms <= MAX_SEGMENT_MS:
            logger.info(f"Transcribing audio ({duration_ms}ms): {audio_file_path}")
            return self._transcribe_single(audio_file_path)
        
        # Split long audio into segments
        logger.info(f"Audio too long ({duration_ms}ms), splitting into segments...")
        segments = self._split_audio(audio_file_path)
        
        try:
            # Transcribe each segment
            transcriptions = []
            for i, segment_path in enumerate(segments):
                if segment_path == audio_file_path:
                    # Original file, not a segment
                    logger.info(f"Transcribing original file")
                else:
                    logger.info(f"Transcribing segment {i + 1}/{len(segments)}: {segment_path.name}")
                
                text = self._transcribe_single(segment_path)
                transcriptions.append(text)
                logger.debug(f"Segment {i + 1} result: {text[:50]}..." if len(text) > 50 else f"Segment {i + 1} result: {text}")
            
            # Merge transcriptions
            merged_text = self._merge_transcriptions(transcriptions)
            logger.info(f"Transcription complete: {len(merged_text)} chars from {len(transcriptions)} segments")
            
            return merged_text
            
        finally:
            # Clean up temporary segment files
            if segments and segments[0] != audio_file_path:
                temp_dir = segments[0].parent
                for segment_path in segments:
                    try:
                        segment_path.unlink()
                    except Exception:
                        pass
                try:
                    temp_dir.rmdir()
                except Exception:
                    pass


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
