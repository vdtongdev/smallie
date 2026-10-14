// Chart cards for the server's chart specs (app/agents/chart/spec.py), drawn with Chart.js.
// Series colours are the 8 categorical palette slots in style.css (--series-1..8), in fixed
// order and never cycled (the server caps series and pie slices at 8); text uses text tokens.
// Light and dark steps come from CSS, so charts are rebuilt when the colour scheme changes.

const chartCards = []; // {canvas, spec, chart}

const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const seriesColor = (i) => cssVar(`--series-${i + 1}`);
const washed = (hex) => `${hex}1a`; // ~10% opacity: area fills are a wash, never a block

function formatValue(value, compact = false) {
  if (value == null) return "–";
  const locale = state.lang === "vi" ? "vi-VN" : "en-US";
  return new Intl.NumberFormat(locale, compact
    ? { notation: "compact", maximumFractionDigits: 1 }
    : { maximumFractionDigits: 2 }).format(value);
}

// A vertical hairline at the hovered x, so line readers aim at a date, not at a 2px line.
const crosshair = {
  id: "crosshair",
  afterDatasetsDraw(chart) {
    const active = chart.tooltip?.getActiveElements?.() || [];
    if (!active.length) return;
    const { ctx, chartArea } = chart;
    ctx.save();
    ctx.strokeStyle = cssVar("--muted");
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(active[0].element.x, chartArea.top);
    ctx.lineTo(active[0].element.x, chartArea.bottom);
    ctx.stroke();
    ctx.restore();
  },
};

// Paint the card's surface behind the chart, so a downloaded PNG isn't transparent.
const surfaceFill = {
  id: "surfaceFill",
  beforeDraw(chart) {
    const { ctx, width, height } = chart;
    ctx.save();
    ctx.fillStyle = cssVar("--surface");
    ctx.fillRect(0, 0, width, height);
    ctx.restore();
  },
};

function chartConfig(spec) {
  const surface = cssVar("--surface");
  const text = cssVar("--text");
  const muted = cssVar("--muted");
  const grid = cssVar("--border");
  const many = spec.labels.length > 24;
  const isLine = spec.type === "line" || spec.type === "area";

  let datasets;
  if (spec.type === "pie") {
    datasets = [{
      label: spec.series[0].name,
      data: spec.series[0].values,
      backgroundColor: spec.labels.map((_, i) => seriesColor(i)),
      borderColor: surface, // the 2px surface gap between slices
      borderWidth: 2,
    }];
  } else {
    datasets = spec.series.map((series, i) => {
      const color = seriesColor(i);
      const common = { label: series.name, borderColor: color, backgroundColor: color };
      if (spec.type === "bar") {
        return { ...common, data: series.values, maxBarThickness: 24, borderRadius: 4, borderSkipped: "start" };
      }
      if (spec.type === "scatter") {
        return {
          ...common, data: spec.labels.map((x, j) => ({ x, y: series.values[j] })),
          pointRadius: 4, pointHoverRadius: 5, pointBorderColor: surface, pointBorderWidth: 2, pointHitRadius: 12,
        };
      }
      return {
        ...common, data: series.values, borderWidth: 2, borderJoinStyle: "round", borderCapStyle: "round",
        fill: spec.type === "area" ? "origin" : false, backgroundColor: spec.type === "area" ? washed(color) : color,
        pointRadius: many ? 0 : 3, pointHoverRadius: 4, pointBorderColor: surface, pointBorderWidth: 2,
        pointHitRadius: 12, tension: 0, spanGaps: true,
      };
    });
  }

  const axis = (title) => ({
    grid: { color: grid, lineWidth: 1 },
    border: { display: false },
    ticks: { color: muted },
    title: { display: !!title, text: title, color: muted },
  });
  const scales = spec.type === "pie" ? {} : {
    x: {
      ...axis(spec.x_label),
      type: spec.type === "scatter" ? "linear" : "category",
      grid: { display: false },
      ticks: { color: muted, autoSkip: true, maxTicksLimit: 8, maxRotation: 0 },
    },
    y: {
      ...axis(spec.y_label),
      beginAtZero: spec.type !== "line" && spec.type !== "scatter",
      ticks: { color: muted, callback: (v) => formatValue(v, true) },
    },
  };

  return {
    type: { area: "line", pie: "pie", bar: "bar", scatter: "scatter" }[spec.type] || "line",
    data: { labels: spec.type === "scatter" ? undefined : spec.labels, datasets },
    plugins: isLine ? [surfaceFill, crosshair] : [surfaceFill],
    options: {
      responsive: true,
      maintainAspectRatio: true,
      aspectRatio: spec.type === "pie" ? 2.2 : 2,
      animation: { duration: 250 },
      // Lines: the whole x position is the target; bars, dots and slices: the mark itself.
      interaction: isLine ? { mode: "index", intersect: false } : { mode: "nearest", intersect: true },
      scales,
      plugins: {
        // One series needs no legend (the title names it); a pie always lists its slices, at its side.
        // Keys mirror the mark: a short line for lines, a small square for bars, dots and slices,
        // always in the series colour (an area's fill is only a 10% wash).
        legend: {
          display: spec.type === "pie" || spec.series.length > 1,
          position: spec.type === "pie" ? "right" : "top",
          labels: {
            color: text,
            boxWidth: isLine ? 16 : 10,
            boxHeight: isLine ? 2 : 10,
            // Pies list their slices with Chart.js' own pie labels; line keys take the line colour.
            ...(isLine && {
              generateLabels: (chart) => Chart.defaults.plugins.legend.labels.generateLabels(chart)
                .map((item) => ({ ...item, fillStyle: item.strokeStyle, lineWidth: 0 })),
            }),
          },
        },
        tooltip: {
          callbacks: {
            label: (item) => {
              const value = spec.type === "scatter" ? item.parsed.y : item.raw;
              const name = spec.type === "pie" ? item.label : item.dataset.label;
              return ` ${formatValue(value)}  ${name}`;
            },
          },
        },
      },
    },
  };
}

function dataTable(spec) {
  const table = document.createElement("table");
  const head = table.insertRow();
  for (const name of [spec.x_label || "", ...spec.series.map((s) => s.name)]) {
    const th = document.createElement("th");
    th.textContent = name;
    head.append(th);
  }
  spec.labels.forEach((label, i) => {
    const row = table.insertRow();
    row.insertCell().textContent = label;
    for (const series of spec.series) row.insertCell().textContent = formatValue(series.values[i]);
  });
  return table;
}

// A chart card, or null when this chart is already on the page (a nested tool result and the
// orchestrator's answer can carry the same chart).
function chartCard(spec) {
  if (document.querySelector(`.chart-card[data-chart-id="${CSS.escape(spec.id)}"]`)) return null;
  const card = document.createElement("figure");
  card.className = "chart-card";
  card.dataset.chartId = spec.id;

  const title = document.createElement("figcaption");
  title.className = "chart-title";
  title.textContent = spec.title;
  const frame = document.createElement("div");
  frame.className = "chart-frame";
  const canvas = document.createElement("canvas");
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", spec.title);
  frame.append(canvas);

  const actions = document.createElement("div");
  actions.className = "chart-actions";
  const download = document.createElement("button");
  download.type = "button";
  download.className = "link-btn";
  download.dataset.i18n = "downloadPng";
  download.textContent = t("downloadPng");
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.dataset.i18n = "showData";
  summary.textContent = t("showData");
  details.append(summary, dataTable(spec));
  actions.append(download, details);
  card.append(title, frame, actions);

  // Free the charts of chats that are no longer on the page.
  for (let i = chartCards.length - 1; i >= 0; i--) {
    if (!chartCards[i].canvas.isConnected) chartCards.splice(i, 1)[0].chart?.destroy();
  }
  const entry = { canvas, spec, chart: null };
  chartCards.push(entry);
  // Chart.js needs the canvas in the page to size it.
  requestAnimationFrame(() => { entry.chart = new Chart(canvas, chartConfig(spec)); });
  download.onclick = () => {
    const link = document.createElement("a");
    link.href = entry.chart.toBase64Image("image/png");
    link.download = `${spec.title.replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "") || "chart"}.png`;
    link.click();
  };
  return card;
}

// Dark and light steps are different colours, not a flip: rebuild every chart on a change.
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  for (let i = chartCards.length - 1; i >= 0; i--) {
    const entry = chartCards[i];
    entry.chart?.destroy();
    if (!entry.canvas.isConnected) { chartCards.splice(i, 1); continue; }
    entry.chart = new Chart(entry.canvas, chartConfig(entry.spec));
  }
});
