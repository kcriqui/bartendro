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
    """What the strength / tartness buttons adjust (old booze.type 0-3)."""
    OTHER = "other"
    ALCOHOL = "alcohol"  # scaled by "strength"
    TART = "tart"        # scaled up by "tartness"
    SWEET = "sweet"      # scaled down by "tartness"


class Level(str, enum.Enum):
    """Last liquid level reading of a dispenser (old dispenser.out: 0 out, 1 ok, 2 low)."""
    UNKNOWN = "unknown"
    OK = "ok"
    LOW = "low"
    OUT = "out"


class Ingredient(Base):
    """Anything that goes in a drink: a generic ingredient ("Vodka") or a specific bottle
    ("Tito's", generic_id -> Vodka). A recipe may ask for either; a generic ingredient is
    available when any of its specific ones is on a dispenser (old booze groups)."""
    __tablename__ = "ingredient"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    brand: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    abv: Mapped[float] = mapped_column(Float, default=0.0)
    kind: Mapped[Kind] = mapped_column(_enum(Kind), default=Kind.OTHER)
    manual: Mapped[bool] = mapped_column(Boolean, default=False)  # added by hand, never pumped
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

    items: Mapped[list[RecipeItem]] = relationship(back_populates="drink", cascade="all, delete-orphan",
                                                   order_by="RecipeItem.position")

    def __repr__(self) -> str:
        return f"<Drink {self.id} {self.name!r}>"


class RecipeItem(Base):
    __tablename__ = "recipe_item"
    __table_args__ = (UniqueConstraint("drink_id", "ingredient_id"),
                      CheckConstraint("parts > 0", name="parts_positive"))

    id: Mapped[int] = mapped_column(primary_key=True)
    drink_id: Mapped[int] = mapped_column(ForeignKey("drink.id", ondelete="CASCADE"), index=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredient.id"))
    parts: Mapped[float] = mapped_column(Float)
    position: Mapped[int] = mapped_column(Integer, default=0)

    drink: Mapped[Drink] = relationship(back_populates="items")
    ingredient: Mapped[Ingredient] = relationship()


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
