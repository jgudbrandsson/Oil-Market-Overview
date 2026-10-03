import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config.toml"


@dataclass(frozen=True)
class Series:
    key: str
    source: str
    source_id: str
    unit: str
    frequency: str


@dataclass(frozen=True)
class Config:
    db_path: str
    series: tuple[Series, ...]

    def for_source(self, source: str) -> list[Series]:
        return [s for s in self.series if s.source == source]


def load(path: Path = DEFAULT_CONFIG) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    series = tuple(Series(**s) for s in raw.get("series", []))
    keys = [s.key for s in series]
    if len(keys) != len(set(keys)):
        raise ValueError(f"duplicate series key in {path}")
    db_path = os.environ.get("OIL_DASH_DB", raw["db_path"])
    return Config(db_path=db_path, series=series)
