import string
from pathlib import Path


def load_desc(path: Path, substitutions: dict[str, str] | None = None) -> str:
    """Load a tool description from a file, with optional substitutions."""
    description = path.read_text()
    if substitutions:
        description = string.Template(description).substitute(substitutions)
    return description
