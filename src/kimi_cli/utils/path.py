import re
from pathlib import Path

import aiofiles.os


async def next_available_rotation(path: Path) -> Path | None:
    """
    Find the next available rotation path for a given path.
    """
    if not path.parent.exists():
        return None
    base_name = path.stem
    suffix = path.suffix
    pattern = re.compile(rf"^{re.escape(base_name)}_(\d+){re.escape(suffix)}$")
    max_num = 0
    # FIXME: protect from race condition
    for p in await aiofiles.os.listdir(path.parent):
        if m := pattern.match(p):
            max_num = max(max_num, int(m.group(1)))
    next_num = max_num + 1
    next_path = path.parent / f"{base_name}_{next_num}{suffix}"
    return next_path
