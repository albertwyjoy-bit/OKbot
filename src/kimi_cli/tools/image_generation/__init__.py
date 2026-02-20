import base64
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
    image: str | list[str] | None = Field(
        description=(
            "Reference image(s) for image-to-image generation. Supported by doubao-seedream-4.5/4.0 models.\n"
            "- Single image: provide a file path or URL\n"
            "- Multiple images: provide a list of file paths or URLs (2-14 images for seedream-4.5/4.0)\n"
            "- Local files will be automatically converted to base64\n"
            "- URLs must be accessible\n"
            "Supported formats: jpeg, png, webp, bmp, tiff, gif"
        ),
        default=None,
    )
    sequential_image_generation: str = Field(
        description=(
            "Control sequential/group image generation (doubao-seedream-4.5/4.0 only).\n"
            "- 'disabled' (default): Generate a single image\n"
            "- 'auto': Automatically generate a sequence of related images (group)\n"
            "When enabled with reference images, generates a group of images based on the input."
        ),
        default="disabled",
    )
    max_images: int = Field(
        description=(
            "Maximum number of images to generate in a group (doubao-seedream-4.5/4.0 only).\n"
            "Only effective when sequential_image_generation is 'auto'.\n"
            "Range: 1-15. Note: input reference images + generated images ≤ 15."
        ),
        default=4,
        ge=1,
        le=15,
    )


class ImageGenerationData(BaseModel):
    """Data wrapper for image generation response."""

    url: str
    """URL of the generated image."""


class ImageGenerationResult(BaseModel):
    """Result from image generation API."""

    data: list[ImageGenerationData]
    """List of generated image data."""


def _image_to_base64(image_path: str) -> str:
    """Convert a local image file to base64 data URL."""
    path = Path(image_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {image_path}")
    
    # Determine MIME type from extension
    suffix = path.suffix.lower()
    mime_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
        ".gif": "image/gif",
    }
    mime_type = mime_types.get(suffix, "image/jpeg")
    
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    
    return f"data:{mime_type};base64,{encoded}"


def _process_image_input(image_input: str | list[str] | None) -> list[str] | str | None:
    """Process image input - convert local paths to base64, keep URLs as-is."""
    if image_input is None:
        return None
    
    if isinstance(image_input, str):
        # Single image - check if it's a URL or local path
        if image_input.startswith(("http://", "https://", "data:image/")):
            return image_input
        else:
            # Local file path
            return _image_to_base64(image_input)
    else:
        # List of images
        processed = []
        for img in image_input:
            if img.startswith(("http://", "https://", "data:image/")):
                processed.append(img)
            else:
                processed.append(_image_to_base64(img))
        return processed


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

        # Process reference images
        processed_images = None
        if params.image:
            try:
                processed_images = _process_image_input(params.image)
            except FileNotFoundError as e:
                return builder.error(
                    f"Reference image not found: {e}",
                    brief="Reference image not found",
                )
            except Exception as e:
                return builder.error(
                    f"Failed to process reference image: {e}",
                    brief="Failed to process reference image",
                )

        headers = {
            "Authorization": f"Bearer {api_key}",
        }
        if self._config.custom_headers:
            headers.update(self._config.custom_headers)

        # Build request body
        request_body: dict = {
            "model": model,
            "prompt": params.prompt,
            "size": params.size,
            "response_format": "url",
        }

        # Add extra_body parameters
        extra_body: dict = {
            "watermark": params.watermark,
        }

        # Add reference images if provided
        if processed_images:
            extra_body["image"] = processed_images

        # Add sequential image generation options for supported models
        if model in ("doubao-seedream-4.5-251128", "doubao-seedream-4.5", 
                     "doubao-seedream-4.0", "doubao-seedream-4.0-250615"):
            extra_body["sequential_image_generation"] = params.sequential_image_generation
            if params.sequential_image_generation == "auto":
                extra_body["sequential_image_generation_options"] = {
                    "max_images": params.max_images
                }

        request_body["extra_body"] = extra_body

        async with (
            new_client_session() as session,
            session.post(
                f"{base_url}/images/generations",
                headers=headers,
                json=request_body,
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

        if not result.data:
            return builder.error(
                "No image data returned from the generation service.",
                brief="No image generated",
            )

        # Build success message
        if params.sequential_image_generation == "auto" and len(result.data) > 1:
            builder.write(f"Generated {len(result.data)} images successfully!\n\n")
        else:
            builder.write(f"Image generated successfully!\n\n")

        for i, img_data in enumerate(result.data):
            if hasattr(img_data, 'url') and img_data.url:
                if len(result.data) > 1:
                    builder.write(f"[{i + 1}] URL: {img_data.url}\n")
                else:
                    builder.write(f"URL: {img_data.url}\n")

        builder.write(f"\nPrompt: {params.prompt}\n")
        builder.write(f"Size: {params.size}\n")
        builder.write(f"Model: {model}\n")
        if processed_images:
            num_ref_images = len(processed_images) if isinstance(processed_images, list) else 1
            builder.write(f"Reference images: {num_ref_images}\n")

        return builder.ok()


__all__ = ("GenerateImage",)
