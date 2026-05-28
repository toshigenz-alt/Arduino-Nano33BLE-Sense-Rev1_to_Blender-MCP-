import os
from pathlib import Path


def load_dotenv(path: str = ".env") -> None:
    """Load a simple KEY=VALUE env file without adding a runtime dependency."""
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_tag_filter(value: str) -> dict[str, str]:
    filters: dict[str, str] = {}
    for item in (value or "").split(","):
        if not item.strip() or "=" not in item:
            continue
        key, val = item.split("=", 1)
        filters[key.strip()] = val.strip().strip('"').strip("'")
    return filters
