"""Access to files packaged inside `getdrift` (templates, canonical schemas)."""

from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = _PKG_DIR / "templates"


def config_template() -> str:
    """Contents of the stub `.drift/config.yaml` written by `drift init`."""
    return (TEMPLATES_DIR / "config.yaml").read_text(encoding="utf-8")
