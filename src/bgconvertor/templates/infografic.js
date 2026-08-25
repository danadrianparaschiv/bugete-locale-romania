"use strict";
/* Infografic buget local — SVG generat local, fără biblioteci externe.
   Datele vin din analysis.json (blocul "infografic"), doar linii verificate. */
(function () {
  var IG = window.IG;
  if (!IG || !IG.capitole) return;
  var C = { ok: "#1a7f37", warn: "#b8860b", bad: "#b02a37", blue: "#0b57d0",
            ink: "#1a1d21", dim: "#6a7178", grid: "#e3e6e9" };
  var GRUP = { proprii: C.ok, stat: C.warn, ue: C.blue };
  var GRUP_L = { proprii: "venit propriu", stat: "de la stat", ue: "fonduri UE" };
  var TOT = IG.total_cheltuieli;

  function nf(n, d) {
    d = d || 0;
    try { return new Intl.NumberFormat("ro-RO", { minimumFractionDigits: d, maximumFractionDigits: d }).format(n); }
    catch (e) { return n.toFixed(d); }
  }
  function mii(v) { return nf(v, 0); }
  function amount(v) { return mii(v) + " mii lei"; }
  function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
  function el(id) { return document.getElementById(id); }
  function put(id, html) { var e = el(id); if (e) e.innerHTML = html; }
  function hide(id) { var e = el(id); if (e) e.style.display = "none"; }

  function selectBar(id, key) {
    Array.prototype.forEach.call(document.querySelectorAll("#" + id + " .brow"), function (row) {
      var on = row.getAttribute("data-key") === key;
      row.className = "brow" + (on ? " sel" : "");
      row.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function renderBarRows(id, rows, valueOf, colorOf, selected, onSelect) {
    var max = Math.max.apply(null, rows.map(valueOf));
    put(id, rows.map(function (row) {
      var key = row.cod, value = valueOf(row), on = key === selected;
      return '<div class="brow' + (on ? " sel" : "") + '" data-key="' + esc(key) +
        '" tabindex="0" role="button" aria-pressed="' + (on ? "true" : "false") + '">' +
        '<span class="bt">' + esc(row.nume.replace(/^CAP\.\s*/i, "")) + "</span>" +
        '<span class="bg2"><i style="background:' + colorOf(row) + ";width:" +
        (value / max * 100).toFixed(1) + '%"></i></span>' +
        '<span class="bv">' + amount(value) + "</span></div>";
    }).join(""));
    Array.prototype.forEach.call(document.querySelectorAll("#" + id + " .brow"), function (row) {
      var activate = function () { onSelect(row.getAttribute("data-key")); };
      row.onclick = activate;
      row.onkeydown = function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(); }
      };
    });
  }

  /* ---- venituri: bare interactive + detaliu ---- */
  if (IG.venituri) {
    var ven = IG.venituri.surse, TV = IG.venituri.total, venSel = ven[0].cod;
    function revenueDetail(cod) {
      venSel = cod;
      var source = null;
      ven.forEach(function (item) { if (item.cod === cod) source = item; });
      if (!source) return;
      selectBar("ig-ven-bars", cod);
      put("ig-ven-detail", '<div class="ttl"><h3>' + esc(source.nume) +
        '</h3><span class="amt">' + amount(source.val) + " · " +
        nf(source.val / TV * 100, 1) + "% din veniturile planificate</span></div>" +
        '<p class="hint">Cod bugetar ' + esc(source.cod) + ' · <span class="tag" style="color:' +
        (GRUP[source.grup] || C.dim) + '">' +
        esc(GRUP_L[source.grup] || "sursă neclasificată") + "</span></p>");
    }
    renderBarRows("ig-ven-bars", ven, function (s) { return s.val; },
      function (s) { return GRUP[s.grup] || C.blue; }, venSel, revenueDetail);
    revenueDetail(venSel);
  } else { hide("ig-sec-ven"); }

  /* ---- capitole: bare interactive + detaliu ---- */
  var mode = "val", sel = IG.capitole[0].cod;
  function capVal(c) { return mode === "func" ? (c.func || 0) : mode === "dezv" ? (c.dezv || 0) : c.val; }
  function drawExpenseBars() {
    var rows = IG.capitole.filter(function (c) { return capVal(c) > 0; })
      .sort(function (a, b) { return capVal(b) - capVal(a); });
    if (!rows.length) { put("ig-bars", '<p class="note">Fără date pe această secțiune.</p>'); put("ig-detail", ""); return; }
    if (!rows.some(function (c) { return c.cod === sel; })) sel = rows[0].cod;
    var col = mode === "func" ? C.ok : mode === "dezv" ? C.warn : C.blue;
    renderBarRows("ig-bars", rows, capVal, function () { return col; }, sel, expenseDetail);
    expenseDetail(sel);
  }
  function expenseDetail(cod) {
    sel = cod;
    var c = null;
    IG.capitole.forEach(function (k) { if (k.cod === cod) c = k; });
    if (!c) return;
    selectBar("ig-bars", cod);
    var value = capVal(c);
    var denominator = mode === "func" ? IG.sectiuni.functionare :
      mode === "dezv" ? IG.sectiuni.dezvoltare : TOT;
    var shareLabel = mode === "func" ? "% din secțiunea de funcționare" :
      mode === "dezv" ? "% din secțiunea de dezvoltare" : "% din buget";
    var children = mode === "val" ? c.copii : [];
    var fd = (mode === "val" && c.func != null && c.dezv != null)
      ? "Funcționare " + amount(c.func) + " · dezvoltare " + amount(c.dezv) : "";
    var mx = children.length ? children[0].val : 1;
    put("ig-detail", '<div class="ttl"><h3>' + esc(c.nume.replace(/^CAP\.\s*/i, "")) + '</h3><span class="amt">' +
      amount(value) + " · " + nf(value / denominator * 100, 1) + shareLabel + "</span></div>" +
      (fd ? '<p class="hint">' + fd + (children.length ? " — principalele subcapitole:" : "") + "</p>" : "") +
      '<div class="bars">' + children.map(function (k) {
        return '<div class="brow" style="cursor:default"><span class="bt">' + esc(k.nume) + "</span>" +
          '<span class="bg2"><i style="background:' + C.dim + ";opacity:.55;width:" + (k.val / mx * 100).toFixed(1) + '%"></i></span>' +
          '<span class="bv">' + amount(k.val) + "</span></div>";
      }).join("") + "</div>");
  }
  drawExpenseBars();
  Array.prototype.forEach.call(document.querySelectorAll("#ig-tabs button"), function (b) {
    b.onclick = function () {
      Array.prototype.forEach.call(document.querySelectorAll("#ig-tabs button"), function (k) {
        k.className = ""; k.setAttribute("aria-pressed", "false");
      });
      b.className = "on"; b.setAttribute("aria-pressed", "true");
      mode = b.getAttribute("data-mode"); drawExpenseBars();
    };
  });
  if (!IG.sectiuni) hide("ig-tabs");

  /* ---- din 100 de lei ---- */
  var pal = [C.blue, C.warn, C.ok, C.bad, "#7c5cbf", "#9aa4ad"], parts = [], used = 0;
  IG.capitole.slice(0, 5).forEach(function (c, i) {
    var p = Math.round(c.val / TOT * 100);
    if (p > 0) { used += p; parts.push({ n: c.nume.replace(/^CAP\.\s*/i, "").toLowerCase(), v: p, c: pal[i] }); }
  });
  if (used < 100) parts.push({ n: "restul capitolelor", v: 100 - used, c: pal[5] });
  put("ig-hundred", parts.map(function (p) {
    var out = "";
    for (var i = 0; i < p.v; i++) out += '<span class="coin" style="background:' + p.c + '" title="' + esc(p.n) + '"></span>';
    return out;
  }).join(""));
  put("ig-hundred-legend", parts.map(function (p) {
    return '<span><i style="background:' + p.c + '"></i><b>' + p.v + " lei</b> " + esc(p.n) + "</span>";
  }).join(""));

  /* ---- coloane generice (trimestre / ani) ---- */
  function columns(cats, stacks, line, ylabel) {
    var W = 680, H = 360, L = 76, Rr = 16, T = 18, B = 40, pw = W - L - Rr, ph = H - T - B, i, j, max = 0;
    for (i = 0; i < cats.length; i++) {
      var s = 0;
      for (j = 0; j < stacks.length; j++) s += stacks[j].data[i];
      max = Math.max(max, s);
    }
    if (line) for (i = 0; i < line.data.length; i++) max = Math.max(max, line.data[i]);
    var step = Math.pow(10, Math.floor(Math.log(max) / Math.LN10));
    max = Math.ceil(max / (step / 2)) * (step / 2);
    var y = function (v) { return T + ph - v / max * ph; }, bw = pw / cats.length * 0.52, out = [];
    for (i = 0; i <= 4; i++) {
      var v = max / 4 * i, yy = y(v);
      out.push('<line x1="' + L + '" y1="' + yy.toFixed(1) + '" x2="' + (W - Rr) + '" y2="' + yy.toFixed(1) + '" stroke="' + C.grid + '"/>' +
        '<text x="' + (L - 8) + '" y="' + (yy + 4).toFixed(1) + '" text-anchor="end" fill="' + C.dim + '" font-size="11">' + mii(v) + "</text>");
    }
    for (i = 0; i < cats.length; i++) {
      var cxc = L + pw / cats.length * (i + 0.5), acc = 0;
      for (j = 0; j < stacks.length; j++) {
        var v2 = stacks[j].data[i], hh = v2 / max * ph;
        out.push('<rect x="' + (cxc - bw / 2).toFixed(1) + '" y="' + y(acc + v2).toFixed(1) + '" width="' + bw.toFixed(1) +
          '" height="' + hh.toFixed(1) + '" fill="' + stacks[j].color + '"><title>' + esc(cats[i]) + " · " + esc(stacks[j].name) +
          ": " + amount(v2) + "</title></rect>");
        acc += v2;
      }
      out.push('<text x="' + cxc.toFixed(1) + '" y="' + (H - 14) + '" text-anchor="middle" fill="' + C.ink + '" font-size="12">' + esc(cats[i]) + "</text>");
    }
    if (line) {
      var pts = [];
      for (i = 0; i < line.data.length; i++) pts.push((L + pw / cats.length * (i + 0.5)).toFixed(1) + "," + y(line.data[i]).toFixed(1));
      out.push('<polyline points="' + pts.join(" ") + '" fill="none" stroke="' + line.color + '" stroke-width="2.5" stroke-dasharray="6 5"/>');
      for (i = 0; i < line.data.length; i++)
        out.push('<circle cx="' + (L + pw / cats.length * (i + 0.5)).toFixed(1) + '" cy="' + y(line.data[i]).toFixed(1) +
          '" r="4" fill="' + line.color + '"><title>' + esc(line.name) + " " + esc(cats[i]) + ": " + amount(line.data[i]) + "</title></circle>");
    }
    out.push('<text x="' + L + '" y="' + (T - 5) + '" fill="' + C.dim + '" font-size="10.5">' + esc(ylabel) + "</text>");
    return '<svg viewBox="0 0 ' + W + " " + H + '" role="img">' + out.join("") + "</svg>";
  }

  if (IG.trim) {
    put("ig-trim", columns(["Trim. I", "Trim. II", "Trim. III", "Trim. IV"],
      [{ name: "Funcționare", data: IG.trim.functionare, color: C.ok },
       { name: "Dezvoltare", data: IG.trim.dezvoltare, color: C.warn }],
      IG.trim.venituri ? { name: "Venituri", data: IG.trim.venituri, color: C.blue } : null,
      "mii lei"));
  } else { hide("ig-sec-trim"); }

  if (IG.ani) {
    var years = ["2026", "2027", "2028", "2029"], ch = IG.ani.cheltuieli;
    put("ig-ani", columns(years,
      [{ name: "Cheltuieli", data: ch, color: C.warn }],
      IG.ani.venituri ? { name: "Venituri", data: IG.ani.venituri, color: C.blue } : null,
      "mii lei"));
    var d = (ch[1] - ch[0]) / ch[0] * 100;
    var msg = d <= -5 ? "o scădere de " : d >= 5 ? "o creștere de " : "o variație de ";
    put("ig-ani-note", "Estimările primăriei pentru 2027 arată " + msg + "<b>" + nf(Math.abs(d), 1) +
      "%</b> față de " + years[0] + ", conform coloanelor de proiecție din același document.");
  } else { hide("ig-sec-ani"); }
})();
