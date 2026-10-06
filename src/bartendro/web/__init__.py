"""FastAPI web app: drink menu, shots and admin pages, a JSON API the pages call, and a
WebSocket that pushes the bot's status and pour events to every open screen (the bot's own
touchscreen and phones alike). No login: whoever is on the bot's WiFi can use everything.

create_app() takes a ready Bot, so tests and the server share the same code; server.py opens
the hardware (or the simulator) and runs it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import secrets
import time
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from starlette.datastructures import UploadFile

from .. import __version__
from ..bot import Bot, BusyError, CantPourError
from ..db import generation, options, recipes
from ..db.menu import (
    by_category,
    can_make,
    categories_of,
    category_of,
    display_name,
    makeable_drinks,
    missing_for,
    one_bottle_away,
    pumped_and_on_hand,
    resolve_line,
    scale_recipe,
    sort_key,
    strength_of,
    suggest_bottles,
    uses,
)
from ..db.models import Dispenser, Drink, Ingredient, Kind, Party, PartyDrink, PourLog, RecipeItem, utcnow
from .theme import DEFAULTS as THEME_DEFAULTS
from .theme import theme_css
from .theme import valid as valid_color

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
ML_PER_OZ = 29.57  # the old UI used 30
SIZE_STEP_ML = 30  # drink size +/- buttons (old size_increment)
PREVIEW_COOKIE = "preview_party"
LOGO_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
MAX_LOGO_BYTES = 2_000_000
ESSENTIALS = 4     # drinks in "the essentials": two rows of two
ROBOTS = {"": "Bartendro party robot", "bar2d2": "Bar2D2 (astromech dome on a Dalek)"}  # Party.robot
MARQUEE_MAX = 200
MENU_CACHE_SECONDS = 60  # also catch changes made by another process (bartendro-db)


def guest_event(event: dict) -> dict:
    """A status or pour event with names as guests see them (display_name): what's being poured
    and the by-hand checklist ([ingredient, ml, text] lists)."""
    event = dict(event)
    for key in ("name", "pouring"):
        if isinstance(event.get(key), str):
            event[key] = display_name(event[key])
    for key in ("before", "after"):
        if isinstance(event.get(key), list):
            event[key] = [[display_name(h[0]), *h[1:]] if isinstance(h, list) and h else h for h in event[key]]
    return event


def _access_logger(path: Path | None):
    """record(ip, method, request, status, start) writing JSON lines to `path`, or None."""
    if path is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5,
                                                       encoding="utf-8")
    except OSError as e:
        log.warning("access log %s: %s - not logging requests", path, e)
        return None
    logger = logging.getLogger("bartendro.access")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    def record(ip: str, method: str, conn, status: int, start: float) -> None:
        url = conn.url
        logger.info(json.dumps({
            "t": datetime.now(UTC).isoformat(timespec="seconds"), "ip": ip, "m": method,
            "path": (url.path + ("?" + url.query if url.query else ""))[:300], "status": status,
            "ms": round((time.monotonic() - start) * 1000), "ua": conn.headers.get("user-agent", "")[:200]}))
    return record


def slugify(name: str) -> str:
    return "-".join("".join(c if c.isalnum() else " " for c in name.lower()).split())


class MakeRequest(BaseModel):
    size_ml: float | None = None
    strength: int = 0
    tartness: int = 0


class AmountRequest(BaseModel):
    ml: float | None = None


class RunRequest(BaseModel):
    ms: int = Field(2000, gt=0)
    reverse: bool = False


class CleanRequest(BaseModel):
    which: str = "all"


class PumpRequest(BaseModel):
    ingredient: str = ""            # name; "" = empty
    ticks_per_ml: float | None = None


class RunAllRequest(BaseModel):
    ms: int = Field(2000, gt=0)
    reverse: bool = False


class PreviewRow(BaseModel):
    ingredient: str
    amount: str = ""
    unit: str = "parts"
    step: str = "after"


class PreviewRequest(BaseModel):
    size_ml: float | None = None
    rows: list[PreviewRow] = []


def create_app(bot: Bot, bot_name: str = "Bartendro", uploads: Path | None = None,
               banner: str = "", allow_uploads: bool = True, reload_templates: bool = False,
               access_log: Path | None = None) -> FastAPI:
    """`uploads`: folder for party logos (default: "uploads" next to the database).
    `banner`: a line shown on every page (the hosted demo bot: "simulated pumps").
    `allow_uploads=False`: no logo uploads (a copy that's open to the internet).
    `reload_templates`: pick up template edits without a restart (development; otherwise every
    request checks every template file for changes - slow, especially on a network share).
    `access_log`: write one JSON line per request / WebSocket to this file (the hosted demo:
    scripts/check_demo_log.py reads it to spot trouble). Rotated at 5 MB, 5 old files kept."""
    sessions = bot.sessions
    if uploads is None:
        db_file = sessions.kw["bind"].url.database
        uploads = Path(db_file).resolve().parent / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    clients: set[asyncio.Queue] = set()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()

        def from_bot(event: dict) -> None:  # called from the bot's worker threads
            loop.call_soon_threadsafe(_broadcast, event)

        def _broadcast(event: dict) -> None:
            event = guest_event(event)
            for q in list(clients):
                q.put_nowait(event)

        bot.subscribe(from_bot)
        yield
        bot.unsubscribe(from_bot)

    app = FastAPI(title="Bartendro", version=__version__, lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1000)  # pages to phones over the bot's WiFi
    record = _access_logger(access_log)

    if record is not None:
        @app.middleware("http")
        async def log_requests(request: Request, call_next):
            start = time.monotonic()
            response = await call_next(request)
            ip = request.client.host if request.client else ""
            if not (ip == "127.0.0.1" and request.url.path == "/api/status"):  # the health check
                record(ip, request.method, request, response.status_code, start)
            return response
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.mount("/uploads", StaticFiles(directory=uploads), name="uploads")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.auto_reload = reload_templates
    templates.env.filters["guest"] = display_name  # "Margarita, Pineapple" -> "Pineapple Margarita"

    def current_party(request: Request, s) -> Party | None:
        """The party being previewed (?party=<id>, remembered in a cookie; 0 = none), else the
        active one."""
        preview = request.query_params.get("party", request.cookies.get(PREVIEW_COOKIE))
        if preview is not None and preview.isdigit():
            return s.get(Party, int(preview)) if int(preview) else None
        return s.scalar(select(Party).where(Party.active))

    def page(request: Request, name: str, **ctx):
        with sessions() as s:
            opts = options.get_all(s)
            party = current_party(request, s)
            metric = opts["metric"]

            def amount(ml: float) -> str:
                return f"{ml:.0f} ml" if metric else f"{ml / ML_PER_OZ:.1f} oz"
            path = request.url.path
            nav = "admin" if path.startswith("/admin") else "shots" if path.startswith("/shots") else "drinks"
            response = templates.TemplateResponse(request, name, {
                "bot_name": bot_name, "status": guest_event(bot.status()), "opts": opts, "amount": amount,
                "version": __version__, "nav": nav, "party": party, "theme_css": theme_css(party),
                "banner": banner, "allow_uploads": allow_uploads, **ctx})
        preview = request.query_params.get("party")
        if preview is not None and preview.isdigit():
            if int(preview):
                response.set_cookie(PREVIEW_COOKIE, preview, max_age=3600)
            else:
                response.delete_cookie(PREVIEW_COOKIE)
        return response

    menu_cache: dict = {"key": None}

    def makeable_now(s) -> list[Drink]:
        """makeable_drinks, worked out again only after a database change (db.generation) or
        MENU_CACHE_SECONDS: it reads every drink and is most of a menu page's time. Everything
        the guest pages read is loaded here, so the drinks still work once `s` is closed."""
        key = (generation(), bot.dispenser_count)
        if menu_cache["key"] != key or time.monotonic() - menu_cache["at"] > MENU_CACHE_SECONDS:
            drinks = makeable_drinks(s, dispenser_count=bot.dispenser_count)
            for d in drinks:
                categories_of(d)
                for item in d.items:
                    item.ingredient.name  # noqa: B018 - loads it now, while the session is open
            menu_cache.update(key=key, at=time.monotonic(), drinks=drinks)
        return list(menu_cache["drinks"])

    def guest_drinks(s, request: Request) -> tuple[list[Drink], list[Drink]]:
        """(drinks guests can order now, the featured ones for "the essentials"). With a party
        drink list: only those, featured = the party's picks in its order."""
        drinks = makeable_now(s)
        party = current_party(request, s)
        if party is not None and party.drinks:
            listed = {pd.drink_id: pd for pd in party.drinks}
            drinks = [d for d in drinks if d.id in listed]
            featured = sorted((d for d in drinks if listed[d.id].featured), key=lambda d: listed[d.id].position)
            return drinks, featured[:ESSENTIALS]
        return drinks, [d for d in drinks if d.popular][:ESSENTIALS]

    # ------------------------------------------------------------------ errors

    @app.exception_handler(BusyError)
    async def busy(_request, exc):
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(CantPourError)
    async def cant_pour(_request, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

    # ------------------------------------------------------------------ pages

    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots():
        """Keep search engines and AI crawlers out: the hosted demo is public (they crawled its
        admin pages), and a bot has no business being indexed anyway."""
        return "User-agent: *\nDisallow: /\n"

    @app.get("/")
    def menu(request: Request):
        with sessions() as s:
            drinks, essentials = guest_drinks(s, request)
            sections = [(name, slugify(name), len(ds)) for name, ds in by_category(drinks).items()]
            # "All drinks" counts each drink once even if it's in several sections
            return page(request, "menu.html", essentials=essentials, sections=sections, total=len(drinks))

    @app.get("/menu/{slug}")
    def menu_section(request: Request, slug: str):
        with sessions() as s:
            drinks, _ = guest_drinks(s, request)
            if slug == "all":
                title, chosen = "All drinks", sorted(drinks, key=sort_key)
            else:
                sections = by_category(drinks)
                title = next((n for n in sections if slugify(n) == slug), None)
                if title is None:
                    return RedirectResponse("/", status_code=303)
                chosen = sections[title]
            return page(request, "category.html", title=title, drinks=chosen)

    @app.get("/drink/{drink_id}")
    def drink_page(request: Request, drink_id: int):
        with sessions() as s:
            drink = s.get(Drink, drink_id)
            if drink is None:
                return RedirectResponse("/", status_code=303)
            size = drink.size_ml or int(options.get(s, "drink_size"))
            return page(request, "drink.html", drink=drink, size=size, size_step=SIZE_STEP_ML,
                        max_size=size * 2, taster=int(options.get(s, "taster_size")))

    @app.get("/shots")
    def shots(request: Request):
        with sessions() as s:
            dispensers = s.scalars(select(Dispenser).where(
                Dispenser.number <= bot.dispenser_count, Dispenser.ingredient_id.is_not(None))
                .order_by(Dispenser.number)).all()
            return page(request, "shots.html", dispensers=dispensers,
                        shot_size=int(options.get(s, "shot_size")))

    # ------------------------------------------------------------------ admin

    @app.get("/admin")
    def admin_dispensers(request: Request):
        with sessions() as s:
            have = {d.number: d for d in s.scalars(select(Dispenser))}
            rows = [have.get(n) or Dispenser(number=n) for n in range(1, bot.dispenser_count + 1)]
            ingredients = s.scalars(select(Ingredient).where(~Ingredient.manual)
                                    .order_by(Ingredient.name)).all()
            makeable = makeable_drinks(s, dispenser_count=bot.dispenser_count)
            n_makeable = len(makeable)
            makes = {d.number: sum(1 for x in makeable if uses(x, d.ingredient))
                     for d in rows if d.ingredient is not None}
            away = one_bottle_away(s, dispenser_count=bot.dispenser_count)
            hand_ids = set(s.scalars(select(RecipeItem.ingredient_id).where(RecipeItem.parts.is_(None))))
            by_hand = s.scalars(select(Ingredient).where(
                Ingredient.manual | Ingredient.on_hand | Ingredient.id.in_(hand_ids))
                .order_by(func.lower(Ingredient.name))).all()
            all_ingredients = s.scalars(select(Ingredient).order_by(func.lower(Ingredient.name))).all()
            return page(request, "admin/dispensers.html", rows=rows, ingredients=ingredients,
                        n_makeable=n_makeable, away=away, by_hand=by_hand, makes=makes,
                        all_ingredients=all_ingredients)

    @app.post("/admin/on-hand")
    async def save_on_hand(request: Request):
        form = await request.form()
        ticked = {int(i) for i in form.getlist("on_hand")}
        with sessions() as s:
            for ing_id in (int(i) for i in form.getlist("listed")):
                if ing := s.get(Ingredient, ing_id):
                    ing.on_hand = ing_id in ticked
            add = " ".join(str(form.get("add", "")).split())
            if add:
                ing = s.scalar(select(Ingredient).where(func.lower(Ingredient.name) == add.lower()))
                if ing is None:
                    return JSONResponse({"error": f"no ingredient called {add!r}"}, status_code=400)
                ing.on_hand = True
            s.commit()
        return RedirectResponse("/admin#on-hand", status_code=303)

    @app.post("/admin/dispensers")
    async def save_dispensers(request: Request):
        form = await request.form()
        with sessions() as s:
            for n in range(1, bot.dispenser_count + 1):
                d = s.get(Dispenser, n) or Dispenser(number=n)
                s.add(d)
                ing = form.get(f"ingredient{n}", "")
                d.ingredient_id = int(ing) if ing else None
                cal = str(form.get(f"cal{n}", "")).strip()
                d.ticks_per_ml = float(cal) if cal else None
            s.commit()
        if not bot.status()["busy"]:
            with suppress(BusyError):
                bot.check_levels(background=True)  # refresh READY / HARD_OUT for the new menu
        return RedirectResponse("/admin", status_code=303)

    @app.get("/admin/drinks")
    def admin_drinks(request: Request):
        with sessions() as s:
            drinks = s.scalars(select(Drink).options(selectinload(Drink.items))
                               .order_by(func.lower(Drink.name))).all()
            pumped, on_hand = pumped_and_on_hand(s, bot.dispenser_count)
            names = dict(s.execute(select(Ingredient.id, Ingredient.name)).all())
            rows = []
            for d in drinks:
                if can_make(d, pumped, on_hand):
                    state = "on the menu" if d.enabled else "switched off"
                else:
                    missing = sorted(names[i] for i, _ in missing_for(d, pumped, on_hand))
                    state = "needs " + ", ".join(missing) if missing else "nothing to pump"
                rows.append((d, ", ".join(categories_of(d)), state))
            n_classics = len(recipes.read().get("drink", []))
            return page(request, "admin/drinks.html", rows=rows, n_classics=n_classics,
                        loaded=request.query_params.get("loaded"))

    @app.post("/admin/recipes/load")
    def load_recipes():
        with sessions() as s:
            report = recipes.load(s)
        return RedirectResponse(f"/admin/drinks?loaded={report.counts['drinks added']}", status_code=303)

    @app.get("/admin/plan")
    def admin_plan(request: Request, pumps: int = 3):
        pumps = max(1, min(15, pumps))
        with sessions() as s:
            bottles, drinks = suggest_bottles(s, pumps)
            return page(request, "admin/plan.html", pumps=pumps, bottles=bottles, drinks=drinks)

    @app.get("/admin/drink/{drink_id}")
    def admin_drink(request: Request, drink_id: str):
        with sessions() as s:
            drink = Drink(name="", enabled=True, popular=False, items=[]) if drink_id == "new" \
                else s.get(Drink, int(drink_id))
            if drink is None:
                return RedirectResponse("/admin/drinks", status_code=303)
            ingredients = s.scalars(select(Ingredient).order_by(func.lower(Ingredient.name))).all()
            categories = sorted({category_of(d) for d in s.scalars(select(Drink))} | {"Non-alcoholic"})
            return page(request, "admin/drink.html", drink=drink, ingredients=ingredients,
                        categories=categories, units=recipes.EDITOR_UNITS, blank_rows=2,
                        created=request.query_params.get("created", ""))

    @app.post("/admin/drink/{drink_id}")
    async def save_drink(request: Request, drink_id: str):
        form = await request.form()
        with sessions() as s:
            if form.get("delete"):
                if drink_id != "new" and (d := s.get(Drink, int(drink_id))):
                    s.delete(d)
                    s.commit()
                return RedirectResponse("/admin/drinks", status_code=303)
            drink = Drink() if drink_id == "new" else s.get(Drink, int(drink_id))
            drink.name = str(form.get("name", "")).strip() or "Unnamed drink"
            drink.sort_name = str(form.get("sort_name", "")).strip()
            drink.description = str(form.get("description", "")).strip()
            size = str(form.get("size_ml", "")).strip()
            drink.size_ml = int(size) if size else None
            drink.popular = bool(form.get("popular"))
            drink.enabled = bool(form.get("enabled"))
            drink.glass = str(form.get("glass", "")).strip()
            drink.instructions = str(form.get("instructions", "")).strip()
            drink.finish = str(form.get("finish", "")).strip()
            drink.category = str(form.get("category", "")).strip()
            by_name = {i.name.lower(): i for i in s.scalars(select(Ingredient))}
            rows: dict[int, tuple] = {}
            created = []
            for name, amount, unit, step in zip(form.getlist("ing_name"), form.getlist("amount"),
                                                form.getlist("unit"), form.getlist("step"), strict=True):
                name = " ".join(str(name).split())
                if not name:
                    continue
                try:
                    parts, amt, unit, step = recipes.row_line(str(amount), str(unit), str(step))
                except ValueError as e:
                    return JSONResponse({"error": f"{name}: {e}"}, status_code=400)
                ing = by_name.get(name.lower())
                if ing is None:  # new ingredient: a mixer until edited under Ingredients
                    ing = by_name[name.lower()] = Ingredient(name=name, alcoholic=False)
                    s.add(ing)
                    s.flush()
                    created.append(name)
                old = rows.get(ing.id)
                if old and old[0] is not None and parts is not None:  # listed twice: add up
                    parts, amt, unit = old[0] + parts, None, ""
                rows[ing.id] = (parts, amt, unit, step)
            s.add(drink)
            drink.items.clear()
            s.flush()  # delete the old rows first: (drink, ingredient) is unique
            drink.items = [RecipeItem(ingredient_id=i, parts=p, amount=a, unit=u, step=st, position=n)
                           for n, (i, (p, a, u, st)) in enumerate(rows.items())]
            s.commit()
            if created:
                return RedirectResponse(f"/admin/drink/{drink.id}?created=" + ", ".join(created),
                                        status_code=303)
        return RedirectResponse("/admin/drinks", status_code=303)

    @app.post("/admin/drink/{drink_id}/duplicate")
    def duplicate_drink(drink_id: int):
        with sessions() as s:
            d = s.get(Drink, drink_id)
            if d is None:
                return RedirectResponse("/admin/drinks", status_code=303)
            copy = Drink(name=f"{d.name} (copy)", sort_name="", description=d.description, popular=False,
                         enabled=False, size_ml=d.size_ml, instructions=d.instructions, glass=d.glass,
                         finish=d.finish, category=d.category, source="",
                         items=[RecipeItem(ingredient_id=i.ingredient_id, parts=i.parts, amount=i.amount,
                                           unit=i.unit, step=i.step, position=i.position) for i in d.items])
            s.add(copy)
            s.commit()
            return RedirectResponse(f"/admin/drink/{copy.id}", status_code=303)

    @app.post("/admin/drink/{drink_id}/toggle/{field}")
    def toggle_drink(drink_id: int, field: str):
        if field not in ("enabled", "popular"):
            return JSONResponse({"error": "bad field"}, status_code=400)
        with sessions() as s:
            d = s.get(Drink, drink_id)
            setattr(d, field, not getattr(d, field))
            s.commit()
            return {field: getattr(d, field)}

    @app.get("/admin/ingredients")
    def admin_ingredients(request: Request):
        with sessions() as s:
            ingredients = s.scalars(select(Ingredient).order_by(func.lower(Ingredient.name))).all()
            used = set(s.scalars(select(RecipeItem.ingredient_id))) | \
                set(s.scalars(select(Dispenser.ingredient_id)))
            return page(request, "admin/ingredients.html", ingredients=ingredients, used=used)

    @app.get("/admin/ingredient/{ing_id}")
    def admin_ingredient(request: Request, ing_id: str):
        with sessions() as s:
            ing = Ingredient(name="", brand="", description="", abv=0, kind=Kind.OTHER, manual=False,
                             on_hand=False, alcoholic=False) \
                if ing_id == "new" else s.get(Ingredient, int(ing_id))
            if ing is None:
                return RedirectResponse("/admin/ingredients", status_code=303)
            generics = s.scalars(select(Ingredient).where(Ingredient.generic_id.is_(None))
                                 .order_by(Ingredient.name)).all()
            return page(request, "admin/ingredient.html", ing=ing, kinds=list(Kind),
                        generics=[g for g in generics if g.id != ing.id])

    @app.post("/admin/ingredient/{ing_id}")
    async def save_ingredient(request: Request, ing_id: str):
        form = await request.form()
        with sessions() as s:
            if form.get("delete"):
                ing = s.get(Ingredient, int(ing_id)) if ing_id != "new" else None
                if ing is not None:
                    used = s.scalar(select(func.count()).select_from(RecipeItem)
                                    .where(RecipeItem.ingredient_id == ing.id)) or \
                        s.scalar(select(func.count()).select_from(Dispenser)
                                 .where(Dispenser.ingredient_id == ing.id))
                    if used:
                        return JSONResponse({"error": "still used by a drink or dispenser"}, status_code=400)
                    for spec in ing.specifics:
                        spec.generic_id = None
                    s.delete(ing)
                    s.commit()
                return RedirectResponse("/admin/ingredients", status_code=303)
            ing = Ingredient() if ing_id == "new" else s.get(Ingredient, int(ing_id))
            ing.name = str(form.get("name", "")).strip() or "Unnamed"
            ing.brand = str(form.get("brand", "")).strip()
            ing.description = str(form.get("description", "")).strip()
            ing.abv = float(form.get("abv") or 0)
            ing.kind = Kind(form.get("kind", "other"))
            ing.manual = bool(form.get("manual"))
            ing.alcoholic = bool(form.get("alcoholic"))
            ing.on_hand = bool(form.get("on_hand"))
            generic = form.get("generic_id", "")
            ing.generic_id = int(generic) if generic and generic != str(ing.id) else None
            s.add(ing)
            s.commit()
        return RedirectResponse("/admin/ingredients", status_code=303)

    @app.get("/admin/options")
    def admin_options(request: Request):
        return page(request, "admin/options.html",
                    fields=[(k, v) for k, v in options.DEFAULTS.items() if k != "login_password_hash"])

    @app.post("/admin/options")
    async def save_options(request: Request):
        form = await request.form()
        with sessions() as s:
            for key, default in options.DEFAULTS.items():
                if key == "login_password_hash":
                    continue
                if isinstance(default, bool):
                    options.set(s, key, bool(form.get(key)))
                elif key in form:
                    with suppress(ValueError):  # a bad value keeps the old one
                        options.set(s, key, str(form[key]))
            s.commit()
        return RedirectResponse("/admin/options", status_code=303)

    @app.get("/admin/log")
    def admin_log(request: Request, days: int = 7):
        since = utcnow() - timedelta(days=days)
        with sessions() as s:
            rows = s.execute(
                select(Drink.name, func.count(), func.sum(PourLog.size_ml))
                .join(Drink, Drink.id == PourLog.drink_id).where(PourLog.time >= since)
                .group_by(Drink.name).order_by(func.count().desc())).all()
            shots = s.execute(
                select(Ingredient.name, func.count(), func.sum(PourLog.size_ml))
                .join(Ingredient, Ingredient.id == PourLog.ingredient_id).where(PourLog.time >= since)
                .group_by(Ingredient.name).order_by(func.count().desc())).all()
            total = s.scalar(select(func.sum(PourLog.size_ml)).where(PourLog.time >= since)) or 0
            return page(request, "admin/log.html", rows=rows, shots=shots, days=days, total=total)

    # ------------------------------------------------------------------ parties

    @app.get("/admin/parties")
    def admin_parties(request: Request):
        with sessions() as s:
            parties = s.scalars(select(Party).order_by(func.lower(Party.name))).all()
            return page(request, "admin/parties.html", parties=parties)

    @app.get("/admin/party/{party_id}")
    def admin_party(request: Request, party_id: str):
        with sessions() as s:
            party = Party(name="", title="", welcome="", drinks=[]) if party_id == "new" \
                else s.get(Party, int(party_id))
            if party is None:
                return RedirectResponse("/admin/parties", status_code=303)
            listed = {pd.drink_id: pd for pd in party.drinks}
            all_drinks = s.scalars(select(Drink).options(selectinload(Drink.items))
                                   .where(Drink.enabled)).all()
            can = {d.id for d in makeable_drinks(s, dispenser_count=bot.dispenser_count)}
            colors = {k: getattr(party, f"color_{k}") or v for k, v in THEME_DEFAULTS.items()}
            return page(request, "admin/party.html", p=party, listed=listed, can=can, colors=colors, robots=ROBOTS,
                        sections=by_category(list(all_drinks), every_section=False), slugify=slugify)

    @app.post("/admin/party/{party_id}")
    async def save_party(request: Request, party_id: str):
        form = await request.form()
        with sessions() as s:
            party = Party(drinks=[]) if party_id == "new" else s.get(Party, int(party_id))
            if party is None:
                return RedirectResponse("/admin/parties", status_code=303)
            party.name = str(form.get("name", "")).strip() or "Party"
            party.title = str(form.get("title", "")).strip()
            party.welcome = str(form.get("welcome", "")).strip()
            robot = str(form.get("robot", ""))
            party.robot = robot if robot in ROBOTS else ""
            party.marquee = " ".join(str(form.get("marquee", "")).split())[:MARQUEE_MAX]
            for key, default in THEME_DEFAULTS.items():
                color = valid_color(str(form.get(f"color_{key}", "")))
                setattr(party, f"color_{key}", "" if color in ("", default) else color)
            logo = form.get("logo")
            if form.get("remove_logo"):
                party.logo = ""
            elif isinstance(logo, UploadFile) and logo.filename and not allow_uploads:
                return JSONResponse({"error": "logo uploads are switched off on this bot"}, status_code=400)
            elif isinstance(logo, UploadFile) and logo.filename:
                suffix = Path(logo.filename).suffix.lower()
                data = await logo.read()
                if suffix not in LOGO_TYPES or len(data) > MAX_LOGO_BYTES:
                    return JSONResponse({"error": f"logo must be {', '.join(sorted(LOGO_TYPES))} "
                                                  f"and under {MAX_LOGO_BYTES // 1_000_000} MB"}, status_code=400)
                name = f"logo-{secrets.token_hex(6)}{suffix}"
                (uploads / name).write_bytes(data)
                party.logo = name
            chosen = [int(i) for i in form.getlist("drink")]
            featured = {int(i) for i in form.getlist("featured")}

            def position(drink_id: int) -> int:
                try:
                    return int(form.get(f"pos{drink_id}") or 0)
                except ValueError:
                    return 0
            s.add(party)
            party.drinks.clear()
            s.flush()
            party.drinks = [PartyDrink(drink_id=i, featured=i in featured, position=position(i))
                            for i in chosen]
            s.commit()
            return RedirectResponse(f"/admin/party/{party.id}?saved=1", status_code=303)

    @app.post("/admin/party/{party_id}/{action}")
    def party_action(party_id: int, action: str):
        with sessions() as s:
            party = s.get(Party, party_id)
            if party is None:
                return RedirectResponse("/admin/parties", status_code=303)
            if action == "activate":
                for other in s.scalars(select(Party).where(Party.active)):
                    other.active = False
                party.active = True
            elif action == "deactivate":
                party.active = False
            elif action == "duplicate":
                copy = Party(name=f"{party.name} (copy)", title=party.title, welcome=party.welcome,
                             logo=party.logo, robot=party.robot, marquee=party.marquee, **{f"color_{k}": getattr(party, f"color_{k}") for k in THEME_DEFAULTS},
                             drinks=[PartyDrink(drink_id=pd.drink_id, featured=pd.featured, position=pd.position)
                                     for pd in party.drinks])
                s.add(copy)
            elif action == "delete":
                s.delete(party)
            else:
                return JSONResponse({"error": f"unknown action {action}"}, status_code=400)
            s.commit()
        return RedirectResponse("/admin/parties", status_code=303)

    # ------------------------------------------------------------------ JSON API

    @app.get("/api/status")
    def api_status():
        return guest_event(bot.status())

    @app.get("/api/drink/{drink_id}/plan")
    def api_plan(drink_id: int, size_ml: float | None = None, strength: int = 0, tartness: int = 0):
        plan = bot.plan_drink(drink_id, size_ml, strength, tartness)
        with sessions() as s:
            names = {d.number: d.ingredient.name for d in s.scalars(select(Dispenser))
                     if d.ingredient is not None}
        return {"name": display_name(plan.name), "size_ml": plan.size_ml,
                "pumps": [{"dispenser": n, "ingredient": display_name(names.get(n, "?")), "ml": round(ml, 1)}
                          for n, ml in sorted(plan.pumps.items())],
                "pre_pumps": [{"dispenser": n, "ingredient": display_name(names.get(n, "?")), "ml": round(ml, 1)}
                              for n, ml in sorted(plan.pre_pumps.items())],
                "before": [_hand(h) for h in plan.before], "after": [_hand(h) for h in plan.after],
                "instructions": plan.instructions, "finish": plan.finish}

    def _hand(h) -> dict:
        return {"ingredient": display_name(h.ingredient), "ml": None if h.ml is None else round(h.ml, 1), "text": h.text}

    @app.post("/api/drink/{drink_id}/make", status_code=202)
    def api_make(drink_id: int, req: MakeRequest):
        plan = bot.make_drink(drink_id, req.size_ml, req.strength, req.tartness, background=True)
        return {"pouring": display_name(plan.name), "ml": round(plan.pumped_ml)}

    @app.post("/api/shot/{number}", status_code=202)
    def api_shot(number: int, req: AmountRequest | None = None):
        plan = bot.shot(number, req.ml if req else None, background=True)
        return {"pouring": display_name(plan.name), "ml": round(plan.pumped_ml)}

    @app.post("/api/dispenser/{number}/test", status_code=202)
    def api_test(number: int, req: AmountRequest | None = None):
        bot.test_dispense(number, req.ml if req else None, background=True)
        return {"ok": True}

    @app.post("/api/dispenser/{number}/clean", status_code=202)
    def api_clean_pump(number: int):
        bot.clean_pump(number, background=True)
        return {"ok": True}

    @app.post("/api/dispenser/{number}/run", status_code=202)
    def api_run(number: int, req: RunRequest):
        bot.run_pump(number, req.ms, req.reverse, background=True)
        return {"ok": True}

    @app.post("/api/dispenser/{number}")
    def api_set_dispenser(number: int, req: PumpRequest):
        """Put a bottle on a pump (by ingredient name; "" = empty) and/or set its calibration."""
        if not 1 <= number <= bot.dispenser_count:
            raise CantPourError(f"no dispenser #{number}")
        with sessions() as s:
            d = s.get(Dispenser, number) or Dispenser(number=number)
            s.add(d)
            name = " ".join(req.ingredient.split())
            if name:
                ing = s.scalar(select(Ingredient).where(func.lower(Ingredient.name) == name.lower()))
                if ing is None:
                    raise CantPourError(f"no ingredient called {name!r} - add it under Ingredients first")
                if ing.manual:
                    raise CantPourError(f"{ing.name} is marked as never going on a pump")
                d.ingredient = ing
            else:
                d.ingredient = None
            d.ticks_per_ml = req.ticks_per_ml if req.ticks_per_ml and req.ticks_per_ml > 0 else None
            s.commit()
            makeable = makeable_drinks(s, dispenser_count=bot.dispenser_count)
            makes = sum(1 for x in makeable if d.ingredient and uses(x, d.ingredient))
            result = {"ingredient": d.ingredient.name if d.ingredient else "", "makes": makes,
                      "menu": len(makeable)}
        if not bot.status()["busy"]:
            with suppress(BusyError):
                bot.check_levels(background=True)
        return result

    @app.post("/api/pumps/run", status_code=202)
    def api_run_all(req: RunAllRequest):
        bot.run_pumps(None, req.ms, req.reverse, background=True)
        return {"ok": True}

    @app.post("/api/drink-preview")
    def api_drink_preview(req: PreviewRequest):
        """What the drink editor's unsaved rows would pour: per line pumped / by hand / missing,
        ml, and the drink's ABV and standard drinks. Nothing is saved."""
        with sessions() as s, s.no_autoflush:
            by_name = {i.name.lower(): i for i in s.scalars(select(Ingredient))}
            drink = Drink(name="preview", items=[])
            errors = []
            for n, row in enumerate(req.rows):
                name = " ".join(row.ingredient.split())
                if not name:
                    continue
                try:
                    parts, amt, unit, step = recipes.row_line(row.amount, row.unit, row.step)
                except ValueError as e:
                    errors.append(f"{name}: {e}")
                    continue
                ing = by_name.get(name.lower()) or Ingredient(id=-1 - n, name=name, abv=0.0,
                                                               alcoholic=False, manual=False)
                drink.items.append(RecipeItem(ingredient=ing, ingredient_id=ing.id, parts=parts,
                                              amount=amt, unit=unit, step=step, position=n))
            measured = sum(i.parts for i in drink.items if i.parts is not None and i.unit in recipes.UNITS_ML)
            size = req.size_ml or measured or float(options.get(s, "drink_size"))
            amounts = scale_recipe(drink, size)
            pumped, on_hand = pumped_and_on_hand(s, bot.dispenser_count)
            lines = []
            for i in drink.items:
                how = resolve_line(i, pumped, on_hand) if i.ingredient.id > 0 else "new"
                ml = amounts.get(i.ingredient_id)
                lines.append({"ingredient": i.ingredient.name, "how": how or "missing",
                              "ml": None if ml is None else round(ml, 1),
                              "text": i.hand_text if i.parts is None else "", "step": i.step,
                              "by_hand": i.by_hand})
            total, abv, std = strength_of([(i.ingredient, amounts[i.ingredient_id]) for i in drink.items
                                           if i.ingredient_id in amounts])
            s.rollback()
        return {"lines": lines, "size_ml": round(size), "total_ml": round(total), "abv": round(abv, 1),
                "std_drinks": round(std, 2), "errors": errors}

    @app.post("/api/clean", status_code=202)
    def api_clean(req: CleanRequest):
        bot.clean(req.which, background=True)
        return {"ok": True}

    @app.post("/api/check-levels", status_code=202)
    def api_check_levels():
        bot.check_levels(background=True)
        return {"ok": True}

    @app.post("/api/continue")
    def api_continue():
        bot.continue_pour()
        return {"ok": True}

    @app.post("/api/cancel-pour")
    def api_cancel_pour():
        bot.continue_pour(cancel=True)
        return {"ok": True}

    @app.post("/api/reset")
    def api_reset():
        bot.reset()
        return bot.status()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        if record is not None:
            record(websocket.client.host if websocket.client else "", "WS", websocket, 101, time.monotonic())
        q: asyncio.Queue = asyncio.Queue()
        clients.add(q)
        try:
            await websocket.send_json(guest_event({"type": "status", **bot.status()}))
            while True:
                await websocket.send_json(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass  # closed by the browser
        finally:
            clients.discard(q)

    return app
