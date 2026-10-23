"""The showcase setup used by the hosted demo bot (bartendro-web --showcase): the bundled
recipes, 15 bottles on the pumps, the usual by-hand items on hand and an inactive demo party."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db import open_db, recipes
from .db.models import Dispenser, Drink, Ingredient, Party, PartyDrink

BOTTLES = ["Vodka", "Tequila", "Rum, White", "Gin", "Whiskey, Rye", "Triple Sec", "Coffee Liqueur",
           "Campari", "Vermouth, Sweet", "Lime Juice", "Lemon Juice", "Simple Syrup", "Orange Juice",
           "Cranberry Juice", "Ginger Beer"]
ON_HAND = ["Ice", "Bitters, Angostura", "Bitters, Peychaud's", "Absinthe", "Half and Half", "Mint",
           "Lime", "Sugar", "Ice, Crushed", "Cola", "Tonic Water", "Soda Water"]
PARTY_DRINKS = ["Margarita", "Moscow Mule", "Cosmopolitan", "Negroni", "Screwdriver", "Cape Cod",
                "Long Island Iced Tea", "Sazerac", "Mojito", "Shirley Temple"]


def build(path: str | Path) -> sessionmaker[Session]:
    """(Re)create the showcase database at `path` and return a session factory for it."""
    path = Path(path)
    path.unlink(missing_ok=True)
    Session = open_db(path)
    with Session() as s:
        recipes.load(s)
        ing = {i.name: i for i in s.scalars(select(Ingredient))}
        for n, name in enumerate(BOTTLES, 1):
            s.add(Dispenser(number=n, ingredient=ing[name]))
        for name in ON_HAND:
            ing[name].on_hand = True
        drinks = {d.name: d for d in s.scalars(select(Drink))}
        party = Party(name="Halloween (demo)", title="Spooky Bar", welcome="Boo! Pick a potion.",
                      color_page="#1c1c24", color_frame="#6a2c91", color_heading="#d35400",
                      color_button="#8e44ad", color_go="#e67e22", active=False)
        party.drinks = [PartyDrink(drink=drinks[n], featured=i < 4, position=i)
                        for i, n in enumerate(PARTY_DRINKS)]
        s.add(party)
        s.commit()
    return Session
