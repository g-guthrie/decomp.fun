// decomp.fun progress history chart (uPlot). Colours follow the page theme.
(() => {
  const el = document.getElementById("chart");
  if (!el) return;
  const status = (text) => {
    el.innerHTML = "";
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = text;
    el.append(p);
  };
  if (!window.uPlot) {
    status("The chart couldn't load. The data is still available as history.json.");
    return;
  }

  const css = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;

  fetch(el.dataset.src)
    .then((r) => {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    })
    .then((points) => {
      if (!points.length) {
        status("No history recorded yet.");
        return;
      }
      const pick = (k) => points.map((p) => p.measures[k] ?? null);
      const data = [
        points.map((p) => Date.parse(p.timestamp) / 1000),
        pick("matched_code_percent"),
        pick("fuzzy_match_percent"),
        pick("complete_code_percent"),
        pick("matched_data_percent"),
      ];
      const pct = (u, v) => (v == null ? "–" : `${v.toFixed(2)}%`);
      const shown = [true, true, false, true, false];
      const size = () => ({ width: el.clientWidth, height: Math.min(400, Math.max(260, el.clientWidth * 0.6)) });
      let plot = null;

      const draw = () => {
        if (plot) {
          plot.series.forEach((s, i) => (shown[i] = s.show));
          plot.destroy();
        }
        el.innerHTML = "";
        const text = css("--chart-text", "#888");
        const grid = css("--chart-grid", "rgba(128,128,128,0.15)");
        const axis = { stroke: text, grid: { stroke: grid }, ticks: { stroke: grid } };
        plot = new uPlot(
          {
            ...size(),
            scales: { x: { time: true }, y: { range: [0, 100] } },
            series: [
              {},
              { label: "Decompiled", stroke: css("--chart-1", "#1f9d55"), width: 2, value: pct, spanGaps: true, show: shown[1] },
              { label: "Including partly decompiled", stroke: css("--chart-2", "#3d8bfd"), width: 2, value: pct, spanGaps: true, show: shown[2] },
              { label: "Fully linked", stroke: css("--chart-3", "#d48806"), width: 2, value: pct, spanGaps: true, show: shown[3] },
              { label: "Data decompiled", stroke: css("--chart-4", "#a24bd0"), width: 2, value: pct, spanGaps: true, show: shown[4] },
            ],
            axes: [axis, { ...axis, values: (u, ticks) => ticks.map((t) => `${t}%`) }],
          },
          data,
          el,
        );
      };
      draw();
      document.addEventListener("themechange", draw);
      window.addEventListener("resize", () => plot && plot.setSize(size()));
    })
    .catch(() => status("Couldn't load the history. Try reloading the page."));
})();
