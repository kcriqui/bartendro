// The /sim page: draws the simulated dispensers and animates their LEDs and rotors.
// State comes from the WebSocket /ws/sim (sent when something changes, ~10 per second at most).
(() => {
  "use strict";
  const grid = document.getElementById("pumps");
  const stateEl = document.getElementById("state");
  const controls = grid.dataset.controls === "1";
  const LEVELS = { Full: 200, Low: 100, Out: 50 };  // the sim's sensor readings (thresholds 120 / 75)

  // The dispenser firmware's LED patterns (firmware/dispenser/led.c): [[r, g, b], steps] segments,
  // each fading to the next. One step per 10 sync pulses of 1.024 ms = 10.24 ms.
  const STEP_MS = 10.24;
  const PATTERNS = {
    idle: [[[255, 0, 0], 100], [[0, 255, 0], 100], [[0, 0, 255], 100]],
    dispense: [[[255, 0, 0], 50], [[0, 0, 255], 50]],
    done: [[[0, 255, 0], 75], [[0, 90, 0], 75]],
    clean: [[[150, 14, 235], 40], [[85, 8, 133], 40], [[85, 64, 64], 40], [[255, 128, 0], 40]],
    current_sense: [[[255, 0, 0], 15], [[255, 0, 0], 0], [[0, 0, 0], 15], [[0, 0, 0], 0]],
  };
  const CYCLE = {};
  for (const [name, segs] of Object.entries(PATTERNS)) CYCLE[name] = segs.reduce((n, s) => n + Math.max(s[1], 1), 0);

  function ledColor(pattern, ms) {
    const segs = PATTERNS[pattern];
    if (!segs) return [0, 0, 0];  // off
    let step = Math.floor(ms / STEP_MS) % CYCLE[pattern];
    for (let i = 0; i < segs.length; i++) {
      const [from, steps] = segs[i];
      const len = Math.max(steps, 1);
      if (step < len) {
        if (!steps) return from;  // a 0-step segment shows its colour for one step
        const to = segs[(i + 1) % segs.length][0];
        return from.map((c, k) => Math.round(c + (to[k] - c) * step / steps));
      }
      step -= len;
    }
    return [0, 0, 0];
  }

  const SVG = `<svg viewBox="0 0 100 100" aria-hidden="true">
    <circle class="led" cx="50" cy="50" r="46" fill="none" stroke-width="5"/>
    <circle cx="50" cy="50" r="40" fill="#2b2f35" stroke="#454b53" stroke-width="2"/>
    <path d="M14 88 L14 50 A36 36 0 0 1 86 50 L86 88" fill="none" stroke="#c9b48a" stroke-width="5" opacity=".75"/>
    <g class="rotor">
      <circle cx="50" cy="50" r="9" fill="#666d76"/>
      <path d="M50 50 L50 23 M50 50 L73.4 63.5 M50 50 L26.6 63.5" stroke="#666d76" stroke-width="5"/>
      <circle cx="50" cy="23" r="7" fill="#9aa2ac"/><circle cx="73.4" cy="63.5" r="7" fill="#9aa2ac"/>
      <circle cx="26.6" cy="63.5" r="7" fill="#9aa2ac"/>
    </g>
  </svg>`;

  const cards = new Map();  // number -> {el, parts, data, ledSince, angle}

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function post(url, body) {
    fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                 body: body ? JSON.stringify(body) : undefined }).catch(() => {});
  }

  function makeCard(p) {
    const card = el("section", "pump");
    const top = el("div", "top");
    const bottle = el("span", "bottle");
    const badges = el("span", "badges");
    top.append(el("span", "num", "#" + p.number), bottle, badges);
    const body = el("div", "body");
    body.insertAdjacentHTML("beforeend", SVG);
    const fig = el("div", "figures");
    const run = el("div");
    const runBar = el("div", "bar");
    runBar.append(el("i"));
    const total = el("div");
    const levelBar = el("div", "bar level");
    levelBar.append(el("i"), el("span", "mark"), el("span", "mark"));
    fig.append(run, runBar, total, levelBar);
    body.append(fig);
    card.append(top, body);
    let jam = null;
    if (controls) {
      const c = el("div", "controls");
      jam = el("button", "", "Jam");
      jam.title = "Stall this pump when it runs (current sense)";
      jam.onclick = () => post(`/api/sim/${p.number}/jam`);
      c.append(jam);
      for (const [label, value] of Object.entries(LEVELS)) {
        const b = el("button", "", label);
        b.title = `Liquid level sensor reads ${value}`;
        b.onclick = () => post(`/api/sim/${p.number}/level`, { level: value });
        c.append(b);
      }
      fig.append(c);
    }
    grid.append(card);
    const svg = body.querySelector("svg");
    return { el: card, data: null, ledSince: performance.now(), angle: 0,
             parts: { bottle, run, runBar, total, levelBar, badges, jam,
                      led: svg.querySelector(".led"), rotor: svg.querySelector(".rotor") } };
  }

  const ml = (ticks, tpm) => (ticks / tpm).toFixed(1);

  function update(p) {
    let c = cards.get(p.number);
    if (!c) { c = makeCard(p); cards.set(p.number, c); }
    if (!c.data || c.data.led !== p.led) c.ledSince = performance.now();  // patterns restart on a change
    c.data = p;
    const q = c.parts;
    q.bottle.textContent = p.bottle || "empty";
    q.bottle.classList.toggle("empty", !p.bottle);
    c.el.classList.toggle("running", p.running);
    c.el.classList.toggle("stalled", p.stalled);
    if (p.pour_ml != null) {  // in the last pour: progress through all of it (pulsed = several bursts)
      const done = p.pour_ticks / p.ticks_per_ml;
      q.run.innerHTML = `pour <b>${done.toFixed(1)}</b> / ${p.pour_ml.toFixed(1)} ml`;
      q.runBar.firstChild.style.width = Math.min(100, 100 * done / p.pour_ml) + "%";
    } else if (p.run_target) {
      q.run.innerHTML = `run <b>${ml(p.run_ticks, p.ticks_per_ml)}</b> / ${ml(p.run_target, p.ticks_per_ml)} ml`;
      q.runBar.firstChild.style.width = Math.min(100, 100 * p.run_ticks / p.run_target) + "%";
    } else {
      q.run.innerHTML = p.run_ticks ? `run <b>${ml(p.run_ticks, p.ticks_per_ml)}</b> ml (timed)` : "run -";
      q.runBar.firstChild.style.width = p.running ? "100%" : "0";
    }
    const full = Math.max(250, p.level);
    const levelState = p.level <= p.out ? "out" : p.level <= p.low ? "low" : "";
    q.total.innerHTML = `total <b>${ml(p.ticks, p.ticks_per_ml)}</b> ml · level ${p.level}`
      + (levelState ? ` <b class="${levelState}">${levelState}</b>` : "");
    q.levelBar.className = "bar level " + levelState;
    q.levelBar.children[0].style.width = (100 * p.level / full) + "%";
    q.levelBar.children[1].style.left = (100 * p.out / full) + "%";
    q.levelBar.children[2].style.left = (100 * p.low / full) + "%";
    q.badges.replaceChildren();
    const badge = (text, cls) => q.badges.append(el("span", "badge " + (cls || ""), text));
    if (p.running) badge(p.reverse ? "reverse" : "pumping", "on");
    if (p.stalled) badge("STALLED", "bad");
    if (p.jammed) badge("jammed", "warn");
    if (p.id_conflict) badge("id conflict", "bad");
    if (q.jam) q.jam.classList.toggle("on", p.jammed);
  }

  function showStatus(s) {
    stateEl.className = "state " + s.state;
    stateEl.textContent = s.state.replace(/_/g, " ") + (s.pouring ? `: ${s.pouring}` : "")
      + (s.message && s.message !== s.pouring ? ` - ${s.message}` : "");
  }

  // LEDs and rotors, every frame.
  let last = performance.now();
  function frame(now) {
    const dt = now - last;
    last = now;
    for (const c of cards.values()) {
      if (!c.data) continue;
      const [r, g, b] = ledColor(c.data.led, now - c.ledSince);
      const color = `rgb(${r},${g},${b})`;
      c.parts.led.setAttribute("stroke", color);
      c.parts.led.style.filter = r + g + b ? `drop-shadow(0 0 4px ${color})` : "none";
      if (c.data.running) {
        c.angle = (c.angle + (c.data.reverse ? -1 : 1) * dt * 0.3) % 360;  // ~1 turn / 1.2 s
        c.parts.rotor.setAttribute("transform", `rotate(${c.angle.toFixed(1)} 50 50)`);
      }
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  function connect() {
    const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/sim");
    ws.onmessage = (m) => {
      const s = JSON.parse(m.data);
      showStatus(s.status);
      s.pumps.forEach(update);
    };
    ws.onclose = () => {
      stateEl.className = "state offline";
      stateEl.textContent = "disconnected - retrying...";
      setTimeout(connect, 2000);
    };
  }
  connect();
})();
