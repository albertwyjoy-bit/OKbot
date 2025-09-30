import re
import string
from pathlib import Path


def truncate_line(line: str, max_length: int) -> str:
    """Truncate a line if it exceeds max_length, preserving the beginning."""
    if len(line) <= max_length:
        return line
    m = re.match(r"[\r\n]+$", line)
    linebreak = m.group(0) if m else ""
    end = "..." + linebreak
    return line[: max_length - len(end)] + end


def load_desc(path: Path, substitutions: dict[str, str] | None = None) -> str:
    """Load a tool description from a file, with optional substitutions."""
    description = path.read_text()
    if substitutions:
        description = string.Template(description).substitute(substitutions)
    return description
