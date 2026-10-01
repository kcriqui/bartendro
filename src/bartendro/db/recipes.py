"""Load the bundled drinks (data/classics.toml) or another file in the same format.

Ingredients are matched to ones already in the database by name or alias (case-insensitive),
so loading into a database imported from an old bartendro.db reuses its bottles; brands listed
for an ingredient get linked to it (only if they aren't linked to something already). Drinks
that already exist (same name) are left alone, unless `update` and they came from this loader.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Drink, Ingredient, Kind, RecipeItem

SOURCE = "classics"
UNITS_ML = {"ml": 1.0, "cl": 10.0, "oz": 29.57}


class RecipeFileError(Exception):
    pass


@dataclass
class LoadReport:
    counts: dict[str, int] = field(default_factory=lambda: {
        "drinks added": 0, "drinks updated": 0, "drinks already there": 0,
        "ingredients added": 0, "ingredients matched": 0, "brand links": 0})
    notes: list[str] = field(default_factory=list)


def bundled_path() -> Path:
    return Path(str(resources.files("bartendro") / "data" / "classics.toml"))


def read(path: str | Path | None = None) -> dict:
    p = Path(path) if path else bundled_path()
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise RecipeFileError(f"{p}: {e}") from e
    _validate(data, p)
    return data


def _validate(data: dict, p: Path) -> None:
    names = set()
    for ing in data.get("ingredient", []):
        if not ing.get("name"):
            raise RecipeFileError(f"{p}: an [[ingredient]] has no name")
        if ing.get("kind", "other") not in {k.value for k in Kind}:
            raise RecipeFileError(f"{p}: {ing['name']}: unknown kind {ing['kind']!r}")
        names.add(ing["name"].lower())
    for d in data.get("drink", []):
        if not d.get("name") or not d.get("ingredients"):
            raise RecipeFileError(f"{p}: a [[drink]] needs a name and ingredients")
        if len({str(i[-1]).lower() for i in d["ingredients"]}) != len(d["ingredients"]):
            raise RecipeFileError(f"{p}: {d['name']}: an ingredient is listed twice")
        for item in d["ingredients"]:
            if len(item) != 3:
                raise RecipeFileError(f"{p}: {d['name']}: ingredients are [amount, unit, name]")
            amount, unit, name = item
            if unit not in UNITS_ML:
                raise RecipeFileError(f"{p}: {d['name']}: unit {unit!r} (use {', '.join(UNITS_ML)})")
            if not isinstance(amount, (int, float)) or amount <= 0:
                raise RecipeFileError(f"{p}: {d['name']}: bad amount {amount!r}")
            if name.lower() not in names:
                raise RecipeFileError(f"{p}: {d['name']}: {name!r} is not an [[ingredient]]")


def _norm(name: str) -> str:
    return " ".join(name.lower().split())


def load(session: Session, path: str | Path | None = None, update: bool = False) -> LoadReport:
    """Add the drinks in `path` (default: the bundled file) to the database and commit."""
    data = read(path)
    report = LoadReport()
    by_name = {_norm(i.name): i for i in session.scalars(select(Ingredient))}

    resolved: dict[str, Ingredient] = {}
    for spec in data.get("ingredient", []):
        found = None
        for candidate in [spec["name"], *spec.get("aliases", [])]:
            found = by_name.get(_norm(candidate))
            if found:
                break
        if found:
            report.counts["ingredients matched"] += 1
            if _norm(found.name) != _norm(spec["name"]):
                report.notes.append(f"using your {found.name!r} for {spec['name']!r}")
        else:
            found = Ingredient(name=spec["name"], kind=Kind(spec.get("kind", "other")),
                               abv=float(spec.get("abv", 0)), manual=False)
            session.add(found)
            by_name[_norm(found.name)] = found
            report.counts["ingredients added"] += 1
        resolved[_norm(spec["name"])] = found
    session.flush()

    for spec in data.get("ingredient", []):
        generic = resolved[_norm(spec["name"])]
        for brand in spec.get("brands", []):
            ing = by_name.get(_norm(brand))
            if ing is None or ing is generic or ing.generic_id is not None:
                continue
            if _is_ancestor(ing, generic):  # don't create a loop
                continue
            ing.generic = generic
            report.counts["brand links"] += 1
            report.notes.append(f"{ing.name} is now a brand of {generic.name}")

    existing = {_norm(d.name): d for d in session.scalars(select(Drink))}
    for spec in data.get("drink", []):
        drink = existing.get(_norm(spec["name"]))
        if drink is not None and not (update and drink.source == SOURCE):
            report.counts["drinks already there"] += 1
            continue
        if drink is None:
            drink = Drink(name=spec["name"], enabled=True, source=SOURCE,
                          popular=bool(spec.get("popular", False)))
            session.add(drink)
            report.counts["drinks added"] += 1
        else:
            report.counts["drinks updated"] += 1
        drink.description = spec.get("description", "")
        drink.instructions = spec.get("instructions", "")
        drink.glass = spec.get("glass", "")
        # Pour the recipe as specified (a Godmother is 70 ml, not the default 150 ml glass);
        # the size buttons still scale it.
        drink.size_ml = round(sum(a * UNITS_ML[u] for a, u, _ in spec["ingredients"]))
        drink.items.clear()
        session.flush()  # delete the old rows first: (drink, ingredient) is unique
        drink.items = [RecipeItem(ingredient=resolved[_norm(name)], parts=amount * UNITS_ML[unit],
                                  amount=float(amount), unit=unit, position=n)
                       for n, (amount, unit, name) in enumerate(spec["ingredients"])]
    session.commit()
    return report


def _is_ancestor(candidate: Ingredient, of: Ingredient) -> bool:
    """Is `candidate` already `of` or one of its generics (Whiskey for Scotch)?"""
    ing, depth = of, 0
    while ing is not None and depth < 10:
        if ing is candidate:
            return True
        ing, depth = ing.generic, depth + 1
    return False
