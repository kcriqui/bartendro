"""Database tables (SQLAlchemy 2). Replaces the old ui/bartendro/model/* tables; see
importer.py for how old bartendro.db rows map onto these.

Recipes are stored in *parts*, like the old app: at pour time the parts are scaled to the
glass size (option `drink_size`), after the strength/tartness adjustment by ingredient kind.
Schema changes go through Alembic (db/migrations) - never edit a released migration.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (Boolean, CheckConstraint, DateTime, Enum, Float, ForeignKey, Integer,
                        MetaData, String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Stable constraint names so Alembic migrations (batch mode on SQLite) can find them.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def _enum(cls):
    # store the readable values ("ok"), not the member names ("OK")
    return Enum(cls, native_enum=False, length=10, validate_strings=True,
                values_callable=lambda e: [m.value for m in e])


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # SQLite has no time zones: store UTC


class Kind(str, enum.Enum):
    """What the tartness button adjusts (old booze.type 2-3). Strength goes by
    Ingredient.alcoholic (old type 1 "alcohol" was folded into that in migration 0005)."""
    OTHER = "other"
    TART = "tart"        # scaled up by "tartness"
    SWEET = "sweet"      # scaled down by "tartness"


class Level(str, enum.Enum):
    """Last liquid level reading of a dispenser (old dispenser.out: 0 out, 1 ok, 2 low)."""
    UNKNOWN = "unknown"
    OK = "ok"
    LOW = "low"
    OUT = "out"


class Ingredient(Base):
    """Anything that goes in a drink: a generic ingredient ("Vodka") or a specific one
    ("Tito's" or "Reposado Tequila", generic_id -> its generic). A recipe asking for a generic
    ingredient can use any of its specific ones (old booze groups); a recipe asking for a
    specific one needs exactly that (a Reposado drink is never made with plain Tequila).
    `alcoholic`: booze (scaled by the strength button) vs. mixer. `manual`: can never go on a
    pump (ice, mint, lime wedges). `on_hand`: the guest can add it by hand right now. Bitters,
    absinthe or half and half can be either: pumped if on a dispenser, else added by hand."""
    __tablename__ = "ingredient"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    brand: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    abv: Mapped[float] = mapped_column(Float, default=0.0)
    kind: Mapped[Kind] = mapped_column(_enum(Kind), default=Kind.OTHER)
    alcoholic: Mapped[bool] = mapped_column(Boolean, default=False)  # booze, not a mixer
    manual: Mapped[bool] = mapped_column(Boolean, default=False)  # can never be pumped
    on_hand: Mapped[bool] = mapped_column(Boolean, default=False)  # available to add by hand
    generic_id: Mapped[int | None] = mapped_column(ForeignKey("ingredient.id"))
    generic_order: Mapped[int] = mapped_column(Integer, default=0)  # sort order within its generic

    generic: Mapped[Ingredient | None] = relationship(remote_side=[id], back_populates="specifics")
    specifics: Mapped[list[Ingredient]] = relationship(back_populates="generic",
                                                       order_by="Ingredient.generic_order")

    def __repr__(self) -> str:
        return f"<Ingredient {self.id} {self.name!r}>"


class Drink(Base):
    __tablename__ = "drink"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    sort_name: Mapped[str] = mapped_column(String(100), default="")  # empty: sort by name
    description: Mapped[str] = mapped_column(Text, default="")
    popular: Mapped[bool] = mapped_column(Boolean, default=False)  # old "the essentials" section
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)   # listed on the menu (old "available")
    size_ml: Mapped[int | None] = mapped_column(Integer)  # per-drink glass size; None = option drink_size
    instructions: Mapped[str] = mapped_column(Text, default="")  # how it's served, e.g. "Over ice."
    finish: Mapped[str] = mapped_column(Text, default="")  # what the guest does after the pour, e.g. "Shake with ice and strain."
    glass: Mapped[str] = mapped_column(String(50), default="")
    source: Mapped[str] = mapped_column(String(50), default="")  # "legacy" (old db), "classics" (bundled), ""
    category: Mapped[str] = mapped_column(String(50), default="")  # menu section override; "" = automatic

    items: Mapped[list[RecipeItem]] = relationship(back_populates="drink", cascade="all, delete-orphan",
                                                   order_by="RecipeItem.position")

    def __repr__(self) -> str:
        return f"<Drink {self.id} {self.name!r}>"


class RecipeItem(Base):
    __tablename__ = "recipe_item"
    """One ingredient of a drink: its share of the mix (`parts`; recipes from the bundled file
    use ml as parts and keep the amount as written in `amount` + `unit`, e.g. 2 oz).

    Counted amounts (parts None: "2 dash", "6 leaf") aren't part of the mix. Whether a line is
    pumped or added by hand is decided when the drink is made (menu.resolve_line): pumped if
    its ingredient is on a dispenser and the amount can be pumped (`pumpable`), else by hand if
    on hand. `step` says when a by-hand line goes in: "before" the pour (absinthe rinse,
    muddled mint, ice) or "after" (bitters, cream)."""
    __tablename__ = "recipe_item"
    __table_args__ = (UniqueConstraint("drink_id", "ingredient_id"),
                      CheckConstraint("parts IS NULL OR parts > 0", name="parts_positive"))

    id: Mapped[int] = mapped_column(primary_key=True)
    drink_id: Mapped[int] = mapped_column(ForeignKey("drink.id", ondelete="CASCADE"), index=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredient.id"))
    parts: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(20), default="")
    step: Mapped[str] = mapped_column(String(10), default="after")  # by hand: "before" / "after" the pour
    position: Mapped[int] = mapped_column(Integer, default=0)

    drink: Mapped[Drink] = relationship(back_populates="items")
    ingredient: Mapped[Ingredient] = relationship()

    @property
    def pumpable(self) -> bool:
        """Could the pumps pour this line? Not for never-pumped ingredients (ice, mint) or
        units without a volume (leaf, wedge, fill)."""
        return not self.ingredient.manual and (self.parts is not None or self.unit in PUMP_ML)

    @property
    def by_hand(self) -> bool:
        """Always added by the guest, whatever is on the pumps."""
        return not self.pumpable

    def pump_ml(self, mix_ml: float | None = None) -> float:
        """ml to pump: its share of the mix (`mix_ml`, from scale_recipe) or a counted amount
        converted with PUMP_ML (2 dash = 1.8 ml; not scaled with the glass)."""
        if self.parts is not None:
            return mix_ml or 0.0
        return (self.amount or 0) * PUMP_ML[self.unit]

    @property
    def hand_text(self) -> str:
        """"3 dashes", "1 barspoon" - how much to add by hand."""
        return amount_text(self.amount, self.unit)


# Counted amounts, always added by hand (singular: plural)
HAND_UNITS = {"dash": "dashes", "drop": "drops", "barspoon": "barspoons", "pinch": "pinches",
              "splash": "splashes", "tsp": "tsp", "leaf": "leaves", "sprig": "sprigs",
              "wedge": "wedges", "slice": "slices", "cube": "cubes", "piece": "pieces",
              "fill": "fill"}  # "fill" (ice): "fill the glass with"
STEPS = ("before", "after")
# ml per counted unit when the ingredient is on a pump (bitters / absinthe on a dispenser)
PUMP_ML = {"dash": 0.9, "drop": 0.05, "barspoon": 5.0, "tsp": 5.0, "splash": 7.0}


def amount_text(amount: float | None, unit: str) -> str:
    if unit == "fill":
        return "fill the glass with"
    if amount is None:
        return unit
    n = f"{amount:g}"
    return f"{n} {unit if amount == 1 else HAND_UNITS.get(unit, unit)}"


class Dispenser(Base):
    """Dispenser #number (1-based, the order the driver found them in) and what's in it."""
    __tablename__ = "dispenser"
    __table_args__ = (CheckConstraint("number BETWEEN 1 AND 15", name="number_range"),)

    number: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    ingredient_id: Mapped[int | None] = mapped_column(ForeignKey("ingredient.id"))  # None = empty
    level: Mapped[Level] = mapped_column(_enum(Level), default=Level.UNKNOWN)
    ticks_per_ml: Mapped[float | None] = mapped_column(Float)  # calibration; None = driver default

    ingredient: Mapped[Ingredient | None] = relationship()


class Option(Base):
    """Settings as text; typed defaults and conversion live in options.py."""
    __tablename__ = "option"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class PourLog(Base):
    """One row per drink or shot poured (old drink_log + shot_log)."""
    __tablename__ = "pour_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    time: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    drink_id: Mapped[int | None] = mapped_column(ForeignKey("drink.id", ondelete="SET NULL"))
    ingredient_id: Mapped[int | None] = mapped_column(ForeignKey("ingredient.id"))  # shots
    size_ml: Mapped[float] = mapped_column(Float)


class Party(Base):
    """A party's look and drink list. At most one is active; with a drink list, guests only see
    those drinks (still only the ones that can be made). Colours are "#rrggbb" or "" for the
    original Bartendro look."""
    __tablename__ = "party"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(100), default="")    # shown in the header
    welcome: Mapped[str] = mapped_column(Text, default="")         # shown above the essentials
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    color_page: Mapped[str] = mapped_column(String(7), default="")
    color_frame: Mapped[str] = mapped_column(String(7), default="")
    color_heading: Mapped[str] = mapped_column(String(7), default="")
    color_button: Mapped[str] = mapped_column(String(7), default="")
    color_go: Mapped[str] = mapped_column(String(7), default="")
    logo: Mapped[str] = mapped_column(String(100), default="")     # file name in the uploads folder

    drinks: Mapped[list[PartyDrink]] = relationship(back_populates="party", cascade="all, delete-orphan",
                                                    order_by="PartyDrink.position")


class PartyDrink(Base):
    __tablename__ = "party_drink"
    __table_args__ = (UniqueConstraint("party_id", "drink_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("party.id", ondelete="CASCADE"), index=True)
    drink_id: Mapped[int] = mapped_column(ForeignKey("drink.id", ondelete="CASCADE"))
    featured: Mapped[bool] = mapped_column(Boolean, default=False)  # in "the essentials"
    position: Mapped[int] = mapped_column(Integer, default=0)

    party: Mapped[Party] = relationship(back_populates="drinks")
    drink: Mapped[Drink] = relationship()
