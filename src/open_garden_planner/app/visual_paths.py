"""Owner PNG directories; existing embedded images need no path migration."""

from pathlib import Path


def visuals_dir(kind: str) -> Path:
    root = Path(__file__).resolve().parents[3] / "visuals"
    preferred = root / kind
    return preferred if preferred.is_dir() else root


def material_files() -> list[Path]:
    return sorted(visuals_dir("materials").glob("*.png"), key=lambda path: path.name.casefold())
