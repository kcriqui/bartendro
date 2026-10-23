from pathlib import Path

import pytest

from bartendro import config

EXAMPLE = Path(__file__).parent.parent / "docs" / "bartendro.example.toml"


def test_defaults_without_a_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BARTENDRO_CONFIG", raising=False)
    monkeypatch.setattr(config, "SEARCH_PATHS", [Path("bartendro.toml")])
    cfg = config.load()
    assert (cfg.name, cfg.hardware.ports, cfg.hardware.dispensers, cfg.ui.screen) == ("Bartendro", 15, None, False)


def test_example_file_parses():
    cfg = config.load(EXAMPLE)
    assert cfg.hardware.ports == 15 and cfg.hardware.dispensers == 15 and cfg.ui.screen
    assert cfg.database_path() == Path("/var/lib/bartendro/bartendro.db")


def test_small_bot(tmp_path, monkeypatch):
    f = tmp_path / "bot.toml"
    f.write_text('name = "Margaritabot"\n[hardware]\nports = 3\ndispensers = 3\n'
                 '[database]\npath = "data/m.db"\n')
    monkeypatch.setenv("BARTENDRO_CONFIG", str(f))
    cfg = config.load()
    assert (cfg.name, cfg.hardware.ports, cfg.hardware.dispensers) == ("Margaritabot", 3, 3)
    assert cfg.database_path() == f.resolve().parent / "data" / "m.db"  # relative to the file


@pytest.mark.parametrize("text, msg", [
    ("[hardware]\nport = 3\n", "unknown setting"),          # typo
    ("colour = 'red'\n", "unknown setting"),
    ("[hardware]\nports = 16\n", "ports must be 1-15"),
    ("[hardware]\nports = 3\ndispensers = 5\n", "dispensers must be 0-3"),
    ("[hardware]\nports = '3'\n", "should be int"),
    ("[hardware]\nstatus_led = 1\n", "should be bool"),
    ("[ui]\nscreen = true\n[ui\n", "config"),               # bad TOML
])
def test_bad_config(text, msg):
    with pytest.raises(config.ConfigError, match=msg):
        config.parse(text)


def test_missing_explicit_file(tmp_path):
    with pytest.raises(config.ConfigError, match="not found"):
        config.load(tmp_path / "nope.toml")
