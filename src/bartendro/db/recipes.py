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

from .models import HAND_UNITS, STEPS, Drink, Ingredient, Kind, RecipeItem

SOURCE = "classics"
UNITS_ML = {"ml": 1.0, "cl": 10.0, "oz": 29.57}  # measured (pumped, or by hand for manual ingredients)
# HAND_UNITS (dash, leaf, wedge, tsp, ...): counted, always added by the guest


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
    names, manual = set(), set()
    for ing in data.get("ingredient", []):
        if not ing.get("name"):
            raise RecipeFileError(f"{p}: an [[ingredient]] has no name")
        if ing.get("kind", "other") not in {k.value for k in Kind}:
            raise RecipeFileError(f"{p}: {ing['name']}: unknown kind {ing['kind']!r}")
        names.add(ing["name"].lower())
        if ing.get("manual"):
            manual.add(ing["name"].lower())
    for ing in data.get("ingredient", []):
        if ing.get("generic") and ing["generic"].lower() not in names:
            raise RecipeFileError(f"{p}: {ing['name']}: generic {ing['generic']!r} is not an [[ingredient]]")
    for d in data.get("drink", []):
        if not d.get("name") or not d.get("ingredients"):
            raise RecipeFileError(f"{p}: a [[drink]] needs a name and ingredients")
        if len({str(i[2]).lower() for i in d["ingredients"] if len(i) >= 3}) != len(d["ingredients"]):
            raise RecipeFileError(f"{p}: {d['name']}: an ingredient is listed twice")
        for item in d["ingredients"]:
            if len(item) not in (3, 4):
                raise RecipeFileError(f"{p}: {d['name']}: ingredients are [amount, unit, name] "
                                      f"or [amount, unit, name, \"before\"/\"after\"]")
            amount, unit, name = item[:3]
            if len(item) == 4 and item[3] not in STEPS:
                raise RecipeFileError(f"{p}: {d['name']}: {name}: step must be before or after")
            if unit not in UNITS_ML and unit not in HAND_UNITS:
                raise RecipeFileError(f"{p}: {d['name']}: unit {unit!r} "
                                      f"(use {', '.join([*UNITS_ML, *HAND_UNITS])})")
            if not isinstance(amount, (int, float)) or amount <= 0:
                raise RecipeFileError(f"{p}: {d['name']}: bad amount {amount!r}")
            if name.lower() not in names:
                raise RecipeFileError(f"{p}: {d['name']}: {name!r} is not an [[ingredient]]")
        if not any(i[1] in UNITS_ML and i[2].lower() not in manual for i in d["ingredients"]):
            raise RecipeFileError(f"{p}: {d['name']}: nothing for the pumps to pour")


def parse_amount(text: str) -> tuple[float | None, float | None, str, str]:
    """Admin form entry -> (parts, amount, unit, step): "2" is 2 parts, "30 ml" / "1 oz" are
    parts in ml, "2 dash" (or leaf, wedge, tsp, ...; plurals ok) is counted and added by hand.
    A trailing "before" / "after" says when a by-hand amount goes in (default after)."""
    words = text.strip().lower().split()
    step = "after"
    if words and words[-1] in STEPS:
        step = words.pop()
    if not words or len(words) > 2:
        raise ValueError(f"amount {text!r}: a number, optionally a unit, then before/after")
    amount = float(words[0])
    if amount <= 0:
        raise ValueError(f"amount {text!r} must be more than 0")
    if len(words) == 1:
        return amount, None, "", step
    unit = words[1]
    singular = {v: k for k, v in HAND_UNITS.items()}.get(unit, unit)
    if singular in HAND_UNITS:
        return None, amount, singular, step
    if unit in UNITS_ML:
        return amount * UNITS_ML[unit], amount, unit, step
    raise ValueError(f"unit {unit!r}: use ml, cl, oz or {', '.join(HAND_UNITS)}")


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
                               abv=float(spec.get("abv", 0)), manual=bool(spec.get("manual", False)),
                               on_hand=False)  # tick it in Admin once you have it
            session.add(found)
            by_name[_norm(found.name)] = found
            report.counts["ingredients added"] += 1
        resolved[_norm(spec["name"])] = found
    session.flush()

    for spec in data.get("ingredient", []):
        generic = resolved[_norm(spec["name"])]
        if spec.get("generic"):  # e.g. Reposado Tequila -> Tequila
            parent = resolved[_norm(spec["generic"])]
            if generic.generic_id is None and not _is_ancestor(generic, parent):
                generic.generic = parent
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
        drink.finish = spec.get("finish", "")
        drink.glass = spec.get("glass", "")
        # Pour the recipe as specified (a Godmother is 70 ml, not the default 150 ml glass);
        # the size buttons still scale it.
        drink.size_ml = round(sum(i[0] * UNITS_ML[i[1]] for i in spec["ingredients"] if i[1] in UNITS_ML))
        drink.items.clear()
        session.flush()  # delete the old rows first: (drink, ingredient) is unique
        drink.items = [RecipeItem(ingredient=resolved[_norm(item[2])],
                                  parts=item[0] * UNITS_ML[item[1]] if item[1] in UNITS_ML else None,
                                  amount=float(item[0]), unit=item[1],
                                  step=item[3] if len(item) == 4 else "after", position=n)
                       for n, item in enumerate(spec["ingredients"])]
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
