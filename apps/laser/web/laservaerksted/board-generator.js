/* Skoleværkstedet / Mathias Kusk Damgaard. Pure generator extracted from
   scripts/plade.js, snapshot SHA-256:
   be591444c19ad40042f9136778824ca0f9e3656fa8c37bc2926cab35a4e25990.
   Website UI and download handling are provided by the host app here. */
/* =========================================================================
   Brætspilsfabrikken · spilleplade-generatoren
   Laver en spilleplade som SVG til laserskæreren. Lagene er delt efter farve:
     rød  (#FF0000)  = skær ud (pladens kant)
     blå  (#0000FF)  = streg (felter, tal, pynt, pile)
     sort (#000000)  = gravér (fyldte stjerner og pilespidser)
   Mål i millimeter. Ingen afhængigheder.
   ========================================================================= */

(function () {
  'use strict';

  var OPTIONS = {
    bane: [['slange', 'Slange'], ['firkant', 'Firkantet spiral'], ['spiral', 'Rund spiral'], ['bugtet', 'Bugtet sti'],
      ['ring', 'Ring langs kanten'], ['rundring', 'Rund ring'], ['blomst', 'Blomsterring']],
    felt: [['firkant', 'Firkanter'], ['cirkel', 'Cirkler'], ['sekskant', 'Sekskanter'], ['sten', 'Sten']],
    laengde: [['kort', 'Kort'], ['mellem', 'Mellem'], ['lang', 'Lang']],
    special: [['ingen', 'Ingen'], ['stjerner', 'Stjerner'], ['spoergsmaal', 'Spørgsmål'], ['genveje', 'Genveje']],
    tema: [['ingen', 'Ingen'], ['skov', 'Skov'], ['hav', 'Hav'], ['rum', 'Rummet'], ['slot', 'Slot']],
    tal: [['ja', 'Med tal'], ['nej', 'Uden tal']],
    stoerrelse: [['200', '20 × 20 cm'], ['300', '30 × 30 cm'], ['370', '37 × 37 cm']]
  };
  var TARGET = { kort: 30, mellem: 45, lang: 60 };
  var LASER_MAX = 375;   // laserens skæreflade er 600 × 375 mm, så en kvadratisk plade må højst være 375 mm
  var state = { bane: 'slange', felt: 'firkant', laengde: 'mellem', special: 'stjerner', tema: 'skov', tal: 'ja', stoerrelse: '300', seed: 1 };

  /* ---------------------------------------------------------------- tilfældighed med frø */

  function rng(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function f(n) { return (Math.round(n * 100) / 100).toString(); }

  /* ---------------------------------------------------------------- stregskrift (en streg pr. bogstav, god til laser) */

  var GLYPH = {
    '0': [[[1, 0], [3, 0], [4, 1], [4, 5], [3, 6], [1, 6], [0, 5], [0, 1], [1, 0]]],
    '1': [[[1, 1], [2, 0], [2, 6]], [[1, 6], [3, 6]]],
    '2': [[[0, 1], [1, 0], [3, 0], [4, 1], [4, 2], [0, 6], [4, 6]]],
    '3': [[[0, 0], [4, 0], [2, 2.5], [3, 2.5], [4, 3.5], [4, 5], [3, 6], [1, 6], [0, 5]]],
    '4': [[[3, 6], [3, 0], [0, 4], [4, 4]]],
    '5': [[[4, 0], [0, 0], [0, 2.5], [3, 2.5], [4, 3.5], [4, 5], [3, 6], [0, 6]]],
    '6': [[[3, 0], [1, 0], [0, 1], [0, 5], [1, 6], [3, 6], [4, 5], [4, 3.5], [3, 2.5], [0, 2.5]]],
    '7': [[[0, 0], [4, 0], [1.5, 6]]],
    '8': [[[1, 0], [3, 0], [4, 1], [4, 2], [3, 3], [1, 3], [0, 4], [0, 5], [1, 6], [3, 6], [4, 5], [4, 4], [3, 3], [1, 3], [0, 2], [0, 1], [1, 0]]],
    '9': [[[4, 3.5], [1, 3.5], [0, 2.5], [0, 1], [1, 0], [3, 0], [4, 1], [4, 5], [3, 6], [1, 6]]],
    'S': [[[4, 1], [3, 0], [1, 0], [0, 1], [0, 2], [1, 3], [3, 3], [4, 4], [4, 5], [3, 6], [1, 6], [0, 5]]],
    'T': [[[0, 0], [4, 0]], [[2, 0], [2, 6]]],
    'A': [[[0, 6], [2, 0], [4, 6]], [[0.7, 4], [3.3, 4]]],
    'R': [[[0, 6], [0, 0], [3, 0], [4, 1], [4, 2], [3, 3], [0, 3]], [[2, 3], [4, 6]]],
    'M': [[[0, 6], [0, 0], [2, 3], [4, 0], [4, 6]]],
    'L': [[[0, 0], [0, 6], [4, 6]]],
    'Å': [[[0, 6], [2, 0.6], [4, 6]], [[0.8, 4.2], [3.2, 4.2]], [[2, -0.4], [2.7, -1.1], [2, -1.8], [1.3, -1.1], [2, -0.4]]],
    '?': [[[0, 1], [1, 0], [3, 0], [4, 1], [4, 2], [2, 3.4], [2, 4.4]], [[2, 5.4], [2, 6]]]
  };

  function textPath(str, cx, cy, h) {
    var s = h / 6, adv = 5.4, w = (str.length * adv - 1.4) * s, x0 = cx - w / 2, y0 = cy - h / 2, d = '';
    Array.prototype.forEach.call(str, function (ch, i) {
      (GLYPH[ch] || []).forEach(function (line) {
        line.forEach(function (p, k) {
          d += (k ? 'L' : 'M') + f(x0 + (i * adv + p[0]) * s) + ' ' + f(y0 + p[1] * s);
        });
      });
    });
    return d;
  }

  /* ---------------------------------------------------------------- baner: returnerer feltcentre i rækkefølge */

  function withAngles(pts) {
    pts.forEach(function (p, i) {
      var a = pts[Math.max(0, i - 1)], b = pts[Math.min(pts.length - 1, i + 1)];
      p.a = Math.atan2(b.y - a.y, b.x - a.x);
    });
    return pts;
  }

  /* på gitterbaner står felterne lige, også i svingene */
  function snap(pts) {
    pts.forEach(function (p) { p.a = Math.round(p.a / (Math.PI / 2)) * (Math.PI / 2); });
    return pts;
  }

  function baneSlange(n, x0, y0, W) {
    var R = Math.max(3, Math.round(Math.sqrt((n + 1) / 2))), C = 2 * R - 1, d = W / C, pts = [];
    for (var r = 0; r < R; r++) {
      for (var k = 0; k < C; k++) {
        var c = r % 2 ? C - 1 - k : k;
        pts.push({ x: x0 + (c + 0.5) * d, y: y0 + (2 * r + 0.5) * d });
      }
      if (r < R - 1) {
        var cEnd = r % 2 ? 0 : C - 1;
        pts.push({ x: x0 + (cEnd + 0.5) * d, y: y0 + (2 * r + 1.5) * d });
      }
    }
    return { pts: snap(withAngles(pts)), fs: d * 0.84 };
  }

  function squareSpiralCells(G) {
    var cells = [[0, G - 1]], x = 0, y = G - 1;
    var dirs = [[0, -1], [1, 0], [0, 1], [-1, 0]];
    var lens = [G - 1, G - 1, G - 1];
    for (var L = G - 3; L > 0; L -= 2) { lens.push(L, L); }
    lens.forEach(function (len, i) {
      var dv = dirs[i % 4];
      for (var s = 0; s < len; s++) { x += dv[0]; y += dv[1]; cells.push([x, y]); }
    });
    return cells;
  }

  function baneFirkant(n, x0, y0, W) {
    var best = 5, bestErr = 1e9;
    [5, 7, 9, 11, 13].forEach(function (G) {
      var e = Math.abs(squareSpiralCells(G).length - n);
      if (e <= bestErr) { bestErr = e; best = G; }
    });
    var d = W / best;
    var pts = squareSpiralCells(best).map(function (c) { return { x: x0 + (c[0] + 0.5) * d, y: y0 + (c[1] + 0.5) * d }; });
    return { pts: snap(withAngles(pts)), fs: d * 0.84 };
  }

  function spiralPoints(fs, R0, cx, cy) {
    var pitch = fs * 1.28, step = fs * 1.12, rMin = fs * 1.55, pts = [];
    var th = 0, r = R0;
    var b = pitch / (2 * Math.PI);
    pts.push({ x: cx, y: cy + r });
    while (r > rMin && pts.length < 400) {
      var dth = step / Math.sqrt(r * r + b * b);
      th += dth;
      r = R0 - b * th;
      if (r <= rMin) { break; }
      pts.push({ x: cx + r * Math.sin(th), y: cy + r * Math.cos(th) });
    }
    return pts;
  }

  function baneSpiral(n, x0, y0, W) {
    var cx = x0 + W / 2, cy = y0 + W / 2, lo = 4, hi = W / 3, fs = 20, pts = [];
    for (var it = 0; it < 30; it++) {
      fs = (lo + hi) / 2;
      pts = spiralPoints(fs, W / 2 - fs / 2, cx, cy);
      if (pts.length + 1 > n) { lo = fs; } else { hi = fs; }
    }
    pts = spiralPoints(fs, W / 2 - fs / 2, cx, cy);
    pts.push({ x: cx, y: cy, goal: fs * 1.35 });
    return { pts: withAngles(pts), fs: fs };
  }

  function baneBugtet(n, x0, y0, W, rand) {
    var R = n < 38 ? 3 : (n < 52 ? 4 : 5), rowH = W / R, rad = rowH / 2, edge = rowH * 0.34;
    var xL = x0 + edge + rad, xR = x0 + W - edge - rad, dense = [];
    for (var r = 0; r < R; r++) {
      var yc = y0 + (r + 0.5) * rowH, A = rowH * 0.13 * (0.6 + rand() * 0.4), k = 1 + Math.floor(rand() * 2), ph = rand() * Math.PI * 2;
      for (var i = 0; i <= 80; i++) {
        var t = i / 80, tt = r % 2 ? 1 - t : t;
        dense.push({ x: xL + (xR - xL) * tt, y: yc + A * Math.sin(Math.PI * t) * Math.sin(2 * Math.PI * k * t + ph) });
      }
      if (r < R - 1) {
        var side = r % 2 ? -1 : 1, ex = side > 0 ? xR : xL, cy = yc + rad;
        for (var j = 1; j < 24; j++) {
          var a = -Math.PI / 2 + Math.PI * j / 24;
          dense.push({ x: ex + side * rad * Math.cos(a), y: cy + rad * Math.sin(a) });
        }
      }
    }
    var cum = [0];
    for (var q = 1; q < dense.length; q++) { cum.push(cum[q - 1] + Math.hypot(dense[q].x - dense[q - 1].x, dense[q].y - dense[q - 1].y)); }
    var L = cum[cum.length - 1], s = L / (n - 1), pts = [], seg = 1;
    for (var m = 0; m < n; m++) {
      var target = m * s;
      while (seg < cum.length - 1 && cum[seg] < target) { seg++; }
      var u = (target - cum[seg - 1]) / ((cum[seg] - cum[seg - 1]) || 1);
      pts.push({ x: dense[seg - 1].x + (dense[seg].x - dense[seg - 1].x) * u, y: dense[seg - 1].y + (dense[seg].y - dense[seg - 1].y) * u });
    }
    return { pts: withAngles(pts), fs: Math.min(s * 0.8, rowH * 0.6) };
  }

  /* ---------------------------------------------------------------- ringbaner: ingen mål, man kører rundt */

  function withAnglesLoop(pts) {
    var n = pts.length;
    pts.forEach(function (p, i) {
      var a = pts[(i - 1 + n) % n], b = pts[(i + 1) % n];
      p.a = Math.atan2(b.y - a.y, b.x - a.x);
    });
    return pts;
  }

  function baneRing(n, x0, y0, W) {
    var k = n < 38 ? 8 : (n < 52 ? 12 : 16), d = W / k, cells = [], i;
    for (i = k - 1; i >= 0; i--) { cells.push([0, i]); }          // op langs venstre side
    for (i = 1; i < k; i++) { cells.push([i, 0]); }               // hen langs toppen
    for (i = 1; i < k; i++) { cells.push([k - 1, i]); }           // ned langs højre side
    for (i = k - 2; i >= 1; i--) { cells.push([i, k - 1]); }      // tilbage langs bunden
    var pts = cells.map(function (c) { return { x: x0 + (c[0] + 0.5) * d, y: y0 + (c[1] + 0.5) * d }; });
    return { pts: snap(withAnglesLoop(pts)), fs: d * 0.84, loop: true };
  }

  function baneRundring(n, x0, y0, W) {
    var q = 0.86 * Math.PI / n, fs = q * W / (1 + q), R = W / 2 - fs / 2, cx = x0 + W / 2, cy = y0 + W / 2, pts = [];
    for (var i = 0; i < n; i++) {
      var t = Math.PI / 2 + 2 * Math.PI * i / n;
      pts.push({ x: cx + R * Math.cos(t), y: cy + R * Math.sin(t) });
    }
    return { pts: withAnglesLoop(pts), fs: fs, loop: true };
  }

  function baneBlomst(n, x0, y0, W, rand) {
    var q = 0.86 * Math.PI / n, fsEst = q * W / (1 + q) * 0.92, A = W * 0.07;
    var petals = 4 + Math.floor(rand() * 3), ph = rand() * Math.PI * 2;
    var R0 = W / 2 - A - fsEst / 2, cx = x0 + W / 2, cy = y0 + W / 2, dense = [];
    for (var i = 0; i <= 1440; i++) {
      var t = Math.PI / 2 + 2 * Math.PI * i / 1440, r = R0 + A * Math.cos(petals * t + ph);
      dense.push({ x: cx + r * Math.cos(t), y: cy + r * Math.sin(t) });
    }
    var cum = [0];
    for (var j = 1; j < dense.length; j++) { cum.push(cum[j - 1] + Math.hypot(dense[j].x - dense[j - 1].x, dense[j].y - dense[j - 1].y)); }
    var L = cum[cum.length - 1], s = L / n, pts = [], seg = 1;
    for (var m = 0; m < n; m++) {
      var target = m * s;
      while (seg < cum.length - 1 && cum[seg] < target) { seg++; }
      var u = (target - cum[seg - 1]) / ((cum[seg] - cum[seg - 1]) || 1);
      pts.push({ x: dense[seg - 1].x + (dense[seg].x - dense[seg - 1].x) * u, y: dense[seg - 1].y + (dense[seg].y - dense[seg - 1].y) * u });
    }
    return { pts: withAnglesLoop(pts), fs: Math.min(s * 0.8, fsEst), loop: true };
  }

  /* ---------------------------------------------------------------- feltformer */

  function fieldPath(kind, p, fs, rand) {
    var h = fs / 2, ca = Math.cos(p.a), sa = Math.sin(p.a);
    function rot(x, y) { return f(p.x + x * ca - y * sa) + ' ' + f(p.y + x * sa + y * ca); }
    if (kind === 'cirkel') {
      var r = h * 0.94;
      return 'M' + f(p.x - r) + ' ' + f(p.y) + 'a' + f(r) + ' ' + f(r) + ' 0 1 0 ' + f(2 * r) + ' 0a' + f(r) + ' ' + f(r) + ' 0 1 0 ' + f(-2 * r) + ' 0Z';
    }
    if (kind === 'sekskant') {
      var d = '';
      for (var i = 0; i < 6; i++) { var a = Math.PI / 6 + i * Math.PI / 3; d += (i ? 'L' : 'M') + rot(h * Math.cos(a), h * Math.sin(a)); }
      return d + 'Z';
    }
    if (kind === 'sten') {
      var n = 9, pts = [];
      for (var k = 0; k < n; k++) {
        var t = 2 * Math.PI * k / n + rand() * 0.25, rr = h * (0.8 + rand() * 0.18);
        pts.push([rr * Math.cos(t), rr * Math.sin(t)]);
      }
      var mid = function (u, v) { return [(u[0] + v[0]) / 2, (u[1] + v[1]) / 2]; };
      var m0 = mid(pts[n - 1], pts[0]), s = 'M' + rot(m0[0], m0[1]);
      for (var j = 0; j < n; j++) {
        var m1 = mid(pts[j], pts[(j + 1) % n]);
        s += 'Q' + rot(pts[j][0], pts[j][1]) + ' ' + rot(m1[0], m1[1]);
      }
      return s + 'Z';
    }
    var w = h * 0.9, c = h * 0.18;
    return 'M' + rot(-w + c, -w) + 'L' + rot(w - c, -w) + 'Q' + rot(w, -w) + ' ' + rot(w, -w + c) + 'L' + rot(w, w - c) + 'Q' + rot(w, w) + ' ' + rot(w - c, w) +
      'L' + rot(-w + c, w) + 'Q' + rot(-w, w) + ' ' + rot(-w, w - c) + 'L' + rot(-w, -w + c) + 'Q' + rot(-w, -w) + ' ' + rot(-w + c, -w) + 'Z';
  }

  function starPath(cx, cy, r, inner) {
    var d = '';
    for (var i = 0; i < 10; i++) {
      var a = -Math.PI / 2 + i * Math.PI / 5, rr = i % 2 ? r * (inner || 0.45) : r;
      d += (i ? 'L' : 'M') + f(cx + rr * Math.cos(a)) + ' ' + f(cy + rr * Math.sin(a));
    }
    return d + 'Z';
  }

  /* ---------------------------------------------------------------- pynt pr. tema (tegnet i enhedsfirkant -1..1) */

  function circ(cx, cy, r) { return 'M' + (cx - r) + ' ' + cy + 'a' + r + ' ' + r + ' 0 1 0 ' + 2 * r + ' 0a' + r + ' ' + r + ' 0 1 0 ' + -2 * r + ' 0'; }
  var ICONS = {
    skov: [
      'M0 -1L0.65 0L0.32 0L0.8 0.68L0.13 0.68L0.13 1L-0.13 1L-0.13 0.68L-0.8 0.68L-0.32 0L-0.65 0Z',
      'M-0.9 0.05Q0 -1.25 0.9 0.05Z M-0.28 0.05L-0.28 0.9L0.28 0.9L0.28 0.05' + circ(-0.3, -0.35, 0.12) + circ(0.3, -0.3, 0.1),
      circ(0, -0.25, 0.55) + circ(-0.45, 0.2, 0.42) + circ(0.45, 0.2, 0.42) + 'M0 0.4L0 1'
    ],
    hav: [
      'M-0.6 0Q0 -0.62 0.75 0Q0 0.62 -0.6 0Z M-0.6 0L-1 -0.42L-1 0.42Z' + circ(0.38, -0.08, 0.07),
      'M-1 -0.15Q-0.75 -0.55 -0.5 -0.15T0 -0.15T0.5 -0.15T1 -0.15 M-1 0.35Q-0.75 -0.05 -0.5 0.35T0 0.35T0.5 0.35T1 0.35',
      'M-0.95 0.3L0.95 0.3L0.6 0.75L-0.6 0.75Z M0 0.3L0 -0.95 M0.07 -0.85L0.75 0.2L0.07 0.2Z'
    ],
    rum: [
      'M0 -1L0.24 -0.33L0.95 -0.31L0.39 0.13L0.59 0.81L0 0.41L-0.59 0.81L-0.39 0.13L-0.95 -0.31L-0.24 -0.33Z',
      circ(0, 0, 0.52) + 'M-1 0.18Q0 -0.55 1 -0.18 M-1 0.18Q0 0.52 1 -0.18',
      'M0 -1Q0.48 -0.42 0.36 0.5L-0.36 0.5Q-0.48 -0.42 0 -1Z M-0.36 0.15L-0.65 0.65L-0.36 0.5 M0.36 0.15L0.65 0.65L0.36 0.5 M-0.15 0.5L0 0.95L0.15 0.5' + circ(0, -0.25, 0.15)
    ],
    slot: [
      'M-0.5 1L-0.5 -0.62L-0.5 -0.95L-0.28 -0.95L-0.28 -0.75L-0.08 -0.75L-0.08 -0.95L0.12 -0.95L0.12 -0.75L0.32 -0.75L0.32 -0.95L0.5 -0.95L0.5 1Z M-0.18 1L-0.18 0.55Q0 0.3 0.18 0.55L0.18 1 M-0.1 -0.3L-0.1 -0.05L0.1 -0.05L0.1 -0.3Z',
      'M-0.4 1L-0.4 -1 M-0.4 -0.95L0.75 -0.62L-0.4 -0.3',
      'M-0.9 0.55L-0.9 -0.5L-0.45 0L0 -0.75L0.45 0L0.9 -0.5L0.9 0.55Z M-0.9 0.8L0.9 0.8'
    ]
  };

  /* ---------------------------------------------------------------- byg pladen */

  function build(opt, forPreview) {
    var rand = rng(opt.seed * 7919 + 13);
    var S = Math.min(+opt.stoerrelse, LASER_MAX), pad = S < 250 ? 11 : 14, W = S - 2 * pad, n = TARGET[opt.laengde];
    function layoutFor(count) {
      return opt.bane === 'slange' ? baneSlange(count, pad, pad, W)
        : opt.bane === 'firkant' ? baneFirkant(count, pad, pad, W)
          : opt.bane === 'spiral' ? baneSpiral(count, pad, pad, W)
            : opt.bane === 'ring' ? baneRing(count, pad, pad, W)
              : opt.bane === 'rundring' ? baneRundring(count, pad, pad, W)
                : opt.bane === 'blomst' ? baneBlomst(count, pad, pad, W, rng(opt.seed * 31 + 7))
                  : baneBugtet(count, pad, pad, W, rand);
    }
    var lay = layoutFor(n);
    /* ringbaner har kun kanten at gøre godt med: hellere færre felter end felter under 13 mm */
    while (lay.loop && lay.fs < 13 && n > 16) { n -= 2; lay = layoutFor(n); }
    var pts = lay.pts, fs = lay.fs, N = pts.length, loop = !!lay.loop;

    /* bundtegninger for hvert lag */
    var cut = [], line = [], fill = [];

    var R0 = Math.min(10, S * 0.03);
    cut.push('M' + R0 + ' 0H' + (S - R0) + 'Q' + S + ' 0 ' + S + ' ' + R0 + 'V' + (S - R0) + 'Q' + S + ' ' + S + ' ' + (S - R0) + ' ' + S +
      'H' + R0 + 'Q0 ' + S + ' 0 ' + (S - R0) + 'V' + R0 + 'Q0 0 ' + R0 + ' 0Z');
    var fr = pad * 0.45, R1 = Math.max(2, R0 - fr);
    line.push('M' + f(fr + R1) + ' ' + f(fr) + 'H' + f(S - fr - R1) + 'Q' + f(S - fr) + ' ' + f(fr) + ' ' + f(S - fr) + ' ' + f(fr + R1) + 'V' + f(S - fr - R1) +
      'Q' + f(S - fr) + ' ' + f(S - fr) + ' ' + f(S - fr - R1) + ' ' + f(S - fr) + 'H' + f(fr + R1) + 'Q' + f(fr) + ' ' + f(S - fr) + ' ' + f(fr) + ' ' + f(S - fr - R1) +
      'V' + f(fr + R1) + 'Q' + f(fr) + ' ' + f(fr) + ' ' + f(fr + R1) + ' ' + f(fr) + 'Z');

    /* specialfelter */
    var special = {}, links = [];
    if (opt.special === 'stjerner' || opt.special === 'spoergsmaal') {
      var every = opt.special === 'stjerner' ? 6 : 5;
      for (var s = 3 + Math.floor(rand() * 3); s < N - 2; s += every - 1 + Math.floor(rand() * 3)) { special[s] = opt.special; }
    }

    /* felter, tal og symboler */
    pts.forEach(function (p, idx) {
      var size = p.goal || fs;
      if (p.goal) {
        var gr = size / 2;
        line.push('M' + f(p.x - gr) + ' ' + f(p.y) + 'a' + f(gr) + ' ' + f(gr) + ' 0 1 0 ' + f(2 * gr) + ' 0a' + f(gr) + ' ' + f(gr) + ' 0 1 0 ' + f(-2 * gr) + ' 0Z');
      } else {
        line.push(fieldPath(opt.felt, p, size, rand));
      }
      var inner = size * (opt.felt === 'sekskant' || opt.felt === 'cirkel' ? 0.66 : 0.72);
      if (idx === 0) {
        var th = Math.min(inner / 4.6, size * 0.2);
        if (loop) {
          /* på en ringbane viser en pil, hvilken vej man kører */
          var nx = pts[1].x - p.x, ny = pts[1].y - p.y, nl = Math.hypot(nx, ny), ux = nx / nl, uy = ny / nl, al = size * 0.2;
          var ax = p.x, ay = p.y + size * 0.2, hx = ax + ux * al, hy = ay + uy * al;
          line.push(textPath('START', p.x, p.y - size * 0.1, th));
          line.push('M' + f(ax - ux * al) + ' ' + f(ay - uy * al) + 'L' + f(hx) + ' ' + f(hy) +
            'M' + f(hx - ux * al * 0.5 - uy * al * 0.4) + ' ' + f(hy - uy * al * 0.5 + ux * al * 0.4) + 'L' + f(hx) + ' ' + f(hy) +
            'L' + f(hx - ux * al * 0.5 + uy * al * 0.4) + ' ' + f(hy - uy * al * 0.5 - ux * al * 0.4));
        } else {
          line.push(textPath('START', p.x, p.y, th));
        }
        return;
      }
      if (idx === N - 1 && !loop) {
        line.push(textPath('MÅL', p.x, p.y, Math.min(inner / 3.2, size * 0.24)));
        return;
      }
      var sp = special[idx];
      var hasNum = opt.tal === 'ja';
      var numH = size * (sp ? 0.16 : 0.26);
      if (sp === 'stjerner') { fill.push(starPath(p.x, p.y + (hasNum ? size * 0.06 : 0), size * 0.27)); }
      if (sp === 'spoergsmaal') { line.push(textPath('?', p.x, p.y + (hasNum ? size * 0.07 : 0), size * 0.36)); }
      if (hasNum) {
        var label = String(idx);
        if (sp) {
          line.push(textPath(label, p.x, p.y - size * 0.27, numH));
        } else {
          line.push(textPath(label, p.x, p.y, Math.min(numH, inner / (label.length * 0.9 + 0.2))));
        }
      }
    });

    /* genveje: buede pile mellem felter, som ikke går hen over andre felter */
    var curveSamples = [];
    function arrowCurve(A, B, bulge) {
      var mx = (A.x + B.x) / 2, my = (A.y + B.y) / 2, dx = B.x - A.x, dy = B.y - A.y;
      var cx = mx - dy * bulge, cy = my + dx * bulge;
      var bez = function (t) {
        var u = 1 - t;
        return { x: u * u * A.x + 2 * u * t * cx + t * t * B.x, y: u * u * A.y + 2 * u * t * cy + t * t * B.y };
      };
      var t0 = 0, t1 = 1, rcut = fs * 0.5;
      while (t0 < 0.5 && Math.hypot(bez(t0).x - A.x, bez(t0).y - A.y) < rcut) { t0 += 0.005; }
      while (t1 > 0.5 && Math.hypot(bez(t1).x - B.x, bez(t1).y - B.y) < rcut + fs * 0.14) { t1 -= 0.005; }
      var samples = [];
      for (var k = 0; k <= 40; k++) { samples.push(bez(t0 + (t1 - t0) * k / 40)); }
      return { bez: bez, t1: t1, samples: samples };
    }
    function clear(curve, a, b) {
      return curve.samples.every(function (q) {
        if (q.x < pad || q.y < pad || q.x > S - pad || q.y > S - pad) { return false; }
        for (var i = 0; i < N; i++) {
          if (i === a || i === b) { continue; }
          var rr = pts[i].goal ? pts[i].goal / 2 : fs * 0.55;
          if (Math.hypot(pts[i].x - q.x, pts[i].y - q.y) < rr + 1) { return false; }
        }
        for (var j = 0; j < curveSamples.length; j++) {
          if (Math.hypot(curveSamples[j].x - q.x, curveSamples[j].y - q.y) < 2) { return false; }
        }
        return true;
      });
    }
    if (opt.special === 'genveje') {
      var used = {};
      for (var tries = 0; tries < 600 && links.length < 6; tries++) {
        var up = links.length % 2 === 0, a0 = 2 + Math.floor(rand() * (N - 4)), jump = 4 + Math.floor(rand() * Math.min(18, N / 2.5));
        var b0 = up ? a0 + jump : a0 - jump;
        if (b0 < 1 || b0 > N - 2 || used[a0] || used[b0] || used[a0 - 1] || used[a0 + 1] || used[b0 - 1] || used[b0 + 1]) { continue; }
        var found = null;
        [0.3, -0.3, 0.5, -0.5, 0.15, -0.15].some(function (bulge) {
          var cv = arrowCurve(pts[a0], pts[b0], bulge);
          if (clear(cv, a0, b0)) { found = cv; return true; }
          return false;
        });
        if (!found) { continue; }
        used[a0] = used[b0] = true;
        links.push([a0, b0]);
        curveSamples = curveSamples.concat(found.samples);
        line.push(found.samples.map(function (q, k) { return (k ? 'L' : 'M') + f(q.x) + ' ' + f(q.y); }).join(''));
        var e = found.bez(found.t1), e2 = found.bez(found.t1 - 0.02), ang = Math.atan2(e.y - e2.y, e.x - e2.x), hl = fs * 0.22, hw = fs * 0.13;
        var tip = { x: e.x + Math.cos(ang) * hl * 0.7, y: e.y + Math.sin(ang) * hl * 0.7 };
        fill.push('M' + f(tip.x) + ' ' + f(tip.y) +
          'L' + f(e.x - Math.cos(ang) * hl * 0.3 - Math.sin(ang) * hw) + ' ' + f(e.y - Math.sin(ang) * hl * 0.3 + Math.cos(ang) * hw) +
          'L' + f(e.x - Math.cos(ang) * hl * 0.3 + Math.sin(ang) * hw) + ' ' + f(e.y - Math.sin(ang) * hl * 0.3 - Math.cos(ang) * hw) + 'Z');
      }
    }

    /* temapynt i de tomme områder */
    var icons = [];
    if (opt.tema !== 'ingen') {
      var set = ICONS[opt.tema], placed = [];
      var lo = pad + 2, hi = S - pad - 2;
      for (var tr = 0; tr < 900 && placed.length < 14; tr++) {
        var r = fs * (0.42 + rand() * 0.38);
        var x = lo + r + rand() * (hi - lo - 2 * r), y = lo + r + rand() * (hi - lo - 2 * r), ok = true;
        for (var pi = 0; pi < N && ok; pi++) {
          var g = pts[pi].goal ? pts[pi].goal / 2 : fs * 0.62;
          if (Math.hypot(pts[pi].x - x, pts[pi].y - y) < r + g + 1.5) { ok = false; }
        }
        for (var ci = 0; ci < curveSamples.length && ok; ci++) {
          if (Math.hypot(curveSamples[ci].x - x, curveSamples[ci].y - y) < r + 3) { ok = false; }
        }
        for (var qi = 0; qi < placed.length && ok; qi++) {
          if (Math.hypot(placed[qi].x - x, placed[qi].y - y) < r + placed[qi].r + 3) { ok = false; }
        }
        if (!ok) { continue; }
        placed.push({ x: x, y: y, r: r });
        var rotDeg = (rand() - 0.5) * 30;
        icons.push('<path transform="translate(' + f(x) + ' ' + f(y) + ') rotate(' + f(rotDeg) + ') scale(' + f(r) + ')" stroke-width="' + f(0.2 / r) + '" d="' + set[placed.length % set.length] + '"/>');
      }
    }

    var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="' + S + 'mm" height="' + S + 'mm" viewBox="0 0 ' + S + ' ' + S + '">' +
      '<title>Spilleplade ' + S / 10 + ' x ' + S / 10 + ' cm — Skoleværkstedet</title>' +
      (forPreview ? '<path class="wood" d="' + cut[0] + '"/>' : '') +
      '<g id="graver" data-layer="graver" fill="#000000" stroke="none">' + fill.map(function (d) { return '<path d="' + d + '"/>'; }).join('') + '</g>' +
      '<g id="streg" data-layer="streg" fill="none" stroke="#0000FF" stroke-width="0.2" stroke-linecap="round" stroke-linejoin="round">' +
      line.filter(Boolean).map(function (d) { return '<path d="' + d + '"/>'; }).join('') + icons.join('') + '</g>' +
      '<g id="skaer" data-layer="skaer" fill="none" stroke="#FF0000" stroke-width="0.1">' + cut.map(function (d) { return '<path d="' + d + '"/>'; }).join('') + '</g>' +
      '</svg>';
    return { svg: svg, n: N, size: S, fs: fs, pts: pts };
  }

  window.LaserBoardGenerator = { options: OPTIONS, defaults: state, build: build };
}());
