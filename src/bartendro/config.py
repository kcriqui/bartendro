"""Per-bot settings that belong to the machine, not the drink data: which router board, how
many dispensers, where the database lives, whether there's a screen. One TOML file per bot
(see docs/bartendro.example.toml); every key is optional.

Search order: an explicit path, $BARTENDRO_CONFIG, ./bartendro.toml, /etc/bartendro/bartendro.toml.
No file at all means the defaults (a 15-port Bartendro).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

SEARCH_PATHS = [Path("bartendro.toml"), Path("/etc/bartendro/bartendro.toml")]


class ConfigError(Exception):
    pass


@dataclass
class HardwareConfig:
    ports: int = 15            # router ports: 15 (Bartendro router) or 3 (mini-router)
    dispensers: int | None = None  # how many dispensers should be found (warn if not); None = don't check
    serial_device: str = ""    # empty: /dev/serial0, ttyAMA0, ttyS0
    i2c_bus: int = 1
    status_led: bool = True
    simulate: int = 0          # >0: no hardware, simulate this many dispensers


@dataclass
class DatabaseConfig:
    path: str = "bartendro.db"  # relative paths are relative to the config file


@dataclass
class UIConfig:
    screen: bool = False  # a touchscreen runs the kiosk browser on this bot


@dataclass
class Config:
    name: str = "Bartendro"
    hardware: HardwareConfig = field(default_factory=HardwareConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    source: Path | None = None  # file it was loaded from

    def database_path(self) -> Path:
        p = Path(self.database.path).expanduser()
        if not p.anchor and self.source is not None:  # anchor: "/x" is absolute on Windows too
            p = self.source.parent / p
        return p


def _fill(cls, data: dict, where: str):
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ConfigError(f"unknown setting(s) in [{where}]: {', '.join(sorted(unknown))}")
    obj = cls()
    for key, value in data.items():
        expected = type(getattr(obj, key))
        if getattr(obj, key) is None:  # optional int
            expected = int
        if expected is float and isinstance(value, int):
            value = float(value)
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ConfigError(f"[{where}] {key} should be {expected.__name__}, not {value!r}")
        setattr(obj, key, value)
    return obj


def _validate(cfg: Config) -> None:
    hw = cfg.hardware
    if not 1 <= hw.ports <= 15:
        raise ConfigError(f"[hardware] ports must be 1-15, not {hw.ports}")
    if hw.dispensers is not None and not 0 <= hw.dispensers <= hw.ports:
        raise ConfigError(f"[hardware] dispensers must be 0-{hw.ports}, not {hw.dispensers}")
    if not 0 <= hw.simulate <= 15:
        raise ConfigError(f"[hardware] simulate must be 0-15, not {hw.simulate}")


def parse(text: str, source: Path | None = None) -> Config:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{source or 'config'}: {e}") from e
    sections = {"hardware": HardwareConfig, "database": DatabaseConfig, "ui": UIConfig}
    top = {k: v for k, v in data.items() if k not in sections}
    if set(top) - {"name"}:
        raise ConfigError(f"unknown setting(s): {', '.join(sorted(set(top) - {'name'}))}")
    cfg = Config(source=source)
    if "name" in top:
        if not isinstance(top["name"], str):
            raise ConfigError("name should be a string")
        cfg.name = top["name"]
    for key, cls in sections.items():
        section = data.get(key, {})
        if not isinstance(section, dict):
            raise ConfigError(f"{key} should be a [{key}] section")
        setattr(cfg, key, _fill(cls, section, key))
    _validate(cfg)
    return cfg


def find(path: str | Path | None = None) -> Path | None:
    if path:
        p = Path(path)
        if not p.is_file():
            raise ConfigError(f"config file {p} not found")
        return p
    env = os.environ.get("BARTENDRO_CONFIG")
    if env:
        return find(env)
    return next((p for p in SEARCH_PATHS if p.is_file()), None)


def load(path: str | Path | None = None) -> Config:
    p = find(path)
    if p is None:
        return Config()
    return parse(p.read_text(encoding="utf-8"), source=p.resolve())
