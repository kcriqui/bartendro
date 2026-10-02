"""Typed settings stored in the `option` table. Defaults and names from ui/bartendro/options.py;
missing keys read as their default, so a new option needs no migration."""

from __future__ import annotations

import hashlib
import hmac
import os

from sqlalchemy.orm import Session

from .models import Option

DEFAULTS: dict[str, bool | int | str] = {
    "use_liquid_level_sensors": False,
    "must_login_to_dispense": False,
    "login_name": "bartendro",
    "login_password_hash": "",  # empty: no admin password set yet (see set_password)
    "metric": False,
    "drink_size": 150,      # ml
    "taster_size": 30,      # ml
    "shot_size": 30,        # ml
    "test_dispense_ml": 10,
    "min_pump_ml": 1,       # smaller pumped amounts are added by hand instead, if on hand
    "show_strength": True,
    "show_size": True,
    "show_taster": False,
    "strength_steps": 2,
    "use_shotbot_ui": False,
    "show_feeling_lucky": False,
    "turbo_mode": False,
}


def parse(key: str, text: str) -> bool | int | str:
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return text.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(float(text))
    return text


def _format(value: bool | int | str) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def get(session: Session, key: str) -> bool | int | str:
    if key not in DEFAULTS:
        raise KeyError(f"unknown option {key!r}")
    row = session.get(Option, key)
    if row is None:
        return DEFAULTS[key]
    try:
        return parse(key, row.value)
    except ValueError:
        return DEFAULTS[key]


def get_all(session: Session) -> dict[str, bool | int | str]:
    return {k: get(session, k) for k in DEFAULTS}


def set(session: Session, key: str, value: bool | int | str) -> None:  # noqa: A001 - mirrors get()
    if key not in DEFAULTS:
        raise KeyError(f"unknown option {key!r}")
    value = parse(key, _format(value))  # validate / normalise the type
    row = session.get(Option, key)
    if row is None:
        session.add(Option(key=key, value=_format(value)))
    else:
        row.value = _format(value)


# The old app kept the admin password in plain text; it is stored hashed now.
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(session: Session, password: str) -> bool:
    stored = str(get(session, "login_password_hash"))
    try:
        scheme, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    test = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    return hmac.compare_digest(test.hex(), digest)


def set_password(session: Session, password: str) -> None:
    set(session, "login_password_hash", hash_password(password))
