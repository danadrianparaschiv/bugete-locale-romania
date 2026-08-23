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
  function mil(v) { return nf(v / 1000, 1); }
  function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
  function el(id) { return document.getElementById(id); }
  function put(id, html) { var e = el(id); if (e) e.innerHTML = html; }
  function hide(id) { var e = el(id); if (e) e.style.display = "none"; }

  /* ---- venituri: donut + listă ---- */
  if (IG.venituri) {
    var ven = IG.venituri.surse, TV = IG.venituri.total;
    var W = 640, H = 400, cx = 300, cy = 190, R = 112, r = 72, a = -Math.PI / 2, arcs = [], labels = [];
    ven.forEach(function (s, i) {
      var ang = s.val / TV * Math.PI * 2, b = a + ang, mid = (a + b) / 2, big = ang > Math.PI ? 1 : 0;
      var x0 = cx + R * Math.cos(a), y0 = cy + R * Math.sin(a), x1 = cx + R * Math.cos(b), y1 = cy + R * Math.sin(b);
      var x2 = cx + r * Math.cos(b), y2 = cy + r * Math.sin(b), x3 = cx + r * Math.cos(a), y3 = cy + r * Math.sin(a);
      arcs.push('<path d="M' + x0.toFixed(1) + " " + y0.toFixed(1) + "A" + R + " " + R + " 0 " + big + " 1 " + x1.toFixed(1) + " " + y1.toFixed(1) +
        "L" + x2.toFixed(1) + " " + y2.toFixed(1) + "A" + r + " " + r + " 0 " + big + " 0 " + x3.toFixed(1) + " " + y3.toFixed(1) +
        'Z" fill="' + GRUP[s.grup] + '" stroke="#fff" stroke-width="2" opacity="' + Math.max(1 - i * 0.05, 0.55).toFixed(2) +
        '"><title>' + esc(s.nume) + ": " + mil(s.val) + " mil. lei (" + nf(s.val / TV * 100, 1) + "%)</title></path>");
      if (s.val / TV >= 0.06) {
        var right = Math.cos(mid) >= 0, ex = cx + (R + 28) * Math.cos(mid), ey = cy + (R + 28) * Math.sin(mid), tx = right ? ex + 6 : ex - 6;
        labels.push('<polyline points="' + (cx + (R + 2) * Math.cos(mid)).toFixed(1) + "," + (cy + (R + 2) * Math.sin(mid)).toFixed(1) + " " +
          ex.toFixed(1) + "," + ey.toFixed(1) + '" fill="none" stroke="' + C.grid + '" stroke-width="1"/>' +
          '<text x="' + tx.toFixed(1) + '" y="' + ey.toFixed(1) + '" fill="' + C.ink + '" font-size="12.5" text-anchor="' + (right ? "start" : "end") + '">' +
          esc(s.nume.length > 30 ? s.nume.slice(0, 28) + "…" : s.nume) + '</text>' +
          '<text x="' + tx.toFixed(1) + '" y="' + (ey + 15).toFixed(1) + '" fill="' + C.dim + '" font-size="11.5" text-anchor="' + (right ? "start" : "end") + '">' +
          mil(s.val) + " mil. · " + nf(s.val / TV * 100, 1) + "%</text>");
      }
      a = b;
    });
    put("ig-ven", '<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Structura veniturilor">' + arcs.join("") + labels.join("") +
      '<text x="' + cx + '" y="' + (cy - 2) + '" text-anchor="middle" fill="' + C.ink + '" font-size="24" font-weight="700">' + mil(TV) + "</text>" +
      '<text x="' + cx + '" y="' + (cy + 18) + '" text-anchor="middle" fill="' + C.dim + '" font-size="12">mil. lei venituri</text></svg>');
    put("ig-ven-list", ven.map(function (s) {
      return '<li><div class="vrow"><span>' + esc(s.nume) + ' <span class="tag" style="color:' + GRUP[s.grup] + '">' + GRUP_L[s.grup] + "</span></span>" +
        '<span class="a">' + mil(s.val) + " mil. · " + nf(s.val / TV * 100, 1) + "%</span></div>" +
        '<div class="vbar"><i style="background:' + GRUP[s.grup] + ";width:" + Math.max(s.val / ven[0].val * 100, 1).toFixed(1) + '%"></i></div></li>';
    }).join(""));
  } else { hide("ig-sec-ven"); }

  /* ---- capitole: bare interactive + detaliu ---- */
  var mode = "val", sel = IG.capitole[0].cod;
  function capVal(c) { return mode === "func" ? (c.func || 0) : mode === "dezv" ? (c.dezv || 0) : c.val; }
  function drawBars() {
    var rows = IG.capitole.filter(function (c) { return capVal(c) > 0; })
      .sort(function (a, b) { return capVal(b) - capVal(a); });
    if (!rows.length) { put("ig-bars", '<p class="note">Fără date pe această secțiune.</p>'); put("ig-detail", ""); return; }
    var max = capVal(rows[0]), col = mode === "func" ? C.ok : mode === "dezv" ? C.warn : C.blue;
    put("ig-bars", rows.map(function (c) {
      return '<div class="brow' + (c.cod === sel ? " sel" : "") + '" data-cod="' + c.cod + '" tabindex="0" role="button">' +
        '<span class="bt">' + esc(c.nume.replace(/^CAP\.\s*/i, "")) + "</span>" +
        '<span class="bg2"><i style="background:' + col + ";width:" + (capVal(c) / max * 100).toFixed(1) + '%"></i></span>' +
        '<span class="bv">' + mil(capVal(c)) + " mil.</span></div>";
    }).join(""));
    Array.prototype.forEach.call(document.querySelectorAll("#ig-bars .brow"), function (row) {
      row.onclick = function () { detail(row.getAttribute("data-cod")); };
      row.onkeydown = function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); detail(row.getAttribute("data-cod")); } };
    });
  }
  function detail(cod) {
    sel = cod;
    var c = null;
    IG.capitole.forEach(function (k) { if (k.cod === cod) c = k; });
    if (!c) return;
    Array.prototype.forEach.call(document.querySelectorAll("#ig-bars .brow"), function (row) {
      row.className = "brow" + (row.getAttribute("data-cod") === cod ? " sel" : "");
    });
    var fd = (c.func != null && c.dezv != null)
      ? "Funcționare " + mil(c.func) + " mil. · dezvoltare " + mil(c.dezv) + " mil." : "";
    var mx = c.copii.length ? c.copii[0].val : 1;
    put("ig-detail", '<div class="ttl"><h3>' + esc(c.nume.replace(/^CAP\.\s*/i, "")) + '</h3><span class="amt">' +
      mil(c.val) + " mil. lei · " + nf(c.val / TOT * 100, 1) + "% din buget</span></div>" +
      (fd ? '<p class="hint">' + fd + (c.copii.length ? " — principalele subcapitole:" : "") + "</p>" : "") +
      '<div class="bars">' + c.copii.map(function (k) {
        return '<div class="brow" style="cursor:default"><span class="bt">' + esc(k.nume) + "</span>" +
          '<span class="bg2"><i style="background:' + C.dim + ";opacity:.55;width:" + (k.val / mx * 100).toFixed(1) + '%"></i></span>' +
          '<span class="bv">' + mil(k.val) + " mil.</span></div>";
      }).join("") + "</div>");
  }
  drawBars(); detail(sel);
  Array.prototype.forEach.call(document.querySelectorAll("#ig-tabs button"), function (b) {
    b.onclick = function () {
      Array.prototype.forEach.call(document.querySelectorAll("#ig-tabs button"), function (k) { k.className = ""; });
      b.className = "on"; mode = b.getAttribute("data-mode"); drawBars();
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
    var W = 680, H = 360, L = 62, Rr = 16, T = 18, B = 40, pw = W - L - Rr, ph = H - T - B, i, j, max = 0;
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
        '<text x="' + (L - 8) + '" y="' + (yy + 4).toFixed(1) + '" text-anchor="end" fill="' + C.dim + '" font-size="11">' + nf(v / 1000, 0) + "</text>");
    }
    for (i = 0; i < cats.length; i++) {
      var cxc = L + pw / cats.length * (i + 0.5), acc = 0;
      for (j = 0; j < stacks.length; j++) {
        var v2 = stacks[j].data[i], hh = v2 / max * ph;
        out.push('<rect x="' + (cxc - bw / 2).toFixed(1) + '" y="' + y(acc + v2).toFixed(1) + '" width="' + bw.toFixed(1) +
          '" height="' + hh.toFixed(1) + '" fill="' + stacks[j].color + '"><title>' + esc(cats[i]) + " · " + esc(stacks[j].name) +
          ": " + mil(v2) + " mil. lei</title></rect>");
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
          '" r="4" fill="' + line.color + '"><title>' + esc(line.name) + " " + esc(cats[i]) + ": " + mil(line.data[i]) + " mil. lei</title></circle>");
    }
    out.push('<text x="' + L + '" y="' + (T - 5) + '" fill="' + C.dim + '" font-size="10.5">' + esc(ylabel) + "</text>");
    return '<svg viewBox="0 0 ' + W + " " + H + '" role="img">' + out.join("") + "</svg>";
  }

  if (IG.trim) {
    put("ig-trim", columns(["Trim. I", "Trim. II", "Trim. III", "Trim. IV"],
      [{ name: "Funcționare", data: IG.trim.functionare, color: C.ok },
       { name: "Dezvoltare", data: IG.trim.dezvoltare, color: C.warn }],
      IG.trim.venituri ? { name: "Venituri", data: IG.trim.venituri, color: C.blue } : null,
      "milioane lei"));
  } else { hide("ig-sec-trim"); }

  if (IG.ani) {
    var years = ["2026", "2027", "2028", "2029"], ch = IG.ani.cheltuieli;
    put("ig-ani", columns(years,
      [{ name: "Cheltuieli", data: ch, color: C.warn }],
      IG.ani.venituri ? { name: "Venituri", data: IG.ani.venituri, color: C.blue } : null,
      "milioane lei"));
    var d = (ch[1] - ch[0]) / ch[0] * 100;
    var msg = d <= -5 ? "o scădere de " : d >= 5 ? "o creștere de " : "o variație de ";
    put("ig-ani-note", "Estimările primăriei pentru 2027 arată " + msg + "<b>" + nf(Math.abs(d), 1) +
      "%</b> față de " + years[0] + ", conform coloanelor de proiecție din același document.");
  } else { hide("ig-sec-ani"); }
})();
