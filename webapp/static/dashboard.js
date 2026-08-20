/* Renders KPI cards + charts from the JSON embedded in the page.
   Chart specs follow the dataviz method: 2px lines, hairline grid, crosshair
   tooltip listing every series, legend for >=2 series, direct end labels,
   single-hue horizontal bars for magnitude, diverging blue/red for +/-,
   stacked columns with surface gaps, sequential-ramp heatmap,
   light/dark from CSS tokens. */

(function () {
  "use strict";

  var charts = [];

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function isDark() {
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function palette() {
    return {
      series: [cssVar("--series-1"), cssVar("--series-2"),
               cssVar("--series-3"), cssVar("--series-4")],
      ink: cssVar("--ink"), ink2: cssVar("--ink-2"), muted: cssVar("--muted"),
      grid: cssVar("--grid"), baseline: cssVar("--baseline"),
      surface: cssVar("--surface"), spark: cssVar("--spark"),
      accent: cssVar("--accent"), neg: cssVar("--diverge-neg")
    };
  }

  // sequential blue ramp (palette.md); reversed on dark so low recedes to surface
  var SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];

  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function periodLabel(p, grain) {
    if (p.length === 7) return MONTHS[+p.slice(5, 7) - 1] + " " + p.slice(2, 4);
    if (p.length === 10) {
      var d = +p.slice(8, 10) + " " + MONTHS[+p.slice(5, 7) - 1];
      return grain === "weekly" ? "wk " + d : d;   // weekly key = its Monday
    }
    return p;
  }

  function fmtCompact(v, pct) {
    if (v === null || v === undefined || isNaN(v)) return "–";
    if (pct) return (+v).toFixed(1) + "%";
    var a = Math.abs(v);
    if (a >= 1e12) return (v / 1e12).toFixed(1) + "T";
    if (a >= 1e9) return (v / 1e9).toFixed(1) + "B";
    if (a >= 1e6) return (v / 1e6).toFixed(1) + "M";
    if (a >= 1e4) return (v / 1e3).toFixed(0) + "K";
    if (a >= 1e3) return (v / 1e3).toFixed(1) + "K";
    return a >= 100 || v === Math.round(v) ? Math.round(v).toLocaleString() : (+v).toFixed(1);
  }

  function fmtFull(v, pct) {
    if (v === null || v === undefined) return "–";
    if (pct) return (+v).toFixed(2) + "%";
    return Math.abs(v) >= 100 ? Math.round(v).toLocaleString() : (+v).toLocaleString();
  }

  /* ---- filter row: two groups, multi-value chips, cascading values ------ */

  // category-family dims in CATEGORY_TREE column order
  var TREE_DIMS = ["finance_l1", "finance_l2",
                   "category_l1", "category_l2", "category_l3", "category_l4"];

  function selectedConstraints(exceptDim) {
    /* {dim: [values]} currently chosen in every OTHER filter field. */
    var out = {};
    document.querySelectorAll(".f-field").forEach(function (field) {
      var dim = field.dataset.dim;
      if (dim === exceptDim) return;
      var vals = Array.prototype.map.call(
        field.querySelectorAll('input[type="hidden"]'),
        function (h) { return h.value; });
      if (vals.length) out[dim] = vals;
    });
    return out;
  }

  function optionsFor(dim) {
    /* Value list for `dim`, restricted through the category hierarchy by
       selections in related fields (L1 narrows L2..L4, finance narrows
       category and vice versa). */
    var base = window.FILTER_OPTIONS[dim] || [];
    if (TREE_DIMS.indexOf(dim) === -1 || !window.CATEGORY_TREE
        || !window.CATEGORY_TREE.length) return base;
    var cons = selectedConstraints(dim);
    var treeCons = [];
    TREE_DIMS.forEach(function (d, idx) {
      if (d !== dim && cons[d]) treeCons.push([idx, cons[d]]);
    });
    if (!treeCons.length) return base;
    var col = TREE_DIMS.indexOf(dim);
    var allowed = {};
    window.CATEGORY_TREE.forEach(function (row) {
      for (var i = 0; i < treeCons.length; i++) {
        if (treeCons[i][1].indexOf(row[treeCons[i][0]]) === -1) return;
      }
      allowed[row[col]] = true;
    });
    return base.filter(function (v) { return allowed[v]; });
  }

  function initFilters() {
    if (!window.FILTER_OPTIONS) return;
    document.querySelectorAll(".f-field").forEach(function (field) {
      var dim = field.dataset.dim;
      var input = field.querySelector(".fg-input");
      var datalist = field.querySelector("datalist");
      var chipsBox = field.querySelector(".fg-chips");

      function hiddenInputs() {
        return Array.prototype.slice.call(
          field.querySelectorAll('input[type="hidden"]'));
      }
      function addChip(value) {
        var chip = document.createElement("span");
        chip.className = "chip";
        chip.appendChild(document.createTextNode(value));
        var x = document.createElement("button");
        x.type = "button";
        x.className = "chip-x";
        x.textContent = "×";
        x.addEventListener("click", function () {
          chip.remove();
          hiddenInputs().forEach(function (h) {
            if (h.value === value) h.remove();
          });
          refreshAllDatalists();
        });
        chip.appendChild(x);
        chipsBox.appendChild(chip);
      }
      function addValue(value) {
        if (!value) return;
        if (optionsFor(dim).indexOf(value) === -1) return;    // only valid values
        if (hiddenInputs().some(function (h) { return h.value === value; })) return;
        var h = document.createElement("input");
        h.type = "hidden";
        h.name = dim;
        h.value = value;
        field.appendChild(h);
        addChip(value);
        input.value = "";
        refreshAllDatalists();
      }
      function fillDatalist() {
        while (datalist.firstChild) datalist.removeChild(datalist.firstChild);
        optionsFor(dim).forEach(function (v) {
          var opt = document.createElement("option");
          opt.value = v;
          datalist.appendChild(opt);
        });
      }
      field._fillDatalist = fillDatalist;
      input.addEventListener("change", function () { addValue(input.value); });
      input.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") {
          ev.preventDefault();
          addValue(input.value);
        }
      });
      hiddenInputs().forEach(function (h) { addChip(h.value); });  // restore from URL
    });
    refreshAllDatalists();
  }

  function refreshAllDatalists() {
    document.querySelectorAll(".f-field").forEach(function (field) {
      if (field._fillDatalist) field._fillDatalist();
    });
  }

  /* ---- shared hover tooltip for HTML/SVG vizzes (map, heatmaps) --------- */

  var vizTip = null;

  function getVizTip() {
    if (!vizTip) {
      vizTip = document.createElement("div");
      vizTip.className = "viz-tip";
      vizTip.style.display = "none";
      document.body.appendChild(vizTip);
    }
    return vizTip;
  }

  function showTip(text, x, y) {
    var tip = getVizTip();
    tip.textContent = text;
    tip.style.display = "block";
    var pad = 14;
    var w = tip.offsetWidth, h = tip.offsetHeight;
    var left = x + pad, top = y + pad;
    if (left + w > window.innerWidth - 8) left = x - w - pad;
    if (top + h > window.innerHeight - 8) top = y - h - pad;
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }

  function hideTip() {
    if (vizTip) vizTip.style.display = "none";
  }

  function attachTip(el, text) {
    el.setAttribute("data-tip", text);
    el.addEventListener("mousemove", function (ev) {
      showTip(el.getAttribute("data-tip"), ev.clientX, ev.clientY);
    });
    el.addEventListener("mouseleave", hideTip);
    el.addEventListener("focus", function () {
      var r = el.getBoundingClientRect();
      showTip(el.getAttribute("data-tip"), r.right, r.top);
    });
    el.addEventListener("blur", hideTip);
  }

  /* ---- loading overlay on slow navigations ------------------------------- */

  function showLoading() {
    if (document.querySelector(".page-loading")) return;
    var ov = document.createElement("div");
    ov.className = "page-loading";
    var sp = document.createElement("div");
    sp.className = "page-loading-spinner";
    ov.appendChild(sp);
    var txt = document.createElement("div");
    txt.className = "page-loading-text";
    txt.textContent = "Loading…";
    ov.appendChild(txt);
    document.body.appendChild(ov);
  }

  function initLoading() {
    var form = document.getElementById("filter-form");
    if (form) form.addEventListener("submit", function () { showLoading(); });
    document.querySelectorAll(".grain-toggle a, nav.tabs a, .topbar .back, .filters-clear")
      .forEach(function (a) {
        a.addEventListener("click", function (ev) {
          // let open-in-new-tab clicks through without an overlay
          if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button === 1) return;
          showLoading();
        });
      });
    // back/forward from bfcache restores the page with the overlay still in
    // the DOM — remove it
    window.addEventListener("pageshow", function (ev) {
      if (ev.persisted) {
        var ov = document.querySelector(".page-loading");
        if (ov) ov.remove();
      }
    });
  }

  /* ---- fullscreen toggle ------------------------------------------------ */

  function exitFullscreen() {
    document.querySelectorAll(".chart-card.fullscreen").forEach(function (fig) {
      fig.classList.remove("fullscreen");
      var b = fig.querySelector(".fs-btn");
      if (b) b.textContent = "⤢";
    });
    document.body.classList.remove("has-fullscreen");
    window.dispatchEvent(new Event("resize"));
  }

  function initFullscreen() {
    document.querySelectorAll(".fs-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var fig = btn.closest(".chart-card");
        var on = !fig.classList.contains("fullscreen");
        exitFullscreen();
        if (on) {
          fig.classList.add("fullscreen");
          btn.textContent = "⤡";
          document.body.classList.add("has-fullscreen");
        }
        window.dispatchEvent(new Event("resize"));
      });
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") exitFullscreen();
    });
  }

  /* ---- info popovers ---------------------------------------------------- */

  function initInfo() {
    var open = null;
    document.querySelectorAll(".info-btn[data-info]").forEach(function (btn) {
      btn.addEventListener("click", function (ev) {
        ev.stopPropagation();
        if (open) { open.remove(); open = null; }
        var pop = document.createElement("div");
        pop.className = "popover";
        btn.dataset.info.split("\n").forEach(function (lineTxt) {
          var p = document.createElement("p");
          p.textContent = lineTxt;
          pop.appendChild(p);
        });
        btn.parentElement.style.position = "relative";
        btn.parentElement.appendChild(pop);
        open = pop;
      });
    });
    document.addEventListener("click", function () {
      if (open) { open.remove(); open = null; }
    });
  }

  /* ---- KPI cards ------------------------------------------------------ */

  function renderCards() {
    document.querySelectorAll(".card-value[data-value]").forEach(function (el) {
      var pct = el.dataset.fmt === "pct";
      var v = parseFloat(el.dataset.value);
      el.insertBefore(document.createTextNode(fmtCompact(v, pct)), el.firstChild);
      el.title = fmtFull(v, pct);
    });
    document.querySelectorAll(".delta[data-delta]").forEach(function (el) {
      var d = el.dataset.delta;
      var name = el.textContent;
      if (d === "") { el.textContent = name + " –"; return; }
      var v = parseFloat(d);
      el.textContent = name + " " + (v > 0 ? "+" : "") + v.toFixed(1) + "%";
      el.classList.add(v >= 0 ? "up" : "down");
    });
  }

  function renderSparks() {
    var pal = palette();
    document.querySelectorAll("canvas.spark[data-spark]").forEach(function (cv) {
      var pts;
      try { pts = JSON.parse(cv.dataset.spark); } catch (e) { return; }
      if (!pts || pts.length < 2) return;
      charts.push(new Chart(cv, {
        type: "line",
        data: {
          labels: pts.map(function (p) { return p[0]; }),
          datasets: [{
            data: pts.map(function (p) { return p[1]; }),
            borderColor: pal.spark,
            borderWidth: 1.5,
            pointRadius: pts.map(function (_, i) { return i === pts.length - 1 ? 2.5 : 0; }),
            pointBackgroundColor: pal.accent,
            pointBorderWidth: 0,
            tension: 0,
            fill: false
          }]
        },
        options: {
          responsive: true, maintainAspectRatio: false, animation: false,
          events: [],
          plugins: { legend: { display: false }, tooltip: { enabled: false } },
          scales: { x: { display: false }, y: { display: false } }
        }
      }));
    });
  }

  /* ---- shared chart pieces ---------------------------------------------- */

  var crosshair = {
    id: "crosshair",
    afterDraw: function (chart) {
      var tt = chart.tooltip;
      if (!tt || !tt.getActiveElements().length) return;
      var x = tt.getActiveElements()[0].element.x;
      var ctx = chart.ctx, area = chart.chartArea;
      ctx.save();
      ctx.strokeStyle = palette().baseline;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, area.top);
      ctx.lineTo(x, area.bottom);
      ctx.stroke();
      ctx.restore();
    }
  };

  var endLabels = {
    id: "endLabels",
    afterDatasetsDraw: function (chart) {
      var opts = chart.options.plugins.endLabels || {};
      if (!opts.enabled) return;
      var ctx = chart.ctx, pal = palette();
      ctx.save();
      ctx.font = "600 11px system-ui, -apple-system, sans-serif";
      ctx.fillStyle = pal.ink2;
      ctx.textBaseline = "middle";
      var used = [];
      chart.data.datasets.forEach(function (ds, i) {
        var metaDs = chart.getDatasetMeta(i);
        if (!metaDs.visible) return;
        var lastIdx = -1;
        for (var j = ds.data.length - 1; j >= 0; j--) {
          if (ds.data[j] !== null && ds.data[j] !== undefined) { lastIdx = j; break; }
        }
        if (lastIdx < 0 || !metaDs.data[lastIdx]) return;
        var pt = metaDs.data[lastIdx];
        var y = pt.y;
        used.forEach(function (u) { if (Math.abs(u - y) < 12) y = u + 12; });
        used.push(y);
        ctx.fillText(fmtCompact(ds.data[lastIdx], opts.pct), pt.x + 6, y);
      });
      ctx.restore();
    }
  };

  function baseScales(pal, pct) {
    return {
      x: {
        grid: { display: false },
        border: { color: pal.baseline },
        ticks: { color: pal.muted, maxTicksLimit: 10, maxRotation: 0 }
      },
      y: {
        beginAtZero: true,
        grid: { color: pal.grid, lineWidth: 1 },
        border: { display: false },
        ticks: {
          color: pal.muted,
          maxTicksLimit: 6,
          callback: function (v) { return fmtCompact(v, pct); }
        }
      }
    };
  }

  function tooltipOpts(pal, pct) {
    return {
      mode: "index",
      intersect: false,
      backgroundColor: pal.surface,
      titleColor: pal.ink2,
      bodyColor: pal.ink,
      borderColor: pal.grid,
      borderWidth: 1,
      padding: 10,
      boxWidth: 10,
      boxHeight: 2,
      usePointStyle: false,
      callbacks: {
        label: function (item) {
          var v = item.parsed.y !== undefined ? item.parsed.y : item.parsed.x;
          return " " + fmtFull(v, pct) + "  " + (item.dataset.label || "");
        }
      }
    };
  }

  function alignSeries(seriesList) {
    var periods = [];
    var seen = {};
    seriesList.forEach(function (s) {
      s.points.forEach(function (p) {
        if (!seen[p[0]]) { seen[p[0]] = true; periods.push(p[0]); }
      });
    });
    periods.sort();
    return periods;
  }

  /* ---- chart kinds ------------------------------------------------------ */

  function buildLine(canvas, spec) {
    var pal = palette();
    var periods = alignSeries(spec.series);
    var single = spec.series.length === 1;
    var datasets = spec.series.map(function (s, i) {
      var byP = {};
      s.points.forEach(function (p) { byP[p[0]] = p[1]; });
      var color = pal.series[i % 4];
      return {
        label: s.label,
        data: periods.map(function (p) { return (p in byP) ? byP[p] : null; }),
        borderColor: color,
        backgroundColor: spec.area && single ? color + "1A" : color,
        fill: !!(spec.area && single),
        borderWidth: 2,
        tension: 0,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBorderColor: pal.surface,
        pointHoverBorderWidth: 2,
        spanGaps: false
      };
    });
    charts.push(new Chart(canvas, {
      type: "line",
      data: { labels: periods.map(function (p) { return periodLabel(p, spec.grain); }),
              datasets: datasets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        layout: { padding: { right: 56 } },
        plugins: {
          legend: {
            display: spec.series.length > 1,
            labels: { color: pal.ink2, boxWidth: 14, boxHeight: 2 }
          },
          tooltip: tooltipOpts(pal, spec.pct),
          endLabels: { enabled: true, pct: spec.pct }
        },
        scales: baseScales(pal, spec.pct)
      },
      plugins: [crosshair, endLabels]
    }));
  }

  function buildStacked(canvas, spec) {
    var pal = palette();
    var periods = alignSeries(spec.series);
    var datasets = spec.series.map(function (s, i) {
      var byP = {};
      s.points.forEach(function (p) { byP[p[0]] = p[1]; });
      return {
        label: s.label,
        data: periods.map(function (p) { return (p in byP) ? byP[p] : 0; }),
        backgroundColor: pal.series[i % 4],
        borderColor: pal.surface,   // 2px surface gap between segments
        borderWidth: 2,
        borderSkipped: false,
        maxBarThickness: 24
      };
    });
    var scales = baseScales(pal, false);
    scales.x.stacked = true;
    scales.y.stacked = true;
    charts.push(new Chart(canvas, {
      type: "bar",
      data: { labels: periods.map(function (p) { return periodLabel(p, spec.grain); }),
              datasets: datasets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: true, labels: { color: pal.ink2, boxWidth: 12, boxHeight: 12 } },
          tooltip: tooltipOpts(pal, false)
        },
        scales: scales
      }
    }));
  }

  function buildShareArea(canvas, spec) {
    // 100%-stacked area: each period's series values are pre-normalized
    // shares (sum to 100). Largest series first = bottom layer.
    var pal = palette();
    var periods = alignSeries(spec.series);
    var datasets = spec.series.map(function (s, i) {
      var byP = {};
      s.points.forEach(function (p) { byP[p[0]] = p[1]; });
      return {
        label: s.label,
        data: periods.map(function (p) { return (p in byP) ? byP[p] : 0; }),
        backgroundColor: pal.series[i % 4],
        borderColor: pal.series[i % 4],
        borderWidth: 1,
        pointRadius: 0,
        fill: true,
        tension: 0.25
      };
    });
    var scales = baseScales(pal, true);
    scales.y.stacked = true;
    scales.y.max = 100;
    charts.push(new Chart(canvas, {
      type: "line",
      data: { labels: periods.map(function (p) { return periodLabel(p, spec.grain); }),
              datasets: datasets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: true, labels: { color: pal.ink2, boxWidth: 12, boxHeight: 12 } },
          tooltip: tooltipOpts(pal, true)
        },
        scales: scales
      }
    }));
  }

  function buildBarh(canvas, spec) {
    var pal = palette();
    charts.push(new Chart(canvas, {
      type: "bar",
      data: {
        labels: spec.rows.map(function (r) { return r[0]; }),
        datasets: [{
          data: spec.rows.map(function (r) { return r[1]; }),
          backgroundColor: pal.series[0],
          maxBarThickness: 18,
          borderRadius: { topRight: 4, bottomRight: 4, topLeft: 0, bottomLeft: 0 },
          borderSkipped: false
        }]
      },
      options: {
        indexAxis: "y",
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            intersect: true,
            backgroundColor: pal.surface, titleColor: pal.ink2, bodyColor: pal.ink,
            borderColor: pal.grid, borderWidth: 1,
            callbacks: { label: function (item) { return " " + fmtFull(item.parsed.x); } }
          }
        },
        scales: {
          x: {
            beginAtZero: true,
            grid: { color: pal.grid, lineWidth: 1 },
            border: { display: false },
            ticks: { color: pal.muted, maxTicksLimit: 6,
                     callback: function (v) { return fmtCompact(v); } }
          },
          y: {
            grid: { display: false },
            border: { color: pal.baseline },
            ticks: { color: pal.ink2, autoSkip: false }
          }
        }
      }
    }));
  }

  function buildDivergingBarh(canvas, spec) {
    var pal = palette();
    charts.push(new Chart(canvas, {
      type: "bar",
      data: {
        labels: spec.rows.map(function (r) { return r[0]; }),
        datasets: [{
          data: spec.rows.map(function (r) { return r[1]; }),
          backgroundColor: spec.rows.map(function (r) {
            return r[1] >= 0 ? pal.series[0] : pal.neg;
          }),
          maxBarThickness: 18,
          borderRadius: 4,
          borderSkipped: false
        }]
      },
      options: {
        indexAxis: "y",
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            intersect: true,
            backgroundColor: pal.surface, titleColor: pal.ink2, bodyColor: pal.ink,
            borderColor: pal.grid, borderWidth: 1,
            callbacks: {
              label: function (item) {
                var v = item.parsed.x;
                return " " + (v > 0 ? "+" : "") + v.toFixed(1) + "% MoM";
              }
            }
          }
        },
        scales: {
          x: {
            grid: { color: pal.grid, lineWidth: 1 },
            border: { display: false },
            ticks: { color: pal.muted, maxTicksLimit: 7,
                     callback: function (v) { return (v > 0 ? "+" : "") + v + "%"; } }
          },
          y: {
            grid: { display: false },
            border: { color: pal.baseline },
            ticks: { color: pal.ink2, autoSkip: false }
          }
        }
      }
    }));
  }

  function buildHeatmap(fig, spec) {
    // weekday (rows) × week (columns) grid, sequential ramp, built as HTML.
    var holder = fig.querySelector(".chart-holder");
    holder.querySelector("canvas").remove();
    var ramp = isDark() ? SEQ.slice().reverse() : SEQ;
    var byDate = {};
    var min = Infinity, max = -Infinity;
    spec.points.forEach(function (p) {
      byDate[p[0]] = p[1];
      if (p[1] < min) min = p[1];
      if (p[1] > max) max = p[1];
    });
    var dates = spec.points.map(function (p) { return p[0]; }).sort();
    var first = new Date(dates[0] + "T00:00:00");
    var last = new Date(dates[dates.length - 1] + "T00:00:00");
    // back up to Monday
    var start = new Date(first);
    start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
    var weeks = [];
    for (var d = new Date(start); d <= last; d.setDate(d.getDate() + 7)) {
      weeks.push(new Date(d));
    }
    var grid = document.createElement("div");
    grid.className = "heatmap";
    grid.style.gridTemplateColumns = "auto repeat(" + weeks.length + ", 1fr)";
    var dayNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    for (var row = 0; row < 7; row++) {
      var lab = document.createElement("div");
      lab.className = "hm-label";
      lab.textContent = dayNames[row];
      grid.appendChild(lab);
      for (var w = 0; w < weeks.length; w++) {
        var day = new Date(weeks[w]);
        day.setDate(day.getDate() + row);
        // build the key from LOCAL date parts — toISOString() shifts a day
        // for viewers east of UTC (the dashboard's audience is UTC+5)
        var key = day.getFullYear() + "-"
          + ("0" + (day.getMonth() + 1)).slice(-2) + "-"
          + ("0" + day.getDate()).slice(-2);
        var cell = document.createElement("div");
        cell.className = "hm-cell";
        var v = byDate[key];
        if (v !== undefined && max > min) {
          var t = (v - min) / (max - min);
          cell.style.background = ramp[Math.min(ramp.length - 1, Math.floor(t * ramp.length))];
          attachTip(cell, key + ": " + fmtFull(v));
          cell.tabIndex = 0;
        } else {
          cell.classList.add("hm-empty");
        }
        grid.appendChild(cell);
      }
    }
    holder.style.height = "auto";
    holder.appendChild(grid);
  }

  function buildMatrix(fig, spec) {
    // dim value (rows) × month (columns); shade normalized per row.
    var holder = fig.querySelector(".chart-holder");
    var canvas = holder.querySelector("canvas");
    if (canvas) canvas.remove();
    var ramp = isDark() ? SEQ.slice().reverse() : SEQ;
    var grid = document.createElement("div");
    grid.className = "heatmap matrix";
    grid.style.gridTemplateColumns = "auto repeat(" + spec.periods.length + ", 1fr)";
    // header row
    grid.appendChild(document.createElement("div"));
    spec.periods.forEach(function (p, i) {
      var h = document.createElement("div");
      h.className = "hm-label hm-col";
      h.textContent = (i % 3 === 0) ? periodLabel(p) : "";
      grid.appendChild(h);
    });
    spec.rows.forEach(function (row) {
      var lab = document.createElement("div");
      lab.className = "hm-label";
      lab.textContent = row[0];
      grid.appendChild(lab);
      var vals = row[1].filter(function (v) { return v !== null; });
      var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
      row[1].forEach(function (v, i) {
        var cell = document.createElement("div");
        cell.className = "hm-cell";
        if (v !== null && max > min) {
          var t = (v - min) / (max - min);
          cell.style.background = ramp[Math.min(ramp.length - 1, Math.floor(t * ramp.length))];
          attachTip(cell, row[0] + " — " + spec.periods[i] + ": " + fmtFull(v));
          cell.tabIndex = 0;
        } else {
          cell.classList.add("hm-empty");
        }
        grid.appendChild(cell);
      });
    });
    holder.style.height = "auto";
    holder.appendChild(grid);
    var note = fig.querySelector(".chart-note");
    if (!note) {
      note = document.createElement("div");
      note.className = "chart-note";
      fig.appendChild(note);
    }
    if (!note.textContent) {
      note.textContent = "Shade is normalized within each row — hover a cell for the value.";
    }
  }

  /* ---- Uzbekistan choropleth --------------------------------------------- */

  var GEO_CACHE = null;

  function buildMap(fig, spec) {
    var holder = fig.querySelector(".chart-holder");
    var canvas = holder.querySelector("canvas");
    if (canvas) canvas.remove();

    function render(geo) {
      var byName = {};
      var min = Infinity, max = -Infinity;
      spec.rows.forEach(function (r) {
        byName[r[0]] = r[1];
        if (r[1] < min) min = r[1];
        if (r[1] > max) max = r[1];
      });
      var ramp = isDark() ? SEQ.slice().reverse() : SEQ;

      // bbox over all coordinates
      var minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;
      function walk(coords, fn) {
        if (typeof coords[0] === "number") { fn(coords); return; }
        coords.forEach(function (c) { walk(c, fn); });
      }
      geo.features.forEach(function (f) {
        walk(f.geometry.coordinates, function (pt) {
          if (pt[0] < minLon) minLon = pt[0];
          if (pt[0] > maxLon) maxLon = pt[0];
          if (pt[1] < minLat) minLat = pt[1];
          if (pt[1] > maxLat) maxLat = pt[1];
        });
      });
      var W = 900;
      var cosMid = Math.cos((minLat + maxLat) / 2 * Math.PI / 180);
      var H = Math.round(W * (maxLat - minLat) / ((maxLon - minLon) * cosMid));
      function px(pt) {
        var x = (pt[0] - minLon) / (maxLon - minLon) * W;
        var y = (maxLat - pt[1]) / (maxLat - minLat) * H;
        return x.toFixed(1) + "," + y.toFixed(1);
      }
      var svgNS = "http://www.w3.org/2000/svg";
      var svg = document.createElementNS(svgNS, "svg");
      svg.setAttribute("viewBox", "0 0 " + W + " " + H);
      svg.setAttribute("class", "uz-map");

      geo.features.forEach(function (f) {
        var d = "";
        var polys = f.geometry.type === "Polygon"
          ? [f.geometry.coordinates] : f.geometry.coordinates;
        polys.forEach(function (poly) {
          poly.forEach(function (ring) {
            d += "M" + ring.map(px).join("L") + "Z";
          });
        });
        var path = document.createElementNS(svgNS, "path");
        path.setAttribute("d", d);
        var name = f.properties.name;
        var v = byName[name];
        if (v !== undefined && max > min) {
          var t = (v - min) / (max - min);
          path.setAttribute("fill", ramp[Math.min(ramp.length - 1, Math.floor(t * ramp.length))]);
        } else if (v !== undefined) {
          path.setAttribute("fill", ramp[Math.floor(ramp.length / 2)]);
        } else {
          path.setAttribute("class", "uz-nodata");
        }
        attachTip(path, f.properties.label + " (" + name + "): "
          + (v === undefined ? "no data" : fmtFull(v)));
        svg.appendChild(path);
      });
      holder.style.height = "auto";
      holder.appendChild(svg);

      // min/max legend
      var legend = document.createElement("div");
      legend.className = "map-legend";
      var lo = document.createElement("span");
      lo.textContent = fmtCompact(min);
      var bar = document.createElement("span");
      bar.className = "map-legend-bar";
      bar.style.background = "linear-gradient(to right," + ramp.join(",") + ")";
      var hi = document.createElement("span");
      hi.textContent = fmtCompact(max);
      legend.appendChild(lo); legend.appendChild(bar); legend.appendChild(hi);
      holder.appendChild(legend);
    }

    if (GEO_CACHE) render(GEO_CACHE);
    else fetch("/static/uz_regions.json").then(function (r) { return r.json(); })
      .then(function (g) { GEO_CACHE = g; render(g); });
  }

  /* ---- render ------------------------------------------------------------ */

  /* ---- search funnel marimekko (DOM kind) ---------------------------------- */

  function buildMarimekko(fig, spec) {
    // week columns: width = that week's search users, stacked segments =
    // exclusive engagement split. spec.cols = [{label, total, segs:[[l,v]]}].
    var holder = fig.querySelector(".chart-holder");
    var canvas = holder.querySelector("canvas");
    if (canvas) canvas.remove();
    var pal = palette();
    // deepest engagement at the bottom: reply green, adview orange, rest blue
    var segColor = { "Searched only": pal.series[0], "Ad view only": pal.series[1],
                     "Sent a reply": pal.series[2] };
    var grand = spec.cols.reduce(function (s, c) { return s + c.total; }, 0);
    var wrap = document.createElement("div");
    wrap.className = "mekko";
    spec.cols.forEach(function (c) {
      var col = document.createElement("div");
      col.className = "mekko-col";
      col.style.width = (c.total / grand * 100) + "%";
      var segs = document.createElement("div");
      segs.className = "mekko-segs";
      c.segs.forEach(function (sg) {
        var d = document.createElement("div");
        d.className = "mekko-seg";
        var share = c.total ? sg[1] / c.total : 0;
        d.style.height = (share * 100) + "%";
        d.style.background = segColor[sg[0]] || pal.series[3];
        if (share > 0.07) {
          var s = document.createElement("span");
          s.textContent = (share * 100).toFixed(1) + "%";
          d.appendChild(s);
        }
        attachTip(d, periodLabel(c.label, "weekly") + " — " + sg[0] + ": "
                  + fmtCompact(sg[1]) + " (" + (share * 100).toFixed(1)
                  + "% of " + fmtCompact(c.total) + " search users)");
        d.tabIndex = 0;
        segs.appendChild(d);
      });
      col.appendChild(segs);
      var lab = document.createElement("div");
      lab.className = "mekko-label";
      lab.textContent = periodLabel(c.label, "weekly");
      var tot = document.createElement("div");
      tot.className = "mekko-total";
      tot.textContent = fmtCompact(c.total);
      lab.appendChild(document.createElement("br"));
      lab.appendChild(tot);
      col.appendChild(lab);
      wrap.appendChild(col);
    });
    holder.style.height = "auto";
    holder.appendChild(wrap);
    var legend = document.createElement("div");
    legend.className = "tm-legend";
    ["Sent a reply", "Ad view only", "Searched only"].forEach(function (name) {
      var chip = document.createElement("span");
      chip.className = "tm-chip";
      var sw = document.createElement("i");
      sw.style.background = segColor[name];
      chip.appendChild(sw);
      chip.appendChild(document.createTextNode(name));
      legend.appendChild(chip);
    });
    holder.appendChild(legend);
  }

  /* ---- squarified treemap (DOM kind) -------------------------------------- */

  function squarify(items, x, y, w, h, out) {
    // items sorted descending, each {v, ...}; classic squarify layout.
    if (!items.length) return;
    var total = items.reduce(function (s, it) { return s + it.v; }, 0);
    if (total <= 0) return;
    var scale = (w * h) / total;
    var row = [], rest = items.slice();

    function worst(row, side) {
      var sum = row.reduce(function (s, it) { return s + it.v * scale; }, 0);
      var mx = 0;
      row.forEach(function (it) {
        var a = it.v * scale;
        var r = Math.max((side * side * a) / (sum * sum), (sum * sum) / (side * side * a));
        if (r > mx) mx = r;
      });
      return mx;
    }

    while (rest.length) {
      var side = Math.min(w, h);
      row.push(rest[0]);
      if (row.length > 1 && worst(row, side) > worst(row.slice(0, -1), side)) {
        row.pop();
        var sum = row.reduce(function (s, it) { return s + it.v * scale; }, 0);
        if (w >= h) {  // lay the row as a vertical strip on the left
          var sw = sum / h;
          var yy = y;
          row.forEach(function (it) {
            var ih = it.v * scale / sw;
            out.push({ it: it, x: x, y: yy, w: sw, h: ih });
            yy += ih;
          });
          x += sw; w -= sw;
        } else {       // horizontal strip on top
          var sh = sum / w;
          var xx = x;
          row.forEach(function (it) {
            var iw = it.v * scale / sh;
            out.push({ it: it, x: xx, y: y, w: iw, h: sh });
            xx += iw;
          });
          y += sh; h -= sh;
        }
        row = [];
      } else {
        rest.shift();
      }
    }
    if (row.length) {
      var sum2 = row.reduce(function (s, it) { return s + it.v * scale; }, 0);
      if (w >= h) {
        var sw2 = sum2 / h, yy2 = y;
        row.forEach(function (it) {
          var ih = it.v * scale / sw2;
          out.push({ it: it, x: x, y: yy2, w: sw2, h: ih });
          yy2 += ih;
        });
      } else {
        var sh2 = sum2 / w, xx2 = x;
        row.forEach(function (it) {
          var iw = it.v * scale / sh2;
          out.push({ it: it, x: xx2, y: y, w: iw, h: sh2 });
          xx2 += iw;
        });
      }
    }
  }

  function buildTreemap(fig, spec) {
    // spec.rows = [[groupL1, labelL2, value]]; tile color = L1 group.
    var holder = fig.querySelector(".chart-holder");
    var canvas = holder.querySelector("canvas");
    if (canvas) canvas.remove();
    var pal = palette();
    var box = document.createElement("div");
    box.className = "treemap";
    holder.style.height = "auto";
    holder.appendChild(box);
    var W = box.clientWidth || holder.clientWidth || 800;
    var H = 380;
    box.style.height = H + "px";
    var total = 0;
    var groups = [];
    spec.rows.forEach(function (r) {
      if (groups.indexOf(r[0]) < 0) groups.push(r[0]);
      total += r[2];
    });
    var items = spec.rows.map(function (r) {
      return { g: r[0], label: r[1], v: r[2] };
    }).filter(function (it) { return it.v > 0; })
      .sort(function (a, b) { return b.v - a.v; });
    var tiles = [];
    squarify(items, 0, 0, W, H, tiles);
    tiles.forEach(function (t) {
      var d = document.createElement("div");
      d.className = "tm-tile";
      d.style.left = t.x.toFixed(1) + "px";
      d.style.top = t.y.toFixed(1) + "px";
      d.style.width = Math.max(0, t.w - 2).toFixed(1) + "px";
      d.style.height = Math.max(0, t.h - 2).toFixed(1) + "px";
      d.style.background = pal.series[groups.indexOf(t.it.g) % pal.series.length];
      if (t.w > 60 && t.h > 26) {
        var s = document.createElement("span");
        s.textContent = t.it.label;
        d.appendChild(s);
      }
      attachTip(d, t.it.g + " › " + t.it.label + ": " + fmtFull(t.it.v)
                + " (" + (t.it.v / total * 100).toFixed(1) + "%)");
      d.tabIndex = 0;
      box.appendChild(d);
    });
    // legend: one chip per L1 group
    var legend = document.createElement("div");
    legend.className = "tm-legend";
    groups.forEach(function (gname, i) {
      var chip = document.createElement("span");
      chip.className = "tm-chip";
      var sw = document.createElement("i");
      sw.style.background = pal.series[i % pal.series.length];
      chip.appendChild(sw);
      chip.appendChild(document.createTextNode(gname));
      legend.appendChild(chip);
    });
    holder.appendChild(legend);
  }

  /* ---- sortable keywords table (DOM kind) ---------------------------------- */

  var KW_ROW_CAP = 400;

  function buildKwTable(fig, spec) {
    var holder = fig.querySelector(".chart-holder");
    var canvas = holder.querySelector("canvas");
    if (canvas) canvas.remove();
    holder.style.height = "auto";

    var state = { platform: "all", text: "", sortKey: "searches", dir: -1 };
    var platIdx = spec.columns.findIndex(function (c) { return c.key === "platform"; });
    var kwIdx = spec.columns.findIndex(function (c) { return c.key === "keyword"; });

    var controls = document.createElement("div");
    controls.className = "kw-controls";
    var chips = document.createElement("div");
    chips.className = "kw-chips";
    ["all"].concat(spec.platforms).forEach(function (p) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "kw-chip" + (p === "all" ? " active" : "");
      b.textContent = p === "all" ? "All platforms" : p;
      b.addEventListener("click", function () {
        state.platform = p;
        chips.querySelectorAll(".kw-chip").forEach(function (c) {
          c.classList.remove("active");
        });
        b.classList.add("active");
        renderBody();
      });
      chips.appendChild(b);
    });
    controls.appendChild(chips);
    var input = document.createElement("input");
    input.type = "search";
    input.placeholder = "Filter keywords…";
    input.className = "kw-filter";
    input.addEventListener("input", function () {
      state.text = input.value.trim().toLowerCase();
      renderBody();
    });
    controls.appendChild(input);
    holder.appendChild(controls);

    var table = document.createElement("table");
    table.className = "kwtable";
    var thead = document.createElement("thead");
    var hr = document.createElement("tr");
    spec.columns.forEach(function (col, i) {
      var th = document.createElement("th");
      th.textContent = col.label + " ";
      var arw = document.createElement("span");
      arw.className = "arw";
      th.appendChild(arw);
      if (col.num) th.classList.add("num");
      th.addEventListener("click", function () {
        if (state.sortKey === col.key) state.dir = -state.dir;
        else { state.sortKey = col.key; state.dir = col.num ? -1 : 1; }
        renderBody();
      });
      hr.appendChild(th);
    });
    thead.appendChild(hr);
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    table.appendChild(tbody);
    holder.appendChild(table);
    var capNote = document.createElement("div");
    capNote.className = "kw-cap";
    holder.appendChild(capNote);

    function renderBody() {
      var si = spec.columns.findIndex(function (c) { return c.key === state.sortKey; });
      var rows = spec.rows.filter(function (r) {
        if (state.platform !== "all" && r[platIdx] !== state.platform) return false;
        if (state.text && String(r[kwIdx]).toLowerCase().indexOf(state.text) < 0) return false;
        return true;
      });
      var isNum = !!spec.columns[si].num;
      rows.sort(function (a, b) {
        var x = a[si], y = b[si];
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        if (isNum) return (x - y) * state.dir;
        return String(x).localeCompare(String(y)) * state.dir;
      });
      thead.querySelectorAll("th").forEach(function (th, i) {
        th.querySelector(".arw").textContent =
          i === si ? (state.dir > 0 ? "▲" : "▼") : "";
      });
      tbody.textContent = "";
      var shown = rows.slice(0, KW_ROW_CAP);
      shown.forEach(function (r) {
        var tr = document.createElement("tr");
        spec.columns.forEach(function (col, i) {
          var td = document.createElement("td");
          if (col.num) {
            td.classList.add("num");
            td.textContent = col.pct ? fmtCompact(r[i], true)
              : (r[i] === null || r[i] === undefined ? "–"
                 : (+r[i]).toLocaleString());
          } else {
            td.textContent = r[i] === null || r[i] === undefined ? "–" : r[i];
          }
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      capNote.textContent = rows.length > KW_ROW_CAP
        ? "Showing top " + KW_ROW_CAP + " of " + rows.length.toLocaleString()
          + " keywords — narrow with the filter."
        : rows.length.toLocaleString() + " keywords.";
    }
    renderBody();
  }

  function renderCharts() {
    document.querySelectorAll(".chart-card[data-chart]").forEach(function (fig) {
      var spec;
      try { spec = JSON.parse(fig.dataset.chart); } catch (e) { return; }
      var canvas = fig.querySelector("canvas");
      if (!spec) return;
      if (spec.kind === "heatmap") {
        if (canvas) buildHeatmap(fig, spec);
        return;
      }
      if (spec.kind === "matrix") {
        if (canvas) buildMatrix(fig, spec);
        return;
      }
      if (spec.kind === "map") {
        if (canvas) buildMap(fig, spec);
        return;
      }
      if (spec.kind === "marimekko") {
        if (canvas) buildMarimekko(fig, spec);
        return;
      }
      if (spec.kind === "treemap") {
        if (canvas) buildTreemap(fig, spec);
        return;
      }
      if (spec.kind === "kwtable") {
        if (canvas) buildKwTable(fig, spec);
        return;
      }
      if (!canvas) return;
      var holder = fig.querySelector(".chart-holder");
      if ((spec.kind === "barh" || spec.kind === "divergingbarh") && spec.rows.length > 8) {
        holder.style.height = (spec.rows.length * 28 + 60) + "px";
      }
      if (spec.kind === "barh") buildBarh(canvas, spec);
      else if (spec.kind === "divergingbarh") buildDivergingBarh(canvas, spec);
      else if (spec.kind === "stacked") buildStacked(canvas, spec);
      else if (spec.kind === "sharearea") buildShareArea(canvas, spec);
      else buildLine(canvas, spec);
    });
  }

  function renderAll() {
    charts.forEach(function (c) { c.destroy(); });
    charts = [];
    renderSparks();
    renderCharts();
  }

  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  initFilters();
  initInfo();
  initFullscreen();
  initLoading();
  renderCards();
  renderAll();

  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
    // heatmaps rebuild their DOM; simplest correct behavior is a reload
    window.location.reload();
  });
})();
