"""What can the bot make, how much of each ingredient goes in a glass, and menu sections."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import options
from .models import Dispenser, Drink, Ingredient, Kind, Level, RecipeItem

STRENGTH_STEP = 0.25  # each strength/tartness step changes those ingredients by 25% (old drink page)
# Menu sections for spirits, recognised by name (ABVs in old databases are unreliable: tequila
# and gin at 30%, Cointreau at 40%). Liqueur-style names never count as the spirit.
SPIRIT_WORDS = {"vodka": "Vodka", "gin": "Gin", "rum": "Rum", "tequila": "Tequila", "mezcal": "Mezcal",
                "whiskey": "Whiskey", "whisky": "Whiskey", "bourbon": "Whiskey", "scotch": "Whiskey",
                "rye": "Whiskey", "brandy": "Brandy", "cognac": "Brandy", "armagnac": "Brandy",
                "cachaca": "Cachaca", "cachaça": "Cachaca", "pisco": "Pisco"}
NOT_SPIRIT_WORDS = {"liqueur", "cream", "schnapps", "sloe", "creme", "crème"}
LIQUEURS = "Liqueurs & Wine"
NON_ALCOHOLIC = "Non-alcoholic"

PUMP, HAND = "pump", "hand"


def _with_generics(session: Session, ids: set[int]) -> set[int]:
    """`ids` plus every generic they belong to (Tito's -> Vodka; Tequila, Reposado -> Tequila)."""
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


def resolve_line(item: RecipeItem, pumped: set[int], on_hand: set[int]) -> str | None:
    """PUMP if the line can be pumped and its ingredient is on a dispenser, else HAND if it's on
    hand, else None (the drink can't be made). The bot may still move a tiny pumped amount to
    by hand (option min_pump_ml)."""
    if item.pumpable and item.ingredient_id in pumped:
        return PUMP
    if item.ingredient_id in on_hand:
        return HAND
    return None


def missing_for(drink: Drink, pumped: set[int], on_hand: set[int]) -> set[tuple[int, bool]]:
    """What `drink` lacks: {(ingredient id, get it for adding by hand?)} - by hand for things
    that can't be pumped and counted amounts (a dash of bitters), else load it on a pump."""
    return {(i.ingredient_id, i.by_hand or i.parts is None) for i in drink.items
            if resolve_line(i, pumped, on_hand) is None}


def can_make(drink: Drink, pumped: set[int], on_hand: set[int]) -> bool:
    """Everything available, and the pumps pour at least one line."""
    resolved = [resolve_line(i, pumped, on_hand) for i in drink.items]
    return bool(resolved) and None not in resolved and PUMP in resolved


def makeable_drinks(session: Session, dispenser_count: int | None = None,
                    enabled_only: bool = True) -> list[Drink]:
    """Drinks the bot can make right now, sorted for a menu."""
    pumped, on_hand = pumped_and_on_hand(session, dispenser_count)
    drinks = [d for d in _drinks(session, enabled_only) if can_make(d, pumped, on_hand)]
    return sorted(drinks, key=sort_key)


def sort_key(d: Drink) -> str:
    return (d.sort_name or d.name).lower()


def _drinks(session: Session, enabled_only: bool = True) -> list[Drink]:
    q = select(Drink).options(selectinload(Drink.items).selectinload(RecipeItem.ingredient))
    if enabled_only:
        q = q.where(Drink.enabled)
    return list(session.scalars(q))


def scale_recipe(drink: Drink, size_ml: float, strength: int = 0, tartness: int = 0,
                 include_manual: bool = True) -> dict[int, float]:
    """{ingredient id: ml} for a glass of `size_ml`, for every line that's part of the mix
    (counted amounts like "2 dash" aren't). strength/tartness are steps (-N..N, see option
    strength_steps): +1 makes alcoholic ingredients 25% more of the mix, tartness +1 does the
    same for tart ingredients and the opposite for sweet ones. Port of update_volumes() on the
    old drink page. `include_manual=False` leaves out never-pumped ingredients."""
    adjusted, never_pumped = {}, set()
    for item in drink.items:
        if item.parts is None:
            continue
        ing = item.ingredient
        step = strength if ing.alcoholic else {Kind.TART: tartness, Kind.SWEET: -tartness}.get(ing.kind, 0)
        adjusted[item.ingredient_id] = max(item.parts * (1 + STRENGTH_STEP * step), 0.0)
        if ing.manual:
            never_pumped.add(item.ingredient_id)
    total = sum(adjusted.values())
    if total <= 0:
        return {}
    return {i: size_ml * parts / total for i, parts in adjusted.items()
            if include_manual or i not in never_pumped}


ETHANOL_DENSITY = 0.789  # g/ml
STANDARD_DRINK_G = 14.0  # US standard drink: 14 g of alcohol


def strength_of(lines: list[tuple[Ingredient, float]]) -> tuple[float, float, float]:
    """(total ml, ABV %, standard drinks) for [(ingredient, ml)] - the measured lines of a pour."""
    total = sum(ml for _, ml in lines)
    ethanol = sum(ml * ing.abv / 100 for ing, ml in lines if ing.alcoholic)
    abv = 100 * ethanol / total if total else 0.0
    return total, abv, ethanol * ETHANOL_DENSITY / STANDARD_DRINK_G


def uses(drink: Drink, ingredient: Ingredient) -> bool:
    """Does the drink pour `ingredient` (or the generic it stands in for) from a pump?"""
    chain, ing, depth = set(), ingredient, 0
    while ing is not None and depth < 10:
        chain.add(ing.id)
        ing, depth = ing.generic, depth + 1
    return any(i.pumpable and i.ingredient_id in chain for i in drink.items)


# ------------------------------------------------------------------ categories

def root(ing: Ingredient) -> Ingredient:
    """The top-level generic: Tequila, Reposado -> Tequila, Whiskey, Rye -> Whiskey."""
    depth = 0
    while ing.generic is not None and depth < 10:
        ing, depth = ing.generic, depth + 1
    return ing


def spirit_of(ing: Ingredient) -> str | None:
    """The spirit section an ingredient belongs to ("Rum" for "Rum, White", "Rum, Dark" or a brand
    linked to Rum), or None for liqueurs, wine and mixers."""
    if not ing.alcoholic:
        return None
    for candidate in (root(ing), ing):
        words = "".join(c if c.isalnum() else " " for c in candidate.name.lower()).split()
        if NOT_SPIRIT_WORDS & set(words):
            return None
        for w in words:
            if w in SPIRIT_WORDS:
                return SPIRIT_WORDS[w]
    return None


def categories_of(drink: Drink) -> list[str]:
    """Menu sections, main one first: the override if set; else one per spirit in the drink
    (spirit_of: Whiskey, Tequila, Rum, ...) - a Long Island Iced Tea is under Vodka, Tequila,
    Rum and Gin - ordered by how much alcohol it brings; "Liqueurs & Wine" when its alcohol is
    all liqueur / wine; "Non-alcoholic" without alcohol. Dashes (bitters) don't count."""
    if drink.category:
        return [drink.category]
    ethanol: dict[str, float] = {}
    weaker = False
    for item in drink.items:
        ing = item.ingredient
        if not ing.alcoholic or item.parts is None:
            continue
        spirit = spirit_of(ing)
        if spirit:
            ethanol[spirit] = ethanol.get(spirit, 0.0) + item.parts * max(ing.abv, 1.0)
        else:
            weaker = True
    if ethanol:
        return sorted(ethanol, key=lambda name: (-ethanol[name], name))
    return [LIQUEURS] if weaker else [NON_ALCOHOLIC]


def category_of(drink: Drink) -> str:
    """The drink's main menu section (see categories_of)."""
    return categories_of(drink)[0]


def by_category(drinks: list[Drink], every_section: bool = True) -> dict[str, list[Drink]]:
    """{section: drinks}, sections alphabetical with Liqueurs & Wine and Non-alcoholic last. A
    drink with several spirits is in each of their sections (`every_section=False`: only its
    main one, e.g. for pickers that must list each drink once)."""
    out: dict[str, list[Drink]] = {}
    for d in drinks:
        for section in categories_of(d) if every_section else [category_of(d)]:
            out.setdefault(section, []).append(d)
    order = sorted(out, key=lambda c: (c in (LIQUEURS, NON_ALCOHOLIC), c == NON_ALCOHOLIC, c.lower()))
    return {c: sorted(out[c], key=sort_key) for c in order}


# ------------------------------------------------------------------ planning helpers

def _pumped_needs(session: Session, enabled_only: bool = True) -> dict[int, set[int]]:
    """{drink id: ingredient ids that need a pump} - measured, pumpable lines; counted amounts
    and never-pumped things are assumed to be added by hand."""
    return {d.id: needs for d in _drinks(session, enabled_only)
            if (needs := {i.ingredient_id for i in d.items if i.pumpable and i.parts is not None})}


def one_bottle_away(session: Session, dispenser_count: int | None = None,
                    limit: int = 5) -> list[tuple[Ingredient, bool, list[Drink]]]:
    """What to get next: [(ingredient, by hand?, the drinks it would make possible)], most
    drinks first. Counts drinks missing exactly that one thing - a bottle to load on a pump,
    or something to have on hand (Peychaud's for a Sazerac)."""
    pumped, on_hand = pumped_and_on_hand(session, dispenser_count)
    unlocks: dict[tuple[int, bool], list[Drink]] = {}
    for d in _drinks(session):
        missing = missing_for(d, pumped, on_hand)
        if len(missing) != 1:
            continue
        ing_id, by_hand = next(iter(missing))
        if not by_hand:
            ok = can_make(d, pumped | {ing_id}, on_hand)
        else:
            ok = can_make(d, pumped, on_hand | {ing_id})
        if ok:
            unlocks.setdefault((ing_id, by_hand), []).append(d)
    ranked = sorted(unlocks.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:limit]
    return [(session.get(Ingredient, ing_id), by_hand, sorted(drinks, key=lambda d: d.name.lower()))
            for (ing_id, by_hand), drinks in ranked]


def suggest_bottles(session: Session, pumps: int, keep: list[int] | None = None,
                    beam: int = 30) -> tuple[list[Ingredient], list[Drink]]:
    """Pick `pumps` bottles that make the most (enabled) drinks. Beam search: keeps the `beam`
    best partial sets at each step (scored by drinks completed, then how close the rest are),
    so it isn't fooled by a bottle that completes one drink on its own. `keep`: ingredient ids
    that must be among them. For planning a small bot; counted amounts and never-pumped
    things are assumed to be added by hand."""
    needs = _pumped_needs(session)
    start = tuple(dict.fromkeys(keep or []))[:pumps]
    candidates = sorted(set().union(*needs.values())) if needs else []

    def score(chosen: frozenset) -> tuple[int, int]:
        done = sum(1 for n in needs.values() if n <= chosen)
        near = sum(len(n & chosen) for n in needs.values() if len(n - chosen) <= 1)
        return done, near

    layer = {frozenset(start): start}
    for _ in range(len(start), pumps):
        grown: dict[frozenset, tuple] = {}
        for chosen, order in layer.items():
            for c in candidates:
                if c not in chosen:
                    grown.setdefault(chosen | {c}, order + (c,))
        if not grown:
            break
        layer = dict(sorted(grown.items(), key=lambda kv: score(kv[0]), reverse=True)[:beam])
    best = max(layer, key=score)
    drinks = [session.get(Drink, d) for d, n in needs.items() if n <= best]
    return [session.get(Ingredient, i) for i in layer[best]], sorted(drinks, key=sort_key)
