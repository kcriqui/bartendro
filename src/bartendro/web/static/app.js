// Bartendro pages: live status over a WebSocket, buttons that call the JSON API, the drink page.
// No libraries: the bot's hotspot has no internet.
"use strict";

const Bartendro = (() => {
  const $ = (sel) => document.querySelector(sel);
  const metric = window.BARTENDRO.metric;
  const ML_PER_OZ = 29.57;
  const amount = (ml) => metric ? `${Math.round(ml)} ml` : `${(ml / ML_PER_OZ).toFixed(1)} oz`;

  const STATUS_TEXT = {
    low: "A bottle is running low", out: "A bottle is empty", hard_out: "Nothing can be made right now",
    pouring: "Pouring…", cleaning: "Cleaning…", error: "Something is wrong", current_sense: "A pump stalled",
  };

  // ------------------------------------------------------------- toast / overlay
  let toastTimer;
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 4000);
  }

  let overlayTimer;
  function handLine(h) {  // {ingredient, ml, text} or [ingredient, ml, text]
    const [name, ml, text] = Array.isArray(h) ? h : [h.ingredient, h.ml, h.text];
    return text ? `${text} ${name}` : `${amount(ml)} ${name}`;
  }

  function overlay({ title, text = "", manual = [], spinner = false, bad = false, reset = false, close = false,
                     autoHide = 0, go = null }) {
    const o = $("#overlay");
    $("#overlay-title").textContent = title;
    $("#overlay-text").textContent = text;
    $("#overlay-finish").textContent = "";
    $("#overlay-spinner").hidden = !spinner;
    $("#overlay-reset").hidden = !reset;
    $("#overlay-close").hidden = !close;
    const ul = $("#overlay-manual");
    ul.innerHTML = "";
    for (const h of manual) {
      const li = document.createElement("li");
      li.textContent = handLine(h);
      ul.appendChild(li);
    }
    $("#overlay-close").textContent = go ? "Cancel" : "OK";
    const goBtn = $("#overlay-go");
    goBtn.hidden = !go;
    goBtn.onclick = go ? () => { hideOverlay(); go(); } : null;
    o.classList.toggle("bad", bad);
    o.hidden = false;
    clearTimeout(overlayTimer);
    if (autoHide) overlayTimer = setTimeout(hideOverlay, autoHide);
  }
  function hideOverlay() { $("#overlay").hidden = true; }

  // ------------------------------------------------------------------ API
  async function post(url, body) {
    const res = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = {};
    try { data = await res.json(); } catch (e) { /* empty body */ }
    if (!res.ok) throw new Error(data.error || data.detail || `${res.status} ${res.statusText}`);
    return data;
  }

  // ------------------------------------------------------------- live status
  let lastState = window.BARTENDRO.status.state;

  function showStatus(s) {
    const bar = $("#statusbar");
    bar.className = `statusbar state-${s.state}`;
    let text = STATUS_TEXT[s.state] || "";
    if (s.state === "pouring" && s.pouring) text = `Pouring ${s.pouring}…`;
    if (s.message && (s.state === "error" || s.state === "current_sense" || s.state === "cleaning")) text += `: ${s.message}`;
    $("#status-text").textContent = text;
    if (s.state === "error" || s.state === "current_sense") {
      overlay({ title: STATUS_TEXT[s.state], text: s.message, bad: true, reset: true, close: true });
    } else if (s.state === "cleaning") {
      overlay({ title: "Cleaning", text: "Flushing the lines…", spinner: true });
    } else if (lastState === "cleaning") {
      hideOverlay();
    }
    lastState = s.state;
  }

  function onEvent(ev) {
    if (ev.type === "status") showStatus(ev);
    else if (ev.type === "pouring") overlay({ title: `Pouring ${ev.name}`, text: amount(ev.ml), spinner: true });
    else if (ev.type === "done") {
      const manual = ev.after || [];
      const todo = manual.length || ev.finish;
      let text = `Your ${ev.name} is ready.`;
      if (manual.length) text = `Now add to your ${ev.name}:`;
      else if (ev.finish) text = ev.finish;
      overlay({ title: todo ? "Almost done!" : "Enjoy!", text, manual, close: true,
                autoHide: todo ? 0 : 5000 });
      if (manual.length && ev.finish) $("#overlay-finish").textContent = ev.finish;
    }
  }

  function connect(delay = 500) {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onmessage = (m) => onEvent(JSON.parse(m.data));
    ws.onopen = () => { delay = 500; };
    ws.onclose = () => setTimeout(() => connect(Math.min(delay * 2, 10000)), delay);
  }

  // ------------------------------------------------------------ generic buttons
  document.addEventListener("click", async (e) => {
    const confirmBtn = e.target.closest("[data-confirm]");
    if (confirmBtn && !confirm(confirmBtn.dataset.confirm)) { e.preventDefault(); return; }
    const btn = e.target.closest("[data-post]");
    if (!btn) return;
    e.preventDefault();
    btn.disabled = true;
    try {
      await post(btn.dataset.post, btn.dataset.body ? JSON.parse(btn.dataset.body) : undefined);
    } catch (err) {
      toast(err.message);
    } finally {
      btn.disabled = false;
    }
  });

  document.addEventListener("change", async (e) => {
    const box = e.target.closest("[data-toggle]");
    if (!box) return;
    try { await post(box.dataset.toggle); } catch (err) { box.checked = !box.checked; toast(err.message); }
  });

  $("#overlay-close").addEventListener("click", hideOverlay);
  $("#overlay-reset").addEventListener("click", async () => {
    try { await post("/api/reset"); hideOverlay(); } catch (err) { toast(err.message); }
  });

  // ------------------------------------------------------------------ drink page
  function drinkPage() {
    const sec = $(".drink");
    const id = sec.dataset.drink, step = +sec.dataset.step, max = +sec.dataset.max, steps = +sec.dataset.steps;
    let size = +sec.dataset.size, strength = 0, plan = null;
    const strengthNames = { "-2": "much weaker", "-1": "weaker", "0": "normal", "1": "stronger", "2": "much stronger" };

    async function refresh() {
      const sizeText = $("#size-text");
      if (sizeText) sizeText.textContent = amount(size);
      const st = $("#strength-text");
      if (st) st.textContent = strengthNames[strength] || (strength > 0 ? `+${strength}` : `${strength}`);
      const res = await fetch(`/api/drink/${id}/plan?size_ml=${size}&strength=${strength}`);
      const data = await res.json();
      const err = $("#plan-error");
      if (!res.ok) { err.textContent = data.error || "can't make this now"; err.hidden = false; $("#make").disabled = true; return; }
      err.hidden = true;
      $("#make").disabled = false;
      const table = $("#recipe");
      table.innerHTML = "";
      plan = data;
      const rows = data.before.map((m) => [`${m.ingredient} (first, by hand)`, m.text || amount(m.ml)])
        .concat(data.pumps.map((p) => [p.ingredient, amount(p.ml)]))
        .concat(data.after.map((m) => [`${m.ingredient} (after pouring, by hand)`, m.text || amount(m.ml)]));
      for (const [name, qty] of rows) {
        const tr = table.insertRow();
        tr.insertCell().textContent = name;
        const td = tr.insertCell();
        td.className = "num";
        td.textContent = qty;
      }
    }

    sec.addEventListener("click", (e) => {
      const b = e.target.closest("[data-adjust]");
      if (!b) return;
      const dir = +b.dataset.dir;
      if (b.dataset.adjust === "size") size = Math.min(max, Math.max(step, size + dir * step));
      else strength = Math.min(steps, Math.max(-steps, strength + dir));
      refresh();
    });

    async function make(ml) {
      const btn = $("#make");
      btn.disabled = true;
      try { await post(`/api/drink/${id}/make`, { size_ml: ml, strength }); }
      catch (err) { toast(err.message); }
      finally { btn.disabled = false; }
    }
    // Drinks with a before-pour step (absinthe rinse, muddled mint) get a checklist first.
    function confirmThen(ml) {
      if (plan && plan.before.length) {
        overlay({ title: "First, by hand", text: plan.instructions, manual: plan.before, close: true,
                  go: () => make(ml) });
      } else {
        make(ml);
      }
    }
    $("#make").addEventListener("click", () => confirmThen(size));
    const taster = $("#taster");
    if (taster) taster.addEventListener("click", () => confirmThen(+taster.dataset.taster));
    refresh();
  }

  showStatus(window.BARTENDRO.status);
  connect();
  return { drinkPage, toast, post };
})();
