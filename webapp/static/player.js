/* Timeline Player — animate metric composition over time.
   One frame = one period's snapshot. Colors are assigned once per payload
   (L1 values get fixed palette hues by overall size, L2 values wear
   lightness steps of their parent's hue) so slices/bars stay trackable
   across frames and across the bar/sunburst chart types. */

(function () {
  "use strict";

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function periodLabel(p) {
    if (p.length === 7) return MONTHS[+p.slice(5, 7) - 1] + " " + p.slice(0, 4);
    var d = +p.slice(8, 10) + " " + MONTHS[+p.slice(5, 7) - 1] + " " + p.slice(0, 4);
    return state.grain === "weekly" ? "wk " + d : d;
  }

  function fmtFull(v) {
    if (v === null || v === undefined) return "–";
    return Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : (+v).toLocaleString();
  }

  function fmtCompact(v) {
    if (v === null || v === undefined || isNaN(v)) return "–";
    var a = Math.abs(v);
    if (a >= 1e12) return (v / 1e12).toFixed(1) + "T";
    if (a >= 1e9) return (v / 1e9).toFixed(1) + "B";
    if (a >= 1e6) return (v / 1e6).toFixed(1) + "M";
    if (a >= 1e3) return (v / 1e3).toFixed(1) + "K";
    return Math.round(v).toLocaleString();
  }

  function hexMix(a, b, t) {
    function ch(h, i) { return parseInt(h.slice(i, i + 2), 16); }
    function hx(n) { return ("0" + Math.round(n).toString(16)).slice(-2); }
    a = a.replace("#", ""); b = b.replace("#", "");
    return "#" + hx(ch(a, 0) + (ch(b, 0) - ch(a, 0)) * t)
               + hx(ch(a, 2) + (ch(b, 2) - ch(a, 2)) * t)
               + hx(ch(a, 4) + (ch(b, 4) - ch(a, 4)) * t);
  }

  /* ---- state ------------------------------------------------------------- */

  var state = {
    metric: "nnl", dim: "finance_l1", grain: "monthly", kind: "bar",
    speed: 1, playing: false, timer: null,
    payload: null, idx: 0,
    colorsOuter: {},     // chosen-dim value -> color (stable per payload)
    colorsInner: {},     // parent-dim value -> solid hue
    barOrder: [],        // fixed bar/slice ordering
    innerOrder: [],
    chart: null,
    globalMax: 0,
  };

  var el = {
    metric: document.getElementById("pl-metric"),
    dim: document.getElementById("pl-dim"),
    grain: document.getElementById("pl-grain"),
    kind: document.getElementById("pl-kind"),
    play: document.getElementById("pl-play"),
    speed: document.getElementById("pl-speed"),
    date: document.getElementById("pl-date"),
    slider: document.getElementById("pl-slider"),
    title: document.getElementById("pl-title"),
    canvas: document.getElementById("pl-canvas"),
    holder: document.querySelector(".player-holder"),
  };

  function hues() {
    return [cssVar("--series-1"), cssVar("--series-2"), cssVar("--series-3"),
            cssVar("--series-4"), cssVar("--series-5"), cssVar("--series-6"),
            cssVar("--series-7"), cssVar("--series-8")];
  }

  /* ---- color + order assignment (once per payload) ------------------------ */

  function avg(series) {
    var s = 0, n = 0;
    series.forEach(function (v) { if (v !== null) { s += v; n++; } });
    return n ? s / n : 0;
  }

  function assignColors() {
    var p = state.payload;
    var pal = hues();
    var gray = cssVar("--muted");
    var surface = cssVar("--surface");
    state.colorsOuter = {};
    state.colorsInner = {};

    var isL2 = !!p.inner;
    // L1 values (either the chosen dim itself, or the inner ring's dim)
    var l1series = isL2 ? p.inner.series : p.series;
    var l1sorted = Object.keys(l1series).sort(function (a, b) {
      return avg(l1series[b]) - avg(l1series[a]);
    });
    l1sorted.forEach(function (v, i) {
      state.colorsInner[v] = i < pal.length ? pal[i] : gray;
    });

    if (isL2) {
      // children ordered under their parents; shade steps of the parent hue.
      // Separate maps: an L2 value may share its parent's name (e.g. finance
      // 'For sale' → 'For sale'), and must not overwrite the solid hue.
      state.innerOrder = l1sorted;
      var kids = {};
      Object.keys(p.series).forEach(function (v) {
        var parent = p.parents[v] || "unknown";
        (kids[parent] = kids[parent] || []).push(v);
      });
      var order = [];
      l1sorted.concat(Object.keys(kids).filter(function (k) {
        return l1sorted.indexOf(k) === -1;
      })).forEach(function (parent) {
        (kids[parent] || []).sort(function (a, b) {
          return avg(p.series[b]) - avg(p.series[a]);
        }).forEach(function (child, i) {
          state.colorsOuter[child] = hexMix(
            state.colorsInner[parent] || gray, surface,
            Math.min(0.5, 0.08 + i * 0.09));
          order.push(child);
        });
      });
      state.barOrder = order;
    } else {
      state.innerOrder = [];
      state.barOrder = l1sorted;
      state.colorsOuter = state.colorsInner;
    }

    state.globalMax = 0;
    Object.keys(p.series).forEach(function (v) {
      p.series[v].forEach(function (x) {
        if (x !== null && x > state.globalMax) state.globalMax = x;
      });
    });
  }

  /* ---- frame data ---------------------------------------------------------- */

  function frameValues(series, order, i) {
    return order.map(function (v) {
      var x = series[v] ? series[v][i] : null;
      return x === null || x === undefined ? 0 : x;
    });
  }

  var BAR_TOP = 14;

  function barFrame(i) {
    var p = state.payload;
    var order = state.barOrder.slice(0, BAR_TOP);
    var values = frameValues(p.series, order, i);
    if (state.barOrder.length > BAR_TOP) {
      var rest = frameValues(p.series, state.barOrder.slice(BAR_TOP), i)
        .reduce(function (a, b) { return a + b; }, 0);
      order = order.concat(["Other"]);
      values = values.concat([rest]);
    }
    return { labels: order, values: values };
  }

  /* ---- chart builders -------------------------------------------------------- */

  function destroyChart() {
    if (state.chart) { state.chart.destroy(); state.chart = null; }
  }

  function tooltipStyle() {
    return {
      backgroundColor: cssVar("--surface"),
      titleColor: cssVar("--ink-2"),
      bodyColor: cssVar("--ink"),
      borderColor: cssVar("--grid"),
      borderWidth: 1,
      padding: 10,
    };
  }

  function buildBar() {
    destroyChart();
    var f = barFrame(state.idx);
    var gray = cssVar("--muted");
    var opts = tooltipStyle();
    opts.callbacks = {
      label: function (item) {
        var total = item.dataset.data.reduce(function (a, b) { return a + b; }, 0);
        var share = total ? (item.parsed.x / total * 100).toFixed(1) : "0";
        return " " + fmtFull(item.parsed.x) + "  (" + share + "%)";
      }
    };
    state.chart = new Chart(el.canvas, {
      type: "bar",
      data: {
        labels: f.labels,
        datasets: [{
          data: f.values,
          backgroundColor: f.labels.map(function (v) { return state.colorsOuter[v] || gray; }),
          maxBarThickness: 22,
          borderRadius: { topRight: 4, bottomRight: 4, topLeft: 0, bottomLeft: 0 },
          borderSkipped: false
        }]
      },
      options: {
        indexAxis: "y",
        responsive: true, maintainAspectRatio: false,
        animation: { duration: Math.min(600, 700 / state.speed), easing: "easeOutQuad" },
        plugins: { legend: { display: false }, tooltip: opts },
        scales: {
          x: {
            beginAtZero: true,
            suggestedMax: state.globalMax * 1.05,
            grid: { color: cssVar("--grid"), lineWidth: 1 },
            border: { display: false },
            ticks: { color: cssVar("--muted"), maxTicksLimit: 7,
                     callback: function (v) { return fmtCompact(v); } }
          },
          y: {
            grid: { display: false },
            border: { color: cssVar("--baseline") },
            ticks: { color: cssVar("--ink-2"), autoSkip: false }
          }
        }
      }
    });
  }

  function buildSunburst() {
    destroyChart();
    var p = state.payload;
    var gray = cssVar("--muted");
    var surface = cssVar("--surface");
    var datasets = [];
    var outer = {
      data: frameValues(p.series, state.barOrder, state.idx),
      backgroundColor: state.barOrder.map(function (v) { return state.colorsOuter[v] || gray; }),
      borderColor: surface,
      borderWidth: 1,
      _labels: state.barOrder,
    };
    datasets.push(outer);
    if (p.inner) {
      datasets.push({
        data: frameValues(p.inner.series, state.innerOrder, state.idx),
        backgroundColor: state.innerOrder.map(function (v) { return state.colorsInner[v] || gray; }),
        borderColor: surface,
        borderWidth: 2,
        _labels: state.innerOrder,
      });
    }
    var opts = tooltipStyle();
    opts.callbacks = {
      title: function () { return periodLabel(state.payload.periods[state.idx]); },
      label: function (item) {
        var labels = item.dataset._labels;
        var total = item.dataset.data.reduce(function (a, b) { return a + b; }, 0);
        var share = total ? (item.parsed / total * 100).toFixed(1) : "0";
        return " " + labels[item.dataIndex] + ": " + fmtFull(item.parsed)
          + " (" + share + "%)";
      }
    };
    state.chart = new Chart(el.canvas, {
      type: "doughnut",
      data: { labels: [], datasets: datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        cutout: p.inner ? "25%" : "45%",
        animation: { duration: Math.min(600, 700 / state.speed), easing: "easeOutQuad" },
        plugins: { legend: { display: false }, tooltip: opts }
      }
    });
  }

  function buildChart() {
    if (state.kind === "bar") buildBar();
    else buildSunburst();
  }

  /* ---- frame updates (animated in place) -------------------------------------- */

  function renderFrame() {
    var p = state.payload;
    if (!p || !state.chart) return;
    el.date.textContent = periodLabel(p.periods[state.idx]);
    el.slider.value = state.idx;
    if (state.kind === "bar") {
      var f = barFrame(state.idx);
      state.chart.data.datasets[0].data = f.values;
    } else {
      state.chart.data.datasets[0].data =
        frameValues(p.series, state.barOrder, state.idx);
      if (p.inner && state.chart.data.datasets[1]) {
        state.chart.data.datasets[1].data =
          frameValues(p.inner.series, state.innerOrder, state.idx);
      }
    }
    state.chart.update();
  }

  /* ---- playback ------------------------------------------------------------------ */

  function stop() {
    state.playing = false;
    el.play.textContent = "▶";
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
  }

  function tick() {
    if (state.idx >= state.payload.periods.length - 1) { stop(); return; }
    state.idx += 1;
    renderFrame();
  }

  function play() {
    if (!state.payload) return;
    if (state.idx >= state.payload.periods.length - 1) state.idx = 0;
    state.playing = true;
    el.play.textContent = "❚❚";
    state.timer = setInterval(tick, 800 / state.speed);
  }

  /* ---- data loading ----------------------------------------------------------------- */

  var loadSeq = 0;

  function load(keepPeriod) {
    stop();
    el.holder.style.opacity = "0.45";
    var seq = ++loadSeq;   // discard stale responses from rapid control changes
    var url = "/player/data?metric=" + encodeURIComponent(state.metric)
      + "&grain=" + state.grain + "&dim=" + encodeURIComponent(state.dim);
    fetch(url).then(function (r) {
      if (!r.ok) throw new Error("no data");
      return r.json();
    }).then(function (payload) {
      if (seq !== loadSeq) return;   // a newer request superseded this one
      state.payload = payload;
      assignColors();
      el.slider.max = payload.periods.length - 1;
      // snap to roughly the same point in time
      var idx = payload.periods.length - 1;
      if (keepPeriod) {
        var month = keepPeriod.slice(0, 7);
        for (var i = 0; i < payload.periods.length; i++) {
          if (payload.periods[i].slice(0, 7) === month) {
            idx = i;   // first period in the same month as the old point
            break;
          }
        }
      }
      state.idx = idx;
      var label = el.metric.options[el.metric.selectedIndex].textContent;
      el.title.textContent = label + " composition by "
        + el.dim.options[el.dim.selectedIndex].textContent;
      buildChart();
      renderFrame();
      el.holder.style.opacity = "1";
    }).catch(function () {
      if (seq !== loadSeq) return;
      el.holder.style.opacity = "1";
      el.date.textContent = "no data for this combination";
    });
  }

  /* ---- controls ------------------------------------------------------------------------ */

  function toggleGroup(container, onChange) {
    container.querySelectorAll("button").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (btn.disabled) return;
        container.querySelectorAll("button").forEach(function (b) {
          b.classList.remove("active");
        });
        btn.classList.add("active");
        onChange(btn.dataset.value);
      });
    });
  }

  function grainsForMetric() {
    return (el.metric.options[el.metric.selectedIndex].dataset.grains || "").split(",");
  }

  function syncGrainAvailability() {
    var grains = grainsForMetric();
    el.grain.querySelectorAll("button").forEach(function (b) {
      b.disabled = grains.indexOf(b.dataset.value) === -1;
      if (b.disabled && b.classList.contains("active")) {
        b.classList.remove("active");
        var fallback = el.grain.querySelector('button[data-value="monthly"]');
        fallback.classList.add("active");
        state.grain = "monthly";
      }
    });
  }

  el.metric.addEventListener("change", function () {
    state.metric = el.metric.value;
    syncGrainAvailability();
    load(currentPeriod());
  });
  el.dim.addEventListener("change", function () {
    state.dim = el.dim.value;
    load(currentPeriod());
  });

  function currentPeriod() {
    return state.payload ? state.payload.periods[state.idx] : null;
  }

  toggleGroup(el.grain, function (v) {
    var old = currentPeriod();
    state.grain = v;
    load(old ? old.slice(0, 7) : null);   // keep the same month either way
  });
  toggleGroup(el.kind, function (v) {
    state.kind = v;
    buildChart();
    renderFrame();
  });
  toggleGroup(el.speed, function (v) {
    state.speed = parseFloat(v);
    if (state.playing) { stop(); play(); }
  });

  el.play.addEventListener("click", function () {
    if (state.playing) stop(); else play();
  });

  el.slider.addEventListener("input", function () {
    stop();
    state.idx = parseInt(el.slider.value, 10) || 0;
    renderFrame();
  });

  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  syncGrainAvailability();
  load(null);

  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
    assignColors();
    buildChart();
    renderFrame();
  });
})();
