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
    document.querySelectorAll(".grain-toggle a, nav.tabs a, .filters-clear")
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
      if (!canvas) return;
      var holder = fig.querySelector(".chart-holder");
      if ((spec.kind === "barh" || spec.kind === "divergingbarh") && spec.rows.length > 8) {
        holder.style.height = (spec.rows.length * 28 + 60) + "px";
      }
      if (spec.kind === "barh") buildBarh(canvas, spec);
      else if (spec.kind === "divergingbarh") buildDivergingBarh(canvas, spec);
      else if (spec.kind === "stacked") buildStacked(canvas, spec);
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
