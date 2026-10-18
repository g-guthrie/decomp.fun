// decomp.fun progress history chart (uPlot).
(() => {
  const el = document.getElementById("chart");
  if (!el || !window.uPlot) return;

  fetch(el.dataset.src)
    .then((r) => r.json())
    .then((points) => {
      if (!points.length) {
        el.textContent = "No history recorded yet.";
        return;
      }
      const pick = (k) => points.map((p) => (p.measures[k] ?? null));
      const data = [
        points.map((p) => Date.parse(p.timestamp) / 1000),
        pick("matched_code_percent"),
        pick("fuzzy_match_percent"),
        pick("complete_code_percent"),
        pick("matched_data_percent"),
      ];
      const pct = (u, v) => (v == null ? "–" : `${v.toFixed(2)}%`);
      const axis = { stroke: "#a9a9b3", grid: { stroke: "rgba(128,128,128,0.12)" }, ticks: { stroke: "rgba(128,128,128,0.2)" } };
      const size = () => ({ width: el.clientWidth, height: 400 });
      const plot = new uPlot(
        {
          ...size(),
          scales: { x: { time: true }, y: { range: [0, 100] } },
          series: [
            {},
            { label: "Decompiled", stroke: "#29c45a", width: 2, value: pct, spanGaps: true },
            { label: "Including partly decompiled", stroke: "#3d8bfd", width: 2, value: pct, spanGaps: true, show: false },
            { label: "Fully linked", stroke: "#f0a020", width: 2, value: pct, spanGaps: true, show: false },
            { label: "Data decompiled", stroke: "#c060e0", width: 2, value: pct, spanGaps: true, show: false },
          ],
          axes: [axis, { ...axis, values: (u, ticks) => ticks.map((t) => `${t}%`) }],
        },
        data,
        el,
      );
      window.addEventListener("resize", () => plot.setSize(size()));
    });
})();
