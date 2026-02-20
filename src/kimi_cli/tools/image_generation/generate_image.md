Generate an image from a text prompt using AI image generation services (e.g., Volcengine Ark).

**When to use:**
- When the user wants to create an image from a text description
- When you need to generate visual content for presentations, documents, or creative projects
- When the user asks for "draw", "create an image", "generate a picture", etc.
- **Image-to-Image**: When the user wants to generate images based on reference images (e.g., "generate a similar image to this one", "create variations of this image")
- **Group Images**: When the user wants to generate a sequence of related images (e.g., "generate a series of images showing...")

**Parameters:**
- `prompt` (required): Detailed text description of the image you want to generate. Be specific about style, lighting, colors, composition, and subject matter.
- `size` (optional): Image resolution. Common options include:
  - Aspect ratios: '1:1' (square), '3:4', '4:3', '16:9', '9:16'
  - Quality presets: '2K', '4K'
  - Specific resolutions: '1024x1024', etc.
  Defaults to '2K' if not specified.
- `watermark` (optional): Whether to include a watermark. Defaults to True.
- `image` (optional): Reference image(s) for image-to-image generation. Supported by doubao-seedream-4.5/4.0 models.
  - Single image: provide a file path or URL
  - Multiple images: provide a list of file paths or URLs (2-14 images for seedream-4.5/4.0)
  - Local files will be automatically converted to base64
  - Supported formats: jpeg, png, webp, bmp, tiff, gif
- `sequential_image_generation` (optional): Control group image generation (doubao-seedream-4.5/4.0 only).
  - 'disabled' (default): Generate a single image
  - 'auto': Automatically generate a sequence of related images
- `max_images` (optional): Maximum number of images in a group when sequential_image_generation is 'auto'. Range: 1-15. Defaults to 4.

**Example prompts:**
- "A serene mountain landscape at sunset, with snow-capped peaks reflecting golden light, misty valleys below, photorealistic style"
- "Cyberpunk cityscape at night, neon lights reflecting on wet streets, flying cars, futuristic architecture, cinematic lighting"
- "Portrait of a wise old owl wearing reading glasses, perched on a stack of ancient books, soft studio lighting, detailed feathers"

**Image-to-Image Examples:**
```python
# Single reference image
GenerateImage(prompt="A cat wearing a wizard hat, magical atmosphere", image="/path/to/cat.jpg")

# Multiple reference images (2-14)
GenerateImage(
    prompt="A fantasy landscape combining these elements",
    image=["/path/to/mountain.jpg", "/path/to/castle.jpg", "/path/to/sunset.jpg"]
)

# Generate a group of related images
GenerateImage(
    prompt="A cute robot exploring different environments",
    sequential_image_generation="auto",
    max_images=6
)

# Image-to-image with group generation
GenerateImage(
    prompt="Create variations of this character in different poses",
    image="/path/to/character.jpg",
    sequential_image_generation="auto",
    max_images=4
)
```

**Configuration:**
This tool requires configuration in `~/.kimi/config.toml`:

```toml
[services.image_generation]
enabled = true                          # Enable/disable the tool
base_url = "https://ark.cn-beijing.volces.com/api/v3"
api_key = "your-api-key-here"
model = "doubao-seedream-4-5-251128"
```

**Important Notes:**
- The tool will be automatically disabled if `enabled` is set to `false`, even if API key is configured
- If `services.image_generation` section is missing or API key is empty, the tool will not be loaded
- The API key can also be set via the `ARK_API_KEY` environment variable (if supported by your provider)
- **Image-to-Image capabilities:**
  - doubao-seedream-4.5/4.0: Supports single or multiple reference images (up to 14), group generation
  - doubao-seedream-3.0-t2i: Text-to-image only, no reference image support
  - doubao-seededit-3.0-i2i: Single reference image only
