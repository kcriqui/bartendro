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

  let onOverlayClose = null;
  function overlay({ title, text = "", manual = [], spinner = false, bad = false, reset = false, close = false,
                     autoHide = 0, go = null, goLabel = "Pour", onClose = null }) {
    onOverlayClose = onClose;
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
    goBtn.textContent = goLabel;
    goBtn.onclick = go ? () => { onOverlayClose = null; hideOverlay(); go(); } : null;
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
    else if (ev.type === "stage_done") {  // e.g. absinthe poured: rinse the glass, then continue
      overlay({ title: "Your turn", text: ev.instructions || "", manual: ev.before || [], close: true,
                go: () => post("/api/continue").catch((err) => toast(err.message)),
                goLabel: "Continue", onClose: () => post("/api/cancel-pour").catch(() => {}) });
    }
    else if (ev.type === "cancelled") overlay({ title: "Cancelled", text: `${ev.name} was not finished.`,
                                               close: true, autoHide: 4000 });
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

  $("#overlay-close").addEventListener("click", () => {
    const cb = onOverlayClose;
    onOverlayClose = null;
    hideOverlay();
    if (cb) cb();
  });
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
      if (plan && plan.before.length && !plan.pre_pumps.length) {  // else: asked between the stages
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

  // --------------------------------------------------------------- admin helpers
  function filterTable(inputSel, tableSel) {  // type to hide rows (data-filter = searchable text)
    const input = $(inputSel), rows = document.querySelectorAll(`${tableSel} tr[data-filter]`);
    input.addEventListener("input", () => {
      const words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
      for (const tr of rows) tr.hidden = !words.every((w) => tr.dataset.filter.includes(w));
    });
  }

  function drinkEditor() {
    const table = $("#ing-table"), tpl = $("#row-template"), preview = $("#preview");
    const form = $("#drink-form");
    $("#add-row").addEventListener("click", () => {
      table.tBodies[0].appendChild(tpl.content.firstElementChild.cloneNode(true));
      table.querySelector("tr.ing-row:last-child input").focus();
    });
    table.addEventListener("click", (e) => {
      const b = e.target.closest(".remove-row");
      if (b) { b.closest("tr").remove(); refresh(); }
    });
    const HOW = { pump: "pumped", hand: "by hand", missing: "not available now", new: "new ingredient" };
    let timer;
    async function refresh() {
      const rows = [...table.querySelectorAll("tr.ing-row")].map((tr) => ({
        ingredient: tr.querySelector("[name=ing_name]").value, amount: tr.querySelector("[name=amount]").value,
        unit: tr.querySelector("[name=unit]").value, step: tr.querySelector("[name=step]").value,
      })).filter((r) => r.ingredient.trim());
      if (!rows.length) { preview.textContent = ""; return; }
      const size = form.querySelector("[name=size_ml]").value;
      try {
        const data = await post("/api/drink-preview", { rows, size_ml: size ? +size : null });
        preview.innerHTML = "";
        const head = document.createElement("div");
        head.textContent = `Pours ${amount(data.size_ml)}: ${data.abv}% ABV, ` +
          `${data.std_drinks} standard drink${data.std_drinks === 1 ? "" : "s"}`;
        preview.appendChild(head);
        const ul = document.createElement("ul");
        for (const l of data.lines) {
          const li = document.createElement("li");
          const qty = l.text || (l.ml == null ? "" : amount(l.ml));
          const when = l.how === "hand" ? ` (${l.step} the pour)` : "";
          li.textContent = `${l.ingredient}: ${qty} - ${HOW[l.how] || l.how}${when}`;
          if (l.how === "missing") li.className = "bad";
          ul.appendChild(li);
        }
        preview.appendChild(ul);
        for (const err of data.errors) {
          const p = document.createElement("div");
          p.className = "bad";
          p.textContent = err;
          preview.appendChild(p);
        }
      } catch (err) { preview.textContent = err.message; }
    }
    form.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(refresh, 300); });
    form.addEventListener("change", () => { clearTimeout(timer); timer = setTimeout(refresh, 100); });
    refresh();
  }

  function pumpCards() {  // bottle / calibration saved as soon as they change
    for (const card of document.querySelectorAll(".pump")) {
      const n = card.dataset.pump, bottle = card.querySelector(".pump-bottle"), cal = card.querySelector(".pump-cal");
      let last = bottle.value;
      async function save() {
        try {
          const data = await post(`/api/dispenser/${n}`, { ingredient: bottle.value, ticks_per_ml: cal.value ? +cal.value : null });
          last = bottle.value = data.ingredient;
          card.querySelector(".pump-makes").textContent = data.ingredient ? `${data.makes} drink(s)` : "";
          $("#menu-count").textContent = data.menu;
          card.classList.add("saved");
          setTimeout(() => card.classList.remove("saved"), 1500);
        } catch (err) { toast(err.message); bottle.value = last; }
      }
      bottle.addEventListener("change", save);
      cal.addEventListener("change", save);
    }
  }

  // ---------------------------------------------------------------- LED sign (party marquee)
  // A scrolling RGB dot-matrix sign in the classic 5x7 LED font (static/led-font.js, from the
  // Adafruit GFX Library), doubled with Scale2x: every font pixel becomes 2x2 LEDs and the steps
  // on diagonals and curves are filled in, so letters come out rounder. Every lit LED is coloured
  // from a rainbow that drifts along as the text moves.
  function scale2x(src, h) {                  // src: one bit-column per x (bit y); h rows -> 2h rows
    const at = (x, y) => (x >= 0 && x < src.length && y >= 0 && y < h ? (src[x] >> y) & 1 : 0);
    const out = new Array(src.length * 2).fill(0);
    for (let x = 0; x < src.length; x++) {
      for (let y = 0; y < h; y++) {
        const p = at(x, y), a = at(x, y - 1), b = at(x + 1, y), c = at(x - 1, y), d = at(x, y + 1);
        const e = [p, p, p, p];               // top-left, top-right, bottom-left, bottom-right
        if (c === a && c !== d && a !== b) e[0] = a;
        if (a === b && a !== c && b !== d) e[1] = b;
        if (d === c && d !== b && c !== a) e[2] = c;
        if (b === d && b !== a && d !== c) e[3] = d;
        out[2 * x] |= (e[0] << (2 * y)) | (e[2] << (2 * y + 1));
        out[2 * x + 1] |= (e[1] << (2 * y)) | (e[3] << (2 * y + 1));
      }
    }
    return out;
  }
  function ledSign(canvas) {
    const ROWS = 16, PAD = 1;                  // the font's 8 rows (7 + descenders) doubled, a dark row above/below
    const font = window.LED_FONT_5X7 || "";
    const glyph = (ch) => {
      let code = ch.charCodeAt(0);
      if (code < 32 || code > 126) code = 63;  // "?" for anything the font doesn't have
      const at = (code - 32) * 10;
      return [0, 1, 2, 3, 4].map((i) => parseInt(font.substr(at + i * 2, 2), 16) || 0);
    };
    const small = [];                           // one byte per column of the whole message
    for (const ch of canvas.dataset.text || "") small.push(...glyph(ch), 0);
    const columns = scale2x(small, 8);          // ...and at double resolution
    const ctx = canvas.getContext("2d");
    const slow = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let cols = 0, pitch = 0, step = 0, last = 0;

    const unlit = document.createElement("canvas");   // the dark LEDs, drawn once per size

    function size() {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = unlit.width = Math.round(canvas.clientWidth * dpr);
      canvas.height = unlit.height = Math.round(canvas.clientHeight * dpr);
      pitch = canvas.height / (ROWS + 2 * PAD);
      cols = Math.floor(canvas.width / pitch);
      const u = unlit.getContext("2d");
      u.fillStyle = "#0b0b0f";
      u.fillRect(0, 0, unlit.width, unlit.height);
      u.fillStyle = "#1b1b22";
      u.beginPath();
      for (let c = 0; c < cols; c++) {
        for (let row = 0; row < ROWS + 2 * PAD; row++) {
          u.moveTo(x0() + c * pitch + pitch * 0.4, (row + 0.5) * pitch);
          u.arc(x0() + c * pitch, (row + 0.5) * pitch, pitch * 0.4, 0, 2 * Math.PI);
        }
      }
      u.fill();
    }
    const x0 = () => (canvas.width - cols * pitch) / 2 + pitch / 2;
    function draw() {                          // the dark board, then only the lit LEDs on top
      const loop = columns.length + cols;       // the text, then a blank screen's width
      ctx.drawImage(unlit, 0, 0);
      const r = pitch * 0.4, left = x0();
      for (let c = 0; c < cols; c++) {
        const sx = ((step + c) % loop) - cols;   // message column under this LED (< 0: the gap)
        const bits = sx >= 0 ? columns[sx] : 0;
        if (!bits) continue;
        ctx.fillStyle = `hsl(${(sx * 3 - step * 3) % 360}, 100%, 62%)`;
        ctx.beginPath();
        for (let row = 0; row < ROWS; row++) {
          if (!((bits >> row) & 1)) continue;
          const x = left + c * pitch, y = (row + PAD + 0.5) * pitch;
          ctx.moveTo(x + r, y);
          ctx.arc(x, y, r, 0, 2 * Math.PI);
        }
        ctx.fill();
      }
    }
    function frame(t) {                        // redraw only when the text moves (easy on a Pi)
      if (t - last > (slow ? 120 : 35)) { step++; last = t; draw(); }
      requestAnimationFrame(frame);
    }
    size();
    window.addEventListener("resize", () => { size(); draw(); });
    requestAnimationFrame(frame);              // starts blank; the text comes in from the right
  }
  document.querySelectorAll("canvas.led-sign").forEach(ledSign);

  showStatus(window.BARTENDRO.status);
  connect();
  return { drinkPage, drinkEditor, filterTable, pumpCards, toast, post };
})();
