class FileOpsWindow:
    """Maintains a window of file operations."""

    pass


from .glob import Glob  # noqa: E402
from .grep import Grep  # noqa: E402
from .patch import PatchFile  # noqa: E402
from .read import ReadFile  # noqa: E402
from .replace import StrReplaceFile  # noqa: E402
from .write import WriteFile  # noqa: E402

__all__ = (
    "ReadFile",
    "Glob",
    "Grep",
    "WriteFile",
    "StrReplaceFile",
    "PatchFile",
)
