from pathlib import Path
from typing import override

from kosong.tooling import CallableTool2, ToolReturnValue
from pydantic import BaseModel, Field

from kimi_cli.config import Config
from kimi_cli.soul.agent import Runtime
from kimi_cli.tools import SkipThisTool
from kimi_cli.tools.utils import ToolResultBuilder, load_desc
from kimi_cli.utils.aiohttp import new_client_session


class Params(BaseModel):
    prompt: str = Field(
        description="Text description of the image to generate. Be detailed and specific."
    )
    size: str = Field(
        description=(
            "Image size/resolution. Two ways to specify (do not mix):\n"
            "Way 1 - Quality presets: '2K', '4K'. Use natural language in prompt to describe aspect ratio.\n"
            "Way 2 - Exact resolution: e.g., '2048x2048', '2560x1440'. "
            "Total pixels must be within [3686400, 16777216] (i.e., between 2560x1440 and 4096x4096). "
            "Aspect ratio must be within [1/16, 16].\n"
            "Valid examples: '2K', '4K', '2048x2048', '2560x1440', '3750x1250'\n"
            "Invalid example: '1500x1500' (total pixels 2250000 < 3686400)\n"
            "Defaults to '2K' if not specified."
        ),
        default="2K",
    )
    watermark: bool = Field(
        description="Whether to add watermark to the generated image. Defaults to True.",
        default=True,
    )


class ImageGenerationData(BaseModel):
    """Data wrapper for image generation response."""

    url: str
    """URL of the generated image."""


class ImageGenerationResult(BaseModel):
    """Result from image generation API."""

    data: list[ImageGenerationData]
    """List of generated image data."""


class GenerateImage(CallableTool2[Params]):
    name: str = "GenerateImage"
    description: str = load_desc(Path(__file__).parent / "generate_image.md", {})
    params: type[Params] = Params

    def __init__(self, config: Config, runtime: Runtime):
        super().__init__()
        if config.services.image_generation is None:
            raise SkipThisTool()
        if not config.services.image_generation.enabled:
            raise SkipThisTool()
        self._runtime = runtime
        self._config = config.services.image_generation

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        builder = ToolResultBuilder(max_line_length=None)

        api_key = self._config.api_key.get_secret_value()
        base_url = self._config.base_url
        model = self._config.model

        if not api_key:
            return builder.error(
                "Image generation service is not configured. "
                "Please configure the image_generation service in ~/.kimi/config.toml",
                brief="Image generation not configured",
            )

        headers = {
            "Authorization": f"Bearer {api_key}",
        }
        if self._config.custom_headers:
            headers.update(self._config.custom_headers)

        async with (
            new_client_session() as session,
            session.post(
                f"{base_url}/images/generations",
                headers=headers,
                json={
                    "model": model,
                    "prompt": params.prompt,
                    "size": params.size,
                    "response_format": "url",
                    "extra_body": {
                        "watermark": params.watermark,
                    },
                },
            ) as response,
        ):
            if response.status != 200:
                error_text = await response.text()
                return builder.error(
                    f"Failed to generate image. Status: {response.status}. Error: {error_text}",
                    brief="Failed to generate image",
                )

            try:
                result_data = await response.json()
                result = ImageGenerationResult.model_validate(result_data)
            except Exception as e:
                return builder.error(
                    f"Failed to parse image generation response. Error: {e}",
                    brief="Failed to parse response",
                )

        if not result.data or not result.data[0].url:
            return builder.error(
                "No image URL returned from the generation service.",
                brief="No image generated",
            )

        image_url = result.data[0].url
        builder.write(f"Image generated successfully!\n\n")
        builder.write(f"URL: {image_url}\n\n")
        builder.write(f"Prompt: {params.prompt}\n")
        builder.write(f"Size: {params.size}\n")
        builder.write(f"Model: {model}\n")

        return builder.ok()


__all__ = ("GenerateImage",)
