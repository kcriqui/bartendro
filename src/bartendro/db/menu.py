"""What can the bot make, and how much of each ingredient goes in a glass."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import options
from .models import Dispenser, Drink, Ingredient, Kind, Level, RecipeItem

STRENGTH_STEP = 0.25  # each strength/tartness step changes those ingredients by 25% (old drink page)


def _with_generics(session: Session, ids: set[int]) -> set[int]:
    """`ids` plus every generic they belong to (Tito's -> Vodka; Reposado Tequila -> Tequila)."""
    parents = dict(session.execute(select(Ingredient.id, Ingredient.generic_id)).all())
    have: set[int] = set()
    for ing in ids:
        while ing is not None and ing not in have:
            have.add(ing)
            ing = parents.get(ing)
    return have


def pumped_and_on_hand(session: Session, dispenser_count: int | None = None) -> tuple[set[int], set[int]]:
    """(ingredients the pumps can pour, ingredients the guest can add by hand), each with the
    generics they stand in for. Pumped: on a dispenser (only the first `dispenser_count` if
    given; not ones that are out when the level sensors are on). By hand: marked on hand.
    Port of mixer.get_available_drink_list's booze list, plus the on-hand list."""
    q = select(Dispenser).where(Dispenser.ingredient_id.is_not(None))
    if dispenser_count is not None:
        q = q.where(Dispenser.number <= dispenser_count)
    if options.get(session, "use_liquid_level_sensors"):
        q = q.where(Dispenser.level != Level.OUT)
    pumped = {d.ingredient_id for d in session.scalars(q)}
    on_hand = set(session.scalars(select(Ingredient.id).where(Ingredient.on_hand)))
    return _with_generics(session, pumped), _with_generics(session, on_hand)


def available_ingredients(session: Session, dispenser_count: int | None = None) -> set[int]:
    """Everything that can go in a drink right now, pumped or by hand."""
    pumped, on_hand = pumped_and_on_hand(session, dispenser_count)
    return pumped | on_hand


def missing_for(drink: Drink, pumped: set[int], on_hand: set[int]) -> set[tuple[int, bool]]:
    """What `drink` lacks: {(ingredient id, by hand?)}. A pumped line needs its ingredient on a
    dispenser; a by-hand line needs it on hand."""
    return {(i.ingredient_id, i.by_hand) for i in drink.items
            if i.ingredient_id not in (on_hand if i.by_hand else pumped)}


def makeable_drinks(session: Session, dispenser_count: int | None = None,
                    enabled_only: bool = True) -> list[Drink]:
    """Drinks the bot can pour (at least one pumped line) with everything available, sorted
    for a menu."""
    pumped, on_hand = pumped_and_on_hand(session, dispenser_count)
    drinks = [d for d in _drinks(session, enabled_only)
              if any(not i.by_hand for i in d.items) and not missing_for(d, pumped, on_hand)]
    return sorted(drinks, key=lambda d: (d.sort_name or d.name).lower())


def _drinks(session: Session, enabled_only: bool = True) -> list[Drink]:
    q = select(Drink).options(selectinload(Drink.items).selectinload(RecipeItem.ingredient))
    if enabled_only:
        q = q.where(Drink.enabled)
    return list(session.scalars(q))


def scale_recipe(drink: Drink, size_ml: float, strength: int = 0, tartness: int = 0,
                 include_manual: bool = False) -> dict[int, float]:
    """{ingredient id: ml} for a glass of `size_ml`. strength/tartness are steps (-N..N, see
    option strength_steps): +1 makes alcohol 25% more of the mix, tartness +1 does the same for
    tart ingredients and the opposite for sweet ones. Port of update_volumes() on the old
    drink page. Manual ingredients (half and half) count toward the mix, as before, but are
    left out unless `include_manual`, since the pumps don't pour them."""
    adjusted, manual = {}, set()
    for item in drink.items:
        if item.parts is None:  # counted ("2 dash"): not part of the mix
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


def _pumped_needs(session: Session, enabled_only: bool = True) -> dict[int, set[int]]:
    """{drink id: ingredient ids the pumps must pour}."""
    return {d.id: needs for d in _drinks(session, enabled_only)
            if (needs := {i.ingredient_id for i in d.items if not i.by_hand})}


def one_bottle_away(session: Session, dispenser_count: int | None = None,
                    limit: int = 5) -> list[tuple[Ingredient, bool, list[Drink]]]:
    """What to get next: [(ingredient, by hand?, the drinks it would make possible)], most
    drinks first. Counts drinks missing exactly that one thing - a bottle to load on a pump,
    or something to have on hand (Peychaud's for a Sazerac)."""
    pumped, on_hand = pumped_and_on_hand(session, dispenser_count)
    unlocks: dict[tuple[int, bool], list[Drink]] = {}
    for d in _drinks(session):
        if not any(not i.by_hand for i in d.items):
            continue
        missing = missing_for(d, pumped, on_hand)
        if len(missing) == 1:
            unlocks.setdefault(missing.pop(), []).append(d)
    ranked = sorted(unlocks.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:limit]
    return [(session.get(Ingredient, ing_id), by_hand, sorted(drinks, key=lambda d: d.name.lower()))
            for (ing_id, by_hand), drinks in ranked]


def suggest_bottles(session: Session, pumps: int, keep: list[int] | None = None
                    ) -> tuple[list[Ingredient], list[Drink]]:
    """Pick `pumps` bottles that make the most (enabled) drinks: greedy, each step adding the
    bottle that completes the most drinks (ties: the one that gets most drinks closest).
    `keep`: ingredient ids that must be among them. For planning a small bot; by-hand
    ingredients don't need a pump and are assumed to be on hand."""
    needs = _pumped_needs(session)
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
