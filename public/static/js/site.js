// decomp.fun: theme toggle, tooltips, relative times and the project directory filters.
(() => {
  // ------------------------------------------------------------ theme
  const root = document.documentElement;
  const toggle = document.getElementById("theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", (ev) => {
      const current = root.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      const next = current === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      toggle.setAttribute("aria-pressed", String(next === "dark"));
      try {
        localStorage.setItem("theme", next);
      } catch {}
      document.dispatchEvent(new CustomEvent("themechange"));
    });
  }

  // ------------------------------------------------------------ tooltips
  let tip = null;
  let owner = null;
  const show = (el) => {
    const text = el.getAttribute("data-tip");
    if (!text) return;
    if (!tip) {
      tip = document.createElement("div");
      tip.id = "tip";
      document.body.appendChild(tip);
    }
    tip.textContent = text;
    tip.classList.add("show");
    const r = el.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    let left = r.left + window.scrollX + (r.width - tip.offsetWidth) / 2;
    left = Math.max(0, Math.min(left, vw - tip.offsetWidth));
    let top = r.top + window.scrollY - tip.offsetHeight - 6;
    tip.classList.toggle("below", top < window.scrollY);
    if (top < window.scrollY) top = r.bottom + window.scrollY + 6;
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
    tip.style.setProperty("--arrow", `${r.left + window.scrollX + r.width / 2 - left}px`);
    owner = el;
  };
  const hide = (el) => {
    if (el === owner && tip) tip.classList.remove("show");
    owner = null;
  };
  const target = (ev) => (ev.target instanceof Element ? ev.target.closest("[data-tip]") : null);
  document.addEventListener("mouseover", (ev) => target(ev) && show(target(ev)), { passive: true });
  document.addEventListener("mouseout", (ev) => target(ev) && hide(target(ev)), { passive: true });
  document.addEventListener("touchstart", (ev) => target(ev) && show(target(ev)), { passive: true });
  document.addEventListener("touchend", (ev) => target(ev) && hide(target(ev)), { passive: true });

  // Bars on cards show a tooltip on hover; a click still opens the card.
  document.addEventListener("click", (ev) => {
    const tipEl = target(ev);
    const card = tipEl && !tipEl.closest("a") ? tipEl.closest(".project") : null;
    const link = card && card.querySelector(".project-link");
    if (link) location.href = link.href;
  });

  // ------------------------------------------------------------ relative times
  const steps = [
    ["year", 31536000],
    ["month", 2592000],
    ["week", 604800],
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];
  const ago = (secs) => {
    for (const [unit, size] of steps) {
      if (secs >= size) {
        const n = Math.floor(secs / size);
        return `${n} ${unit}${n === 1 ? "" : "s"} ago`;
      }
    }
    return "just now";
  };
  const now = Date.now() / 1000;
  document.querySelectorAll("time[data-ago]").forEach((t) => {
    const ts = Date.parse(t.getAttribute("datetime")) / 1000;
    if (!Number.isNaN(ts)) t.textContent = ago(Math.max(0, Math.floor(now - ts)));
  });

  // ------------------------------------------------------------ directory
  const list = document.getElementById("directory");
  if (!list) return;
  const cards = Array.from(list.querySelectorAll("article.project"));
  const boxes = Array.from(document.querySelectorAll('.plat-item input[type="checkbox"]'));
  const platSummary = document.querySelector(".plat-list summary");
  const sortSummary = document.querySelector(".sort-list summary");
  const search = document.getElementById("search");
  const counter = document.getElementById("result-count");

  const sorts = {
    updated: ["Recently updated", (a, b) => b.dataset.updated.localeCompare(a.dataset.updated)],
    name: ["Name", (a, b) => a.dataset.name.localeCompare(b.dataset.name)],
    percent: ["Most decompiled (%)", (a, b) => +b.dataset.percent - +a.dataset.percent],
    matched: ["Most code decompiled", (a, b) => +b.dataset.matched - +a.dataset.matched],
    size: ["Largest games", (a, b) => +b.dataset.size - +a.dataset.size],
  };

  const params = new URL(location.href).searchParams;
  let sort = sorts[params.get("sort")] ? params.get("sort") : "updated";
  const wanted = (params.get("platform") || "").split(",").filter(Boolean);
  if (wanted.length) boxes.forEach((b) => (b.checked = wanted.includes(b.value)));
  if (search) search.value = params.get("q") || "";

  const apply = () => {
    const on = boxes.filter((b) => b.checked).map((b) => b.value);
    const all = on.length === boxes.length || on.length === 0;
    const q = (search?.value || "").trim().toLowerCase();

    let shown = 0;
    for (const c of cards.sort(sorts[sort][1])) {
      const ok = (all || on.includes(c.dataset.platform)) && (!q || c.dataset.search.includes(q));
      c.classList.toggle("hidden", !ok);
      if (ok) shown++;
      list.appendChild(c);
    }

    if (platSummary) {
      platSummary.textContent = all
        ? "All systems"
        : on.length === 1
          ? boxes.find((b) => b.checked).closest("label").querySelector(".plat-name").textContent
          : `${on.length} systems`;
    }
    if (sortSummary) sortSummary.textContent = sorts[sort][0];
    if (counter) counter.textContent = `${shown} project${shown === 1 ? "" : "s"}`;

    const url = new URL(location.href);
    all ? url.searchParams.delete("platform") : url.searchParams.set("platform", on.join(","));
    sort === "updated" ? url.searchParams.delete("sort") : url.searchParams.set("sort", sort);
    q ? url.searchParams.set("q", q) : url.searchParams.delete("q");
    history.replaceState(null, "", url.toString().replace(/%2C/g, ","));
  };

  boxes.forEach((b) => b.addEventListener("change", apply));
  document.querySelectorAll(".plat-item button").forEach((btn) =>
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      const mine = btn.closest(".plat-item").querySelector("input");
      const onlyMine = mine.checked && boxes.every((b) => b === mine || !b.checked);
      boxes.forEach((b) => (b.checked = onlyMine ? true : b === mine));
      apply();
    }),
  );
  document.querySelectorAll(".plat-list a[data-all]").forEach((a) =>
    a.addEventListener("click", (ev) => {
      ev.preventDefault();
      boxes.forEach((b) => (b.checked = true));
      a.closest("details").open = false;
      apply();
    }),
  );
  document.querySelectorAll(".sort-list a[data-sort]").forEach((a) =>
    a.addEventListener("click", (ev) => {
      ev.preventDefault();
      sort = a.dataset.sort;
      a.closest("details").open = false;
      apply();
    }),
  );
  search?.addEventListener("input", apply);
  list.addEventListener("click", (ev) => {
    const badge = ev.target instanceof Element ? ev.target.closest("a.plat") : null;
    if (!badge) return;
    ev.preventDefault();
    boxes.forEach((b) => (b.checked = b.value === badge.dataset.p));
    apply();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
  apply();
})();
