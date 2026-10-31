# Recipes: rules and data

## The bundled recipe file

`src/bartendro/data/classics.toml` - 62 drinks (7 non-alcoholic) written for this project
(2026-10-01). Amounts are facts from classic / IBA specs; no copied text. Not bundled on purpose:
the GitHub IBA datasets (scrapes of IBA's site + Wikipedia) and TheCocktailDB (may not be
redistributed). Load it with `bartendro-db load-recipes` or Admin > Drinks.

The loader matches existing ingredients by name or alias, links brands (Kahlua -> Coffee Liqueur)
and `generic` children, and matches drinks by name or alias (old names stay as aliases, so loading
into an older database doesn't duplicate). Classic drinks pour their recipe total (`size_ml`).
"One step away" (load a bottle / get something on hand) and `suggest N` (beam-search bottle
planner) help choose what to load.

## Kevin's rules

- **The bot only pours liquids.** The guest does the rest by hand, before the pour (absinthe rinse,
  muddled mint / lime, ice) or after (dashes of bitters, shake / stir per `finish`).
- **Ice** is a before-the-pour checklist line (`[1, "fill", "Ice", "before"]`), never "Over ice"
  text; Ice starts on hand. Sazerac exists neat and "on the Rocks".
- **Pumped or by hand is decided at pour time** (`menu.resolve_line`): pumped if the ingredient is
  on a dispenser and the amount can be pumped (counted units convert via `PUMP_ML`: dash 0.9 ml,
  drop 0.05, barspoon 5, tsp 5, splash 7), else by hand if on hand. Bitters, absinthe and half and
  half can be either; `Ingredient.manual` = never pumped (ice, mint, wedges). Pumped amounts under
  option `min_pump_ml` (default 1) go by hand if on hand. `step` = before / after; a pumped
  "before" line (absinthe rinse) pours in two stages with a Continue in between.
- **On hand is tracked like the pumps**: `Ingredient.on_hand` (Admin > Dispensers > On hand). Drinks
  only show when their pumped bottles are loaded AND their by-hand items are on hand.
- **Specific spirits stay specific**: a recipe asking for Tequila, Reposado needs reposado (plain
  Tequila won't do); a reposado bottle can make generic-Tequila drinks.
- **Booze vs mixer**: `Ingredient.alcoholic`; the strength button scales booze only.
- **Menu section**: `Drink.category` override, else the main spirit's top-level generic
  (`menu.category_of`).

## Names

Kevin's convention: base first, kind after a comma - "Tequila, Anejo", "Whiskey, Rye",
"Margarita, Tommy's", "Bitters, Angostura" - so variants sort together (as in his old bot
databases). Juices / syrups / purees keep the "Lime Juice, Persian" style; liqueurs aren't renamed.
Bundled files keep the old names as aliases. Guests see them the usual way round ("Tommy's
Margarita", "Anejo Tequila"; a lower-case qualifier stays after: "Sazerac on the Rocks"); admin
pages and the database keep the real names, and menus sort by the real name.

Old databases rename drinks ("Margarita, SND"; "zzz ..." = retired; "z(na) " = non-alcoholic), so
match old drinks on description / recipe, not just name.

Accented names (Jägermeister, Piña Colada, Strawberry Purée) are fine UTF-8 in the old and new dbs;
"�" only appears when a script prints to the Windows console. Check the bytes
(`text_factory = bytes`) before "fixing" text, or set `PYTHONIOENCODING=utf-8`.

## Small pours

Brainstorm only (2026-10-02, also in [plans/](plans/2026-10-02-pump-or-hand-categories-parties.md)):
independent size and strength-in-standard-drinks controls, sub-linear mixer scaling, a minimum pour
floor, per-pump calibration offset (ticks = a*ml + b). Needs real pumps and a scale to tune.
