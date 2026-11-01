# Plan: pump-or-hand ingredients, category menus, original look, party themes & lists, easier admin

## Context
Kevin wants the guest UI and admin to be practical for real parties:
- bitters / absinthe / half and half may sit on a pump *or* be added by hand (today `manual` = never pumped);
- a short "essentials" block (2 rows) then category buttons (Whiskey, Tequila, Vodka, ...,
  plus a new Non-alcoholic list) that open sub-menus;
- the original Bartendro look (colours, framed panel, logo/robot images) as the default theme;
- per-party customisation: theme (title, colours, logo) and a party-specific drink list;
- recipes can be switched off even when makeable; easy add/edit/delete of recipes and of
  which bottle is on which pump (keyboard is fine for admin);
- ingredients classified as booze vs mixer;
- small pours: brainstorm only (no implementation this round).

Decisions (asked): categories automatic from the main spirit with per-drink override; party
lists hide everything else; original look is the default theme, parties can restyle it.

## Existing pieces to reuse
- `bot.plan_drink` + `HandStep` / `Plan.before/after` (src/bartendro/bot.py) - extend, don't replace.
- `menu.pumped_and_on_hand`, `missing_for`, `makeable_drinks`, `_with_generics` (src/bartendro/db/menu.py).
- `recipes.parse_amount`, `UNITS_ML`, `HAND_UNITS`, loader + classics.toml (src/bartendro/db/recipes.py).
- `Drink.enabled` already = "switched off even if makeable"; keep, just surface it better.
- Original assets: `ui/content/static/images/{bartendro-logo.png, partyrobot.png, sick_party_robot.png, icon.ico}`
  and colours from `ui/content/static/css/bartendro.css` (grey `#DADADA` page, tan `#D9A180` 12px
  rounded frame, `#B98160` inner border on white, headings/drink bar `#005991`, button border
  `#4788BF`, orange drink buttons `#fc9c64→#fa6c19`, green pour/shot buttons `#62c462→#51a351`,
  Verdana). GPL - same licence as us, copy into `src/bartendro/web/static/img/`.

## 1. Pump-or-hand ingredients (core change)
- Meaning of `Ingredient.manual` narrows to "can never be on a pump" (ice, mint, lime wedges,
  sugar). Bitters, absinthe, half and half, cream become normal ingredients (manual=false);
  migration 0005 flips them for existing rows by name; classics.toml updated.
- Each recipe line keeps amount+unit+step. At plan time (`bot.plan_drink`) every line is
  resolved: **pumped** if its ingredient (or a brand of it) is on a dispenser and the amount
  converts to ml; else **by hand** if on hand; else the drink can't be made.
- Counted units get an ml equivalent for pumping: dash 0.9, drop 0.05, barspoon 5, tsp 5,
  splash 7 (constants next to `HAND_UNITS`); leaf/wedge/slice/sprig/cube/piece/fill never pump.
  Pumped dashes are always at half speed; below a per-pump minimum (option, default 1 ml) fall
  back to by-hand if on hand.
- "before" lines that end up pumped (absinthe rinse from a pump) => two-stage pour: stage 1
  pours the before-lines, checklist ("swirl and discard"), stage 2 the rest. `Plan` gets
  `stages: list[dict[int, float]]`; `Bot._pour` loops stages, emitting a `"stage_done"` event
  and waiting for the UI's "continue" (`POST /api/continue`); timeout cancels.
- `menu.missing_for` / `makeable_drinks` / `one_bottle_away` use the same resolver so the menu
  matches what the bot will do (one helper `resolve_line(item, pumped, on_hand)` in menu.py,
  used by bot too).

## 2. Booze vs mixer, categories, non-alcoholic list
- Migration 0005: `Ingredient.alcoholic` (bool, = abv > 0 initially; editable) - replaces
  `Kind.ALCOHOL` for the strength button (Kind keeps tart/sweet/other; ALCOHOL rows -> other,
  alcoholic=true). Fixes old-db bottles like Kahlua typed "unknown".
- Category of a drink = `Drink.category` override, else auto: the line with most ethanol
  (parts x abv) -> walk up generics to a top-level spirit (Reposado -> Tequila, Rye -> Whiskey,
  Cognac -> Brandy); liqueur-only drinks -> "Liqueurs"; no alcohol -> "Non-alcoholic".
  `menu.category_of(drink)`; category list = top-level alcoholic generics that have drinks.
- classics.toml: ~10 non-alcoholic drinks from existing mixers (Shirley Temple, Roy Rogers,
  Virgin Mary, Cinderella, Nojito, Arnold Palmer needs iced tea + lemonade -> new mixers,
  cranberry spritzer, ...). Test rule: no alcoholic ingredient in them.

## 3. Guest UI: essentials + category menus, original look
- Menu page: header (logo / party title), "essentials" = first 2 rows of featured drinks
  (party featured list, else `Drink.popular`), then a grid of category buttons with counts;
  `/menu/<category>` lists that category's drinks (two-column orange button grid like the
  original `drink_table`). Non-alcoholic is its own category button.
- Restyle style.css to the original palette and framed panel; orange drink buttons, green
  "Pour" / shot buttons, blue headings/drink bar; logo in header (party robot on phones),
  sick robot on error/hard-out screens. Keep our phone/10" responsive rules.
- `scripts/render_old_ui.py`: renders the original templates (ui/content/templates/index,
  drink/index, shots, layout) with Jinja2 + sample data from bartendro.db.default to
  `build/old-ui/*.html` with ui/content/static copied alongside - for side-by-side screenshots.
  (Needs: bare template names via FileSystemLoader, `options` as 0/1 values, stub
  `current_user.is_authenticated()`.)

## 4. Parties: theme + drink list
- New tables (migration 0006): `party` (id, name, title, subtitle, colours: accent/button/
  heading/background as hex, logo filename, active bool) and `party_drink` (party_id, drink_id,
  featured bool, position).
- Option `active_party` (or `party.active`, at most one). Menu: if a party is active and has a
  list -> only listed drinks (still must be enabled + makeable); featured -> essentials.
  Theme: base.html emits `:root{--accent:...}` overrides from the party; logo upload stored in
  `<data dir>/uploads/` and served at `/uploads/`.
- Admin > Parties: list / new / duplicate / delete / activate; editor with colour pickers,
  title, logo upload, and a drink picker grouped by category (checkbox = on the list,
  star = featured, drag or up/down for order). "Preview" opens the menu with `?party=<id>`.

## 5. Admin usability
- Drinks list: filter box, category column, clear states (on menu / switched off / can't make:
  missing X), one-click enable toggle (exists), Duplicate, Delete.
- Drink editor: rows added/removed with JS, ingredient field = text input + `<datalist>`
  (type to search, keyboard friendly), separate amount / unit `<select>` / step `<select>`
  columns instead of the "1 dash before" text trick (parse_amount stays for the API), category
  override select, live preview (calls `/api/drink/<id>/plan`-style preview endpoint with the
  unsaved form) showing ml per line, total, ABV and standard drinks.
- Ingredient editor: alcoholic checkbox, "never pumped" checkbox, on hand.
- Bottles on pumps: dispensers page becomes one card per pump with a searchable ingredient
  field and a "Change bottle" flow: 1) reverse-run to empty the line back (button), 2) pick
  the new bottle, 3) prime (button), 4) optional test pour; shows "this pump makes N drinks";
  plus "Empty all lines" / "Prime all" helpers. Same page usable on the touchscreen.

## 6. Small pours - brainstorm only (no code this round)
Problem: shrinking a drink scales every line by the same factor, so small drinks have
sub-accurate pump amounts (a few ml of lime) and the same strength; Kevin wants smaller/lighter
drinks to lose alcohol faster than mixers.
Ideas to discuss / prototype later (all build on `scale_recipe` and the new `alcoholic` flag):
1. **Two independent controls: size and strength (standard drinks).** Compute ethanol
   = sum(ml x abv); strength sets target ethanol (e.g. 0.5 / 1 / 1.5 / 2 standard drinks =
   14 g ethanol each); booze lines scale to hit it, mixers fill the remaining volume in their
   recipe ratio. Spirit-only drinks (Negroni) can only get smaller, not weaker.
2. **Sub-linear mixer scaling:** for size factor f < 1, booze x f, mixers x f^k (k ~ 0.5),
   then renormalise to the glass - smaller drinks automatically lighter.
3. **Minimum pour floor per line** (pump accuracy, e.g. 3-5 ml): lines below it are rounded
   up (mixers) or dropped/moved to by-hand (dashes), the rest rescaled; warn on the drink page.
4. **Pump calibration with an offset:** ticks = a*ml + b (line slack / spin-up), measured at
   ~10 ml and ~60 ml per pump; makes small pours accurate regardless of the scaling rule.
5. **"Light" preset per party/drink:** cap ethanol per drink (e.g. 1 standard drink) and top
   up with the drink's main mixer.
6. Show ABV and standard drinks on the drink page so the effect of the controls is visible.
Recommend: prototype 4 (accuracy) + 1 (controls) first, tune with real pumps and a scale.

## Order of work (each step: tests + commit)
1. Migration 0005 (alcoholic, manual meaning, Drink.category) + resolver (section 1 core,
   without two-stage pours) + categories (2).
2. Original look + menu with essentials and category pages (3) + old-UI render script.
3. Parties (4).
4. Admin usability (5).
5. Two-stage pours for pumped "before" lines (1, last part).
6. Non-alcoholic drinks in classics.toml.

## Critical files
- src/bartendro/db/models.py, db/migrations/versions/0005_*.py, 0006_*.py
- src/bartendro/db/menu.py (resolver, categories), db/recipes.py, data/classics.toml
- src/bartendro/bot.py (resolution, stages)
- src/bartendro/web/__init__.py (+ split into routes modules if it grows: guest / admin / parties)
- src/bartendro/web/templates/{base,menu,category,drink}.html, admin/{dispensers,drink,drinks,parties,party}.html
- src/bartendro/web/static/{style.css,app.js,img/*}
- scripts/render_old_ui.py

## Verification
- pytest (existing 84 + new): resolver (bitters pumped vs by hand vs missing; dash->ml;
  min-pour fallback), categories (auto + override, Reposado->Tequila, non-alcoholic), party
  filtering/featured/theme CSS, migrations on a populated db (`test_upgrade_keeps_data` extended),
  admin form posts (new editor fields, party editor, change-bottle endpoints), two-stage pour
  events with the simulator.
- `bartendro-web --sim 15 --db demo.db` in the browser pane at phone (375x812) and 10" (1280x800):
  menu -> category -> drink -> pour; party theme switch; screenshots next to
  `scripts/render_old_ui.py` output of the original pages.
- Recipe editor round-trip with keyboard only (tab/type/enter).
