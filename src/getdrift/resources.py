"""Access to files packaged inside `getdrift` (templates, canonical schemas)."""

from pathlib import Path
from typing import Dict

_PKG_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = _PKG_DIR / "templates"
SCHEMAS_DIR = _PKG_DIR / "schemas"


def config_template() -> str:
    """Contents of the stub `.drift/config.yaml` written by `drift init`."""
    return (TEMPLATES_DIR / "config.yaml").read_text(encoding="utf-8")


def packaged_schemas() -> Dict[str, str]:
    """Canonical schema files, filename -> contents, copied into `.drift/schema/`."""
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(SCHEMAS_DIR.glob("*.schema.json"))
    }
