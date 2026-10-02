"""FastAPI web app: drink menu, shots and admin pages, a JSON API the pages call, and a
WebSocket that pushes the bot's status and pour events to every open screen (the bot's own
touchscreen and phones alike). No login: whoever is on the bot's WiFi can use everything.

create_app() takes a ready Bot, so tests and the server share the same code; server.py opens
the hardware (or the simulator) and runs it.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .. import __version__
from ..bot import Bot, BusyError, CantPourError
from ..db import options
from ..db import recipes
from ..db.menu import makeable_drinks, one_bottle_away, suggest_bottles
from ..db.models import Dispenser, Drink, Ingredient, Kind, PourLog, RecipeItem, utcnow

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
ML_PER_OZ = 29.57  # the old UI used 30
SIZE_STEP_ML = 30  # drink size +/- buttons (old size_increment)


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


def create_app(bot: Bot, bot_name: str = "Bartendro") -> FastAPI:
    sessions = bot.sessions
    clients: set[asyncio.Queue] = set()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()

        def from_bot(event: dict) -> None:  # called from the bot's worker threads
            loop.call_soon_threadsafe(_broadcast, event)

        def _broadcast(event: dict) -> None:
            for q in list(clients):
                q.put_nowait(event)

        bot.subscribe(from_bot)
        yield
        bot.unsubscribe(from_bot)

    app = FastAPI(title="Bartendro", version=__version__, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")

    def page(request: Request, name: str, **ctx):
        with sessions() as s:
            opts = options.get_all(s)
        metric = opts["metric"]

        def amount(ml: float) -> str:
            return f"{ml:.0f} ml" if metric else f"{ml / ML_PER_OZ:.1f} oz"
        return templates.TemplateResponse(request, name, {
            "bot_name": bot_name, "status": bot.status(), "opts": opts, "amount": amount,
            "version": __version__, **ctx})

    # ------------------------------------------------------------------ errors

    @app.exception_handler(BusyError)
    async def busy(_request, exc):
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(CantPourError)
    async def cant_pour(_request, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

    # ------------------------------------------------------------------ pages

    @app.get("/")
    def menu(request: Request):
        with sessions() as s:
            drinks = makeable_drinks(s, dispenser_count=bot.dispenser_count)
            essentials = [d for d in drinks if d.popular]
            others = [d for d in drinks if not d.popular]
            return page(request, "menu.html", essentials=essentials, others=others)

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
            n_makeable = len(makeable_drinks(s, dispenser_count=bot.dispenser_count))
            away = one_bottle_away(s, dispenser_count=bot.dispenser_count)
            hand_ids = set(s.scalars(select(RecipeItem.ingredient_id).where(RecipeItem.parts.is_(None))))
            by_hand = s.scalars(select(Ingredient).where(Ingredient.manual | Ingredient.id.in_(hand_ids))
                                .order_by(func.lower(Ingredient.name))).all()
            return page(request, "admin/dispensers.html", rows=rows, ingredients=ingredients,
                        n_makeable=n_makeable, away=away, by_hand=by_hand)

    @app.post("/admin/on-hand")
    async def save_on_hand(request: Request):
        form = await request.form()
        ticked = {int(i) for i in form.getlist("on_hand")}
        with sessions() as s:
            for ing_id in (int(i) for i in form.getlist("listed")):
                if ing := s.get(Ingredient, ing_id):
                    ing.on_hand = ing_id in ticked
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
            try:
                bot.check_levels(background=True)  # refresh READY / HARD_OUT for the new menu
            except BusyError:
                pass
        return RedirectResponse("/admin", status_code=303)

    @app.get("/admin/drinks")
    def admin_drinks(request: Request):
        with sessions() as s:
            drinks = s.scalars(select(Drink).options(selectinload(Drink.items))
                               .order_by(func.lower(Drink.name))).all()
            can = {d.id for d in makeable_drinks(s, dispenser_count=bot.dispenser_count,
                                                 enabled_only=False)}
            n_classics = len(recipes.read().get("drink", []))
            return page(request, "admin/drinks.html", drinks=drinks, can=can, n_classics=n_classics,
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
            ingredients = s.scalars(select(Ingredient).order_by(Ingredient.name)).all()
            return page(request, "admin/drink.html", drink=drink, ingredients=ingredients,
                        blank_rows=max(3, 8 - len(drink.items)))

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
            rows: dict[int, tuple] = {}
            for ing, text in zip(form.getlist("ingredient"), form.getlist("parts")):
                if not ing or not str(text).strip():
                    continue
                try:
                    parts, amount, unit, step = recipes.parse_amount(str(text))
                except ValueError as e:
                    return JSONResponse({"error": str(e)}, status_code=400)
                old = rows.get(int(ing))
                if old and old[0] is not None and parts is not None:  # listed twice: add up
                    parts, amount, unit = old[0] + parts, None, ""
                rows[int(ing)] = (parts, amount, unit, step)
            s.add(drink)
            drink.items.clear()
            s.flush()  # delete the old rows first: (drink, ingredient) is unique
            drink.items = [RecipeItem(ingredient_id=i, parts=p, amount=a, unit=u, step=st, position=n)
                           for n, (i, (p, a, u, st)) in enumerate(rows.items())]
            s.add(drink)
            s.commit()
        return RedirectResponse("/admin/drinks", status_code=303)

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
                    try:
                        options.set(s, key, str(form[key]))
                    except ValueError:
                        pass  # keep the old value
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

    # ------------------------------------------------------------------ JSON API

    @app.get("/api/status")
    def api_status():
        return bot.status()

    @app.get("/api/drink/{drink_id}/plan")
    def api_plan(drink_id: int, size_ml: float | None = None, strength: int = 0, tartness: int = 0):
        plan = bot.plan_drink(drink_id, size_ml, strength, tartness)
        with sessions() as s:
            names = {d.number: d.ingredient.name for d in s.scalars(select(Dispenser))
                     if d.ingredient is not None}
        return {"name": plan.name, "size_ml": plan.size_ml,
                "pumps": [{"dispenser": n, "ingredient": names.get(n, "?"), "ml": round(ml, 1)}
                          for n, ml in sorted(plan.pumps.items())],
                "pre_pumps": [{"dispenser": n, "ingredient": names.get(n, "?"), "ml": round(ml, 1)}
                              for n, ml in sorted(plan.pre_pumps.items())],
                "before": [_hand(h) for h in plan.before], "after": [_hand(h) for h in plan.after],
                "instructions": plan.instructions, "finish": plan.finish}

    def _hand(h) -> dict:
        return {"ingredient": h.ingredient, "ml": None if h.ml is None else round(h.ml, 1), "text": h.text}

    @app.post("/api/drink/{drink_id}/make", status_code=202)
    def api_make(drink_id: int, req: MakeRequest):
        plan = bot.make_drink(drink_id, req.size_ml, req.strength, req.tartness, background=True)
        return {"pouring": plan.name, "ml": round(plan.pumped_ml)}

    @app.post("/api/shot/{number}", status_code=202)
    def api_shot(number: int, req: AmountRequest | None = None):
        plan = bot.shot(number, req.ml if req else None, background=True)
        return {"pouring": plan.name, "ml": round(plan.pumped_ml)}

    @app.post("/api/dispenser/{number}/test", status_code=202)
    def api_test(number: int, req: AmountRequest | None = None):
        bot.test_dispense(number, req.ml if req else None, background=True)
        return {"ok": True}

    @app.post("/api/dispenser/{number}/run", status_code=202)
    def api_run(number: int, req: RunRequest):
        bot.run_pump(number, req.ms, req.reverse, background=True)
        return {"ok": True}

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
        q: asyncio.Queue = asyncio.Queue()
        clients.add(q)
        try:
            await websocket.send_json({"type": "status", **bot.status()})
            while True:
                await websocket.send_json(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass  # closed by the browser
        finally:
            clients.discard(q)

    return app
