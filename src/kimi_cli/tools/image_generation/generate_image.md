Generate an image from a text prompt using AI image generation services (e.g., Volcengine Ark).

**When to use:**
- When the user wants to create an image from a text description
- When you need to generate visual content for presentations, documents, or creative projects
- When the user asks for "draw", "create an image", "generate a picture", etc.

**Parameters:**
- `prompt` (required): Detailed text description of the image you want to generate. Be specific about style, lighting, colors, composition, and subject matter.
- `size` (optional): Image resolution. Common options include:
  - Aspect ratios: '1:1' (square), '3:4', '4:3', '16:9', '9:16'
  - Quality presets: '2K', '4K'
  - Specific resolutions: '1024x1024', etc.
  Defaults to '1:1' if not specified.
- `watermark` (optional): Whether to include a watermark. Defaults to True.

**Example prompts:**
- "A serene mountain landscape at sunset, with snow-capped peaks reflecting golden light, misty valleys below, photorealistic style"
- "Cyberpunk cityscape at night, neon lights reflecting on wet streets, flying cars, futuristic architecture, cinematic lighting"
- "Portrait of a wise old owl wearing reading glasses, perched on a stack of ancient books, soft studio lighting, detailed feathers"

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
