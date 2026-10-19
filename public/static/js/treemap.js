// decomp.fun unit treemap. Layout is precomputed by build.py (normalised 0..1 rects).
(() => {
  const canvas = document.getElementById("treemap");
  if (!canvas) return;
  const input = document.getElementById("unit-filter");
  const ctx = canvas.getContext("2d");
  let units = [];
  let hover = null;
  let base = null; // offscreen render of all cells

  const fmtSize = (n) => {
    const u = ["B", "kB", "MB", "GB"];
    let i = 0;
    while (n >= 1000 && i < u.length - 1) {
      n /= 1000;
      i++;
    }
    return `${n.toFixed(2)} ${u[i]}`;
  };
  const fmtPct = (p) => `${(p === 0 || p === 100 ? p : Math.min(Math.max(p, 0.01), 99.99)).toFixed(2)}%`;

  // Colours come from CSS (--tm-*), so they follow the light/dark theme: untouched code is the
  // "0%" colour, shading to the "99%" colour as the fuzzy match improves; exact matches are "100%".
  const status = document.getElementById("treemap-status");
  const setStatus = (text) => {
    if (!status) return;
    status.textContent = text;
    status.hidden = !text;
  };
  let pal = null;
  const rgb = (c) => {
    const m = c.trim().match(/^#([0-9a-f]{6})$/i);
    if (!m) return [128, 128, 128];
    const n = parseInt(m[1], 16);
    return [n >> 16, (n >> 8) & 255, n & 255];
  };
  const readPalette = () => {
    const st = getComputedStyle(document.documentElement);
    const v = (k, d) => st.getPropertyValue(k).trim() || d;
    pal = {
      lo: rgb(v("--tm-0", "#3a3f4b")),
      hi: rgb(v("--tm-99", "#2f7fd8")),
      done: v("--tm-100", "#29c45a"),
      bg: v("--tm-bg", "#14171e"),
      line: v("--tm-line", "#0b0d11"),
      hover: v("--tm-hover", "#ffffff"),
    };
  };
  const shade = (c, k) => `rgb(${c.map((x) => Math.round(x * k)).join(",")})`;
  const fill = (u) => {
    if (u.p >= 100) return [pal.done, pal.done];
    const t = u.p / 100;
    const c = pal.lo.map((a, i) => a + (pal.hi[i] - a) * t);
    return [shade(c, 1), shade(c, 0.82)];
  };

  const rect = (u, w, h) => [u.x * w, u.y * h, u.w * w, u.h * h];

  const renderBase = (w, h, dpr) => {
    base = document.createElement("canvas");
    base.width = Math.round(w * dpr);
    base.height = Math.round(h * dpr);
    const b = base.getContext("2d");
    b.scale(dpr, dpr);
    b.fillStyle = pal.bg;
    b.fillRect(0, 0, w, h);
    b.strokeStyle = pal.line;
    b.lineWidth = 1;
    for (const u of units) {
      const [x, y, rw, rh] = rect(u, w, h);
      const [c0, c1] = fill(u);
      const g = b.createRadialGradient(x + rw * 0.4, y + rh * 0.4, (rw + rh) * 0.1, x + rw * 0.4, y + rh * 0.4, (rw + rh) * 0.5);
      g.addColorStop(0, c0);
      g.addColorStop(1, c1);
      b.globalAlpha = u.hidden ? 0.08 : 1;
      b.fillStyle = g;
      b.fillRect(x, y, rw, rh);
      b.globalAlpha = 1;
      if (rw > 2 && rh > 2) b.strokeRect(x + 0.5, y + 0.5, rw - 1, rh - 1);
    }
  };

  const label = (u, w, h) => {
    const text = `${u.n} • ${fmtSize(u.s)} • ${fmtPct(u.p)}`;
    const style = getComputedStyle(document.documentElement);
    ctx.font = `14px ${style.getPropertyValue("--pico-font-family") || "sans-serif"}`;
    let t = text;
    while (ctx.measureText(t).width + 20 > w && t.length > 8) t = `${t.slice(0, t.length - 2)}…`;
    const tw = ctx.measureText(t).width + 20;
    const th = 26;
    const [x, y, rw, rh] = rect(u, w, h);
    let lx = Math.min(Math.max(x + rw / 2 - tw / 2, 0), w - tw);
    let ly = y - th - 6;
    if (ly < 0) ly = Math.min(y + rh + 6, h - th);
    ctx.fillStyle = style.getPropertyValue("--pico-contrast-background") || "#fff";
    ctx.beginPath();
    ctx.roundRect(lx, ly, tw, th, 5);
    ctx.fill();
    ctx.fillStyle = style.getPropertyValue("--pico-contrast-inverse") || "#000";
    ctx.textBaseline = "middle";
    ctx.fillText(t, lx + 10, ly + th / 2);
  };

  let dirty = true;
  const draw = () => {
    const { width: w, height: h } = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const pw = Math.round(w * dpr);
    const ph = Math.round(h * dpr);
    if (canvas.width !== pw || canvas.height !== ph) {
      canvas.width = pw;
      canvas.height = ph;
      dirty = true;
    }
    if (!pal) readPalette();
    if (dirty || !base) {
      renderBase(w, h, dpr);
      dirty = false;
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(base, 0, 0);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (hover) {
      const [x, y, rw, rh] = rect(hover, w, h);
      ctx.strokeStyle = pal.hover;
      ctx.lineWidth = 2;
      ctx.strokeRect(x, y, rw, rh);
      label(hover, w, h);
    }
  };
  const schedule = () => requestAnimationFrame(draw);

  const hit = (cx, cy) => {
    const r = canvas.getBoundingClientRect();
    const x = (cx - r.left) / r.width;
    const y = (cy - r.top) / r.height;
    return units.find((u) => !u.hidden && x >= u.x && x <= u.x + u.w && y >= u.y && y <= u.y + u.h) || null;
  };
  const move = (cx, cy) => {
    const u = hit(cx, cy);
    if (u !== hover) {
      hover = u;
      schedule();
    }
  };
  canvas.addEventListener("mousemove", (e) => move(e.clientX, e.clientY));
  canvas.addEventListener("mouseleave", () => {
    hover = null;
    schedule();
  });
  canvas.addEventListener("touchmove", (e) => move(e.touches[0].clientX, e.touches[0].clientY), { passive: true });

  // Filter: words match unit names; ">50%", "<10%", ">4kb", "linked", "unlinked".
  const sizeUnits = { b: 1, kb: 1e3, mb: 1e6 };
  const test = (tok, u) => {
    if (tok === "linked") return u.l;
    if (tok === "unlinked") return !u.l;
    const m = tok.match(/^(>=|<=|>|<|=)(\d+(?:\.\d+)?)(%|b|kb|mb)$/);
    if (!m) return u.n.toLowerCase().includes(tok);
    const value = m[3] === "%" ? u.p : u.s;
    const want = m[3] === "%" ? +m[2] : +m[2] * sizeUnits[m[3]];
    return { ">": value > want, "<": value < want, ">=": value >= want, "<=": value <= want, "=": value === want }[m[1]];
  };
  const filter = () => {
    const toks = (input?.value || "").toLowerCase().split(/\s+/).filter(Boolean);
    for (const u of units) u.hidden = !toks.every((t) => test(t, u));
    dirty = true;
    hover = null;
    schedule();
  };
  input?.addEventListener("input", filter);

  document.addEventListener("themechange", () => {
    readPalette();
    dirty = true;
    schedule();
  });

  new ResizeObserver(schedule).observe(canvas);
  fetch(canvas.dataset.src)
    .then((r) => {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    })
    .then((data) => {
      units = data;
      setStatus("");
      filter();
    })
    .catch(() => setStatus("Couldn't load the source files. Try reloading the page."));
})();
