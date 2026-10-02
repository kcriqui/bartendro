"""What can the bot make, and how much of each ingredient goes in a glass."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import options
from .models import Dispenser, Drink, Ingredient, Kind, Level, RecipeItem

STRENGTH_STEP = 0.25  # each strength/tartness step changes those ingredients by 25% (old drink page)


def available_ingredients(session: Session, dispenser_count: int | None = None) -> set[int]:
    """Ids of ingredients the bot can use now: what's on the dispensers (only the first
    `dispenser_count` if given, skipping empty ones when the level sensors are on), the generic
    ingredients those belong to, and every manual (added by hand) ingredient. Hand-added
    recipe lines (a dash of bitters) never count against a drink: the guest adds those.
    Port of mixer.get_available_drink_list's booze list."""
    q = select(Dispenser).where(Dispenser.ingredient_id.is_not(None))
    if dispenser_count is not None:
        q = q.where(Dispenser.number <= dispenser_count)
    if options.get(session, "use_liquid_level_sensors"):
        q = q.where(Dispenser.level != Level.OUT)
    pumped = {d.ingredient_id for d in session.scalars(q)}

    parents = dict(session.execute(select(Ingredient.id, Ingredient.generic_id)).all())
    have = set()
    for ing in pumped:
        while ing is not None and ing not in have:  # walk up: Tito's -> Vodka -> ...
            have.add(ing)
            ing = parents.get(ing)
    have |= set(session.scalars(select(Ingredient.id).where(Ingredient.manual)))
    return have


def makeable_drinks(session: Session, dispenser_count: int | None = None,
                    enabled_only: bool = True) -> list[Drink]:
    """Drinks whose every ingredient is available, sorted for a menu."""
    have = available_ingredients(session, dispenser_count)
    q = select(Drink).options(selectinload(Drink.items))
    if enabled_only:
        q = q.where(Drink.enabled)
    drinks = [d for d in session.scalars(q)
              if any(not i.by_hand for i in d.items)
              and all(i.ingredient_id in have for i in d.items if not i.by_hand)]
    return sorted(drinks, key=lambda d: (d.sort_name or d.name).lower())


def scale_recipe(drink: Drink, size_ml: float, strength: int = 0, tartness: int = 0,
                 include_manual: bool = False) -> dict[int, float]:
    """{ingredient id: ml} for a glass of `size_ml`. strength/tartness are steps (-N..N, see
    option strength_steps): +1 makes alcohol 25% more of the mix, tartness +1 does the same for
    tart ingredients and the opposite for sweet ones. Port of update_volumes() on the old
    drink page. Manual ingredients count toward the mix (as before) but are left out unless
    `include_manual`, since the pumps don't pour them."""
    adjusted, manual = {}, set()
    for item in drink.items:
        if item.by_hand:  # a dash of bitters: not part of the mix
            continue
        kind = item.ingredient.kind
        step = {Kind.ALCOHOL: strength, Kind.TART: tartness, Kind.SWEET: -tartness}.get(kind, 0)
        adjusted[item.ingredient_id] = max(item.parts * (1 + STRENGTH_STEP * step), 0.0)
        if item.ingredient.manual:
            manual.add(item.ingredient_id)
    total = sum(adjusted.values())
    if total <= 0:
        return {}
    return {i: size_ml * parts / total for i, parts in adjusted.items()
            if include_manual or i not in manual}


def _needs(session: Session, enabled_only: bool = True) -> dict[int, set[int]]:
    """{drink id: ingredient ids the pumps must pour} (manual ingredients left out)."""
    q = select(Drink).options(selectinload(Drink.items).selectinload(RecipeItem.ingredient))
    if enabled_only:
        q = q.where(Drink.enabled)
    return {d.id: needs for d in session.scalars(q)
            if (needs := {i.ingredient_id for i in d.items if not i.by_hand and not i.ingredient.manual})}


def one_bottle_away(session: Session, dispenser_count: int | None = None,
                    limit: int = 5) -> list[tuple[Ingredient, list[Drink]]]:
    """Ingredients that would each make more drinks possible if loaded: [(ingredient, the
    drinks it unlocks)], most drinks first. Only drinks missing exactly that one ingredient."""
    have = available_ingredients(session, dispenser_count)
    unlocks: dict[int, list[int]] = {}
    for drink_id, needs in _needs(session).items():
        missing = needs - have
        if len(missing) == 1:
            unlocks.setdefault(missing.pop(), []).append(drink_id)
    ranked = sorted(unlocks.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:limit]
    out = []
    for ing_id, drink_ids in ranked:
        drinks = sorted((session.get(Drink, d) for d in drink_ids), key=lambda d: d.name.lower())
        out.append((session.get(Ingredient, ing_id), drinks))
    return out


def suggest_bottles(session: Session, pumps: int, keep: list[int] | None = None
                    ) -> tuple[list[Ingredient], list[Drink]]:
    """Pick `pumps` bottles that make the most (enabled) drinks: greedy, each step adding the
    bottle that completes the most drinks (ties: the one that gets most drinks closest).
    `keep`: ingredient ids that must be among them. For planning a small bot."""
    needs = _needs(session)
    chosen: list[int] = list(dict.fromkeys(keep or []))[:pumps]
    candidates = set().union(*needs.values()) if needs else set()

    def complete(have: set[int]) -> int:
        return sum(1 for n in needs.values() if n <= have)

    def closeness(have: set[int]) -> int:
        return sum(len(n & have) for n in needs.values() if len(n - have) <= 1)

    while len(chosen) < pumps:
        have = set(chosen)
        best = max((c for c in candidates if c not in have),
                   key=lambda c: (complete(have | {c}), closeness(have | {c}), -c), default=None)
        if best is None:
            break
        chosen.append(best)
    have = set(chosen)
    drinks = [session.get(Drink, d) for d, n in needs.items() if n <= have]
    return ([session.get(Ingredient, i) for i in chosen],
            sorted(drinks, key=lambda d: (d.sort_name or d.name).lower()))
