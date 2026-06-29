from pathlib import Path
from utils.logger import get_logger

log = get_logger("file_utils")


def ensure_dir(path: str) -> None:
    """Create directory if it does not exist."""
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    log.debug(f"Ensured directory exists: {directory}")


def get_project_root() -> Path:
    """Project root = parent of this file's parent."""
    return Path(__file__).resolve().parent.parent


def build_path(*parts: str) -> Path:
    """Build an absolute path from project root and given parts."""
    root = get_project_root()
    return root.joinpath(*parts)