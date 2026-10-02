// Static demo (scripts/export_site.py): stands in for the bot so the pages can be tried on
// GitHub Pages. API calls are answered from exported files (each drink's pour plan at every
// strength, written next to the pages) or faked; pours play the same pop-ups as the real bot.
// Loaded only when the app runs with demo=True; app.js doesn't open its WebSocket then.
"use strict";

(() => {
  const realFetch = window.fetch.bind(window);
  const json = (data, status = 200) => new Response(JSON.stringify(data),
    { status, headers: { "Content-Type": "application/json" } });
  const notSaved = { error: "This is a static demo - nothing is saved or poured." };
  let lastPlan = null, waiting = null;

  // app.js defines Bartendro as a top-level const: visible by name, not as window.Bartendro
  const app = () => (typeof Bartendro === "undefined" ? null : Bartendro);
  const emit = (ev) => app() && app().onEvent(ev);
  const status = (state, pouring = null) =>
    emit({ type: "status", state, message: "", busy: state !== "ready", dispensers: 15, pouring });

  function finish(plan) {
    emit({ type: "pouring", name: plan.name, ml: Math.round(plan.pumps.reduce((a, p) => a + p.ml, 0)),
           after: plan.after.map((h) => [h.ingredient, h.ml, h.text]), finish: plan.finish });
    setTimeout(() => {
      emit({ type: "done", name: plan.name, after: plan.after.map((h) => [h.ingredient, h.ml, h.text]),
             finish: plan.finish });
      status("ready");
    }, 2500);
  }

  function pour(plan) {
    status("pouring", plan.name);
    if (plan.pre_pumps && plan.pre_pumps.length) {  // e.g. an absinthe rinse from a pump
      emit({ type: "pouring", name: plan.name, ml: 1, stage: 1 });
      setTimeout(() => {
        waiting = plan;
        emit({ type: "stage_done", name: plan.name, instructions: plan.instructions,
               before: plan.before.map((h) => [h.ingredient, h.ml, h.text]) });
      }, 1500);
    } else {
      finish(plan);
    }
  }

  async function planFor(id, url) {
    const strength = +(url.searchParams.get("strength") || 0);
    const size = url.searchParams.get("size_ml");
    const res = await realFetch(`api/drink/${id}/plan${strength}.json`);
    if (!res.ok) return json({ error: "This drink can't be made with the demo's bottles." }, 400);
    const plan = await res.json();
    if (plan.error) return json(plan, 400);
    if (size && +size !== plan.size_ml) {  // the size buttons scale everything that's measured
      const k = +size / plan.size_ml;
      for (const p of [...plan.pumps, ...plan.pre_pumps]) p.ml = Math.round(p.ml * k * 10) / 10;
      for (const h of [...plan.before, ...plan.after]) if (h.ml != null) h.ml = Math.round(h.ml * k * 10) / 10;
      plan.size_ml = +size;
    }
    lastPlan = plan;
    return json(plan);
  }

  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    const path = url.pathname.replace(/^.*?\/api\//, "/api/");
    if (!path.startsWith("/api/") || path.endsWith(".json")) return realFetch(input, init);
    const method = (init.method || "GET").toUpperCase();
    let m;
    if (method === "GET" && (m = path.match(/^\/api\/drink\/(\d+)\/plan$/))) return planFor(m[1], url);
    if ((m = path.match(/^\/api\/drink\/(\d+)\/make$/))) {
      if (!lastPlan) return json({ error: "no plan" }, 400);
      pour(lastPlan);
      return json({ pouring: lastPlan.name }, 202);
    }
    if ((m = path.match(/^\/api\/shot\/(\d+)$/))) {
      pour({ name: "your shot", pumps: [{ ml: 30 }], pre_pumps: [], before: [], after: [], finish: "" });
      return json({ pouring: "shot" }, 202);
    }
    if (path === "/api/continue" && waiting) { const p = waiting; waiting = null; finish(p); return json({ ok: true }); }
    if (path === "/api/cancel-pour") { waiting = null; status("ready"); return json({ ok: true }); }
    if (path === "/api/drink-preview") return json({ demo: true });
    if (path === "/api/status") return json(window.BARTENDRO.status);
    return json(notSaved, 400);  // admin actions, pumps, cleaning...
  };

  // Admin forms would post to a server that isn't there.
  document.addEventListener("submit", (e) => {
    e.preventDefault();
    if (app()) app().toast(notSaved.error);
  }, true);
})();
