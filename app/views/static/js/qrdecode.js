/* ===== QR code reader (update 37) - runs in the page, no external library or service =====
   AOIQRDecode.decode({data, width, height}) reads one QR code from an RGBA image (a camera frame drawn on a canvas)
   and returns its text, or null. QR Code Model 2, versions 1-40, error correction L/M/Q/H, numeric / alphanumeric /
   byte (UTF-8) data. Steps: adaptive black and white, the three finder squares, the alignment square (version 2+),
   perspective grid, format and version bits, Reed-Solomon error correction, data segments.
   Written for this project (the sandbox could not download a library); tested with OpenCV-made pictures. */
(function (root) {
  "use strict";

  // ---------- black and white (local average of 8x8 blocks, like the ZXing "hybrid" method) ----------
  function binarize(img) {
    var w = img.width, h = img.height, d = img.data, n = w * h, lum = new Uint8Array(n), i;
    for (i = 0; i < n; i++) lum[i] = (d[i * 4] * 77 + d[i * 4 + 1] * 150 + d[i * 4 + 2] * 29) >> 8;
    var B = 8, bw = Math.ceil(w / B), bh = Math.ceil(h / B), black = new Float32Array(bw * bh), bx, by, x, y;
    for (by = 0; by < bh; by++) {
      for (bx = 0; bx < bw; bx++) {
        var sum = 0, cnt = 0, mn = 255, mx = 0;
        for (y = by * B; y < Math.min(h, by * B + B); y++) {
          for (x = bx * B; x < Math.min(w, bx * B + B); x++) {
            var v = lum[y * w + x]; sum += v; cnt++; if (v < mn) mn = v; if (v > mx) mx = v;
          }
        }
        var avg = sum / cnt;
        if (mx - mn <= 24) {                               // flat block: lighter than the black around it
          avg = mn / 2;
          if (by > 0 && bx > 0) {
            var nb = (black[(by - 1) * bw + bx] + 2 * black[by * bw + bx - 1] + black[(by - 1) * bw + bx - 1]) / 4;
            if (mn < nb) avg = nb;
          }
        }
        black[by * bw + bx] = avg;
      }
    }
    var bits = new Uint8Array(n);                          // 1 = dark
    for (by = 0; by < bh; by++) {
      for (bx = 0; bx < bw; bx++) {
        var s = 0, c = 0;
        for (var dy = -2; dy <= 2; dy++) {
          for (var dx = -2; dx <= 2; dx++) {
            var yy = Math.min(bh - 1, Math.max(0, by + dy)), xx = Math.min(bw - 1, Math.max(0, bx + dx));
            s += black[yy * bw + xx]; c++;
          }
        }
        var t = s / c;
        for (y = by * B; y < Math.min(h, by * B + B); y++) {
          for (x = bx * B; x < Math.min(w, bx * B + B); x++) bits[y * w + x] = lum[y * w + x] <= t ? 1 : 0;
        }
      }
    }
    return { w: w, h: h, bits: bits };
  }
  function dark(bm, x, y) {                               // pixel i covers [i, i + 1)
    x = Math.floor(x); y = Math.floor(y);
    return x >= 0 && y >= 0 && x < bm.w && y < bm.h && bm.bits[y * bm.w + x] === 1;
  }

  // ---------- finder squares (dark-light-dark-light-dark runs of 1:1:3:1:1) ----------
  function ratioOk(c) {
    var total = c[0] + c[1] + c[2] + c[3] + c[4];
    if (total < 7) return false;
    var m = total / 7, v = m / 1.5;
    return Math.abs(m - c[0]) < v && Math.abs(m - c[1]) < v && Math.abs(3 * m - c[2]) < 3 * v &&
           Math.abs(m - c[3]) < v && Math.abs(m - c[4]) < v;
  }
  // runs along a line through (cx, cy) in direction (dx, dy): returns the centre offset and the total, or null
  function crossCheck(bm, cx, cy, dx, dy, maxCount, expectedTotal) {
    var c = [0, 0, 0, 0, 0], i = 0;
    while (dark(bm, cx - dx * i, cy - dy * i)) { c[2]++; i++; if (i > 4 * maxCount) return null; }
    while (inside(bm, cx - dx * i, cy - dy * i) && !dark(bm, cx - dx * i, cy - dy * i) && c[1] <= maxCount) { c[1]++; i++; }
    if (!inside(bm, cx - dx * i, cy - dy * i) || c[1] > maxCount) return null;
    while (inside(bm, cx - dx * i, cy - dy * i) && dark(bm, cx - dx * i, cy - dy * i) && c[0] <= maxCount) { c[0]++; i++; }
    if (c[0] > maxCount) return null;
    i = 1;
    while (dark(bm, cx + dx * i, cy + dy * i)) { c[2]++; i++; if (i > 4 * maxCount) return null; }
    while (inside(bm, cx + dx * i, cy + dy * i) && !dark(bm, cx + dx * i, cy + dy * i) && c[3] < maxCount) { c[3]++; i++; }
    if (!inside(bm, cx + dx * i, cy + dy * i) || c[3] >= maxCount) return null;
    while (inside(bm, cx + dx * i, cy + dy * i) && dark(bm, cx + dx * i, cy + dy * i) && c[4] < maxCount) { c[4]++; i++; }
    if (c[4] >= maxCount) return null;
    var total = c[0] + c[1] + c[2] + c[3] + c[4];
    if (expectedTotal && 5 * Math.abs(total - expectedTotal) >= 2 * expectedTotal) return null;
    if (!ratioOk(c)) return null;
    return { offset: (i - c[4] - c[3]) - c[2] / 2, total: total };   // centre, from the start pixel along the line
  }
  function inside(bm, x, y) { x = Math.floor(x); y = Math.floor(y); return x >= 0 && y >= 0 && x < bm.w && y < bm.h; }

  function findFinders(bm) {
    var found = [], w = bm.w, h = bm.h, skip = Math.max(2, Math.floor(h / 180));
    function add(x, y, size) {
      for (var k = 0; k < found.length; k++) {
        var f = found[k];
        if (Math.abs(f.x - x) <= size && Math.abs(f.y - y) <= size && Math.abs(size - f.size) <= Math.max(1, f.size)) {
          var n = f.count;
          f.x = (f.x * n + x) / (n + 1); f.y = (f.y * n + y) / (n + 1); f.size = (f.size * n + size) / (n + 1); f.count++;
          return;
        }
      }
      found.push({ x: x, y: y, size: size, count: 1 });
    }
    function tryAt(c, endX, y) {
      var total = c[0] + c[1] + c[2] + c[3] + c[4];
      var cx = endX - c[4] - c[3] - c[2] / 2;
      var vert = crossCheck(bm, cx, y, 0, 1, c[2], total);
      if (!vert) return;
      var cy = y + vert.offset;
      var hor = crossCheck(bm, cx, cy, 1, 0, c[2], total);
      if (!hor) return;
      cx = Math.floor(cx) + hor.offset;
      var vert2 = crossCheck(bm, cx, cy, 0, 1, c[2], total);         // once more through the better centre
      if (vert2) cy = Math.floor(cy) + vert2.offset;
      add(cx, cy, (vert.total + hor.total) / 14);
    }
    for (var y = skip - 1; y < h; y += skip) {
      var c = [0, 0, 0, 0, 0], state = 0;
      for (var x = 0; x < w; x++) {
        var isDark = bm.bits[y * w + x] === 1;
        if (isDark) {
          if (state & 1) state++;
          c[state]++;
        } else if (!(state & 1)) {
          if (state === 4) {
            if (ratioOk(c)) tryAt(c, x, y);
            c = [c[2], c[3], c[4], 1, 0]; state = 3;
          } else { state++; c[state]++; }
        } else c[state]++;
      }
      if (state === 4 && ratioOk(c)) tryAt(c, w, y);
    }
    return found;
  }

  function dist(a, b) { return Math.sqrt((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y)); }
  // the best threes: similar sizes and a right angle at the top-left square, ordered top-left / top-right / bottom-left
  function triples(found) {
    var list = found.filter(function (f) { return f.count >= 2; });
    if (list.length < 3) list = found.slice();
    list.sort(function (a, b) { return b.count - a.count; });
    list = list.slice(0, 12);
    var out = [];
    for (var i = 0; i < list.length; i++) {
      for (var j = i + 1; j < list.length; j++) {
        for (var k = j + 1; k < list.length; k++) {
          var p = [list[i], list[j], list[k]];
          var sizes = p.map(function (f) { return f.size; });
          var smax = Math.max.apply(null, sizes), smin = Math.min.apply(null, sizes);
          if (smax > smin * 1.6) continue;
          var d01 = dist(p[0], p[1]), d02 = dist(p[0], p[2]), d12 = dist(p[1], p[2]), tl, a, b;
          if (d12 >= d01 && d12 >= d02) { tl = p[0]; a = p[1]; b = p[2]; }
          else if (d02 >= d01) { tl = p[1]; a = p[0]; b = p[2]; }
          else { tl = p[2]; a = p[0]; b = p[1]; }
          var la = dist(tl, a), lb = dist(tl, b), hyp = dist(a, b);
          var cross = (a.x - tl.x) * (b.y - tl.y) - (a.y - tl.y) * (b.x - tl.x);
          var tr = cross > 0 ? a : b, bl = cross > 0 ? b : a;
          var ms = (sizes[0] + sizes[1] + sizes[2]) / 3;
          if (Math.min(la, lb) < 10 * ms * 0.8) continue;                // at least version 1 (14 modules apart)
          var score = Math.abs(la - lb) / Math.max(la, lb) + Math.abs(hyp - Math.sqrt(la * la + lb * lb)) / hyp + (smax - smin) / smax;
          if (score > 0.5) continue;
          out.push({ tl: tl, tr: tr, bl: bl, ms: ms, score: score });
        }
      }
    }
    out.sort(function (x, y) { return x.score - y.score; });
    return out.slice(0, 4);
  }

  // ---------- perspective (unit square <-> four points) ----------
  function squareToQuad(x0, y0, x1, y1, x2, y2, x3, y3) {
    var dx3 = x0 - x1 + x2 - x3, dy3 = y0 - y1 + y2 - y3;
    if (dx3 === 0 && dy3 === 0) {
      return [x1 - x0, x2 - x1, x0, y1 - y0, y2 - y1, y0, 0, 0, 1];
    }
    var dx1 = x1 - x2, dx2 = x3 - x2, dy1 = y1 - y2, dy2 = y3 - y2, den = dx1 * dy2 - dx2 * dy1;
    var a13 = (dx3 * dy2 - dx2 * dy3) / den, a23 = (dx1 * dy3 - dx3 * dy1) / den;
    return [x1 - x0 + a13 * x1, x3 - x0 + a23 * x3, x0, y1 - y0 + a13 * y1, y3 - y0 + a23 * y3, y0, a13, a23, 1];
  }
  // matrix as [a11 a21 a31 a12 a22 a32 a13 a23 a33] in ZXing order: x' = (a11 u + a21 v + a31) / (a13 u + a23 v + a33)
  function adjoint(m) {
    var a11 = m[0], a21 = m[1], a31 = m[2], a12 = m[3], a22 = m[4], a32 = m[5], a13 = m[6], a23 = m[7], a33 = m[8];
    return [a22 * a33 - a23 * a32, a23 * a31 - a21 * a33, a21 * a32 - a22 * a31,
            a13 * a32 - a12 * a33, a11 * a33 - a13 * a31, a12 * a31 - a11 * a32,
            a12 * a23 - a13 * a22, a13 * a21 - a11 * a23, a11 * a22 - a12 * a21];
  }
  function times(p, q) {          // p then q
    return [
      p[0] * q[0] + p[3] * q[1] + p[6] * q[2], p[1] * q[0] + p[4] * q[1] + p[7] * q[2], p[2] * q[0] + p[5] * q[1] + p[8] * q[2],
      p[0] * q[3] + p[3] * q[4] + p[6] * q[5], p[1] * q[3] + p[4] * q[4] + p[7] * q[5], p[2] * q[3] + p[5] * q[4] + p[8] * q[5],
      p[0] * q[6] + p[3] * q[7] + p[6] * q[8], p[1] * q[6] + p[4] * q[7] + p[7] * q[8], p[2] * q[6] + p[5] * q[7] + p[8] * q[8]];
  }
  function quadToQuad(src, dst) {     // src, dst: 8 numbers (four corners in the same order)
    var qs = adjoint(squareToQuad.apply(null, src)), sq = squareToQuad.apply(null, dst);
    return times(qs, sq);
  }
  function apply(m, u, v) {
    var den = m[6] * u + m[7] * v + m[8];
    return { x: (m[0] * u + m[1] * v + m[2]) / den, y: (m[3] * u + m[4] * v + m[5]) / den };
  }

  // ---------- QR tables ----------
  // per version: EC codewords per block, then for L, M, Q, H [blocks, data codewords, blocks, data codewords]
  var EC = [null,
    [[7, 1, 19], [10, 1, 16], [13, 1, 13], [17, 1, 9]], [[10, 1, 34], [16, 1, 28], [22, 1, 22], [28, 1, 16]],
    [[15, 1, 55], [26, 1, 44], [18, 2, 17], [22, 2, 13]], [[20, 1, 80], [18, 2, 32], [26, 2, 24], [16, 4, 9]],
    [[26, 1, 108], [24, 2, 43], [18, 2, 15, 2, 16], [22, 2, 11, 2, 12]], [[18, 2, 68], [16, 4, 27], [24, 4, 19], [28, 4, 15]],
    [[20, 2, 78], [18, 4, 31], [18, 2, 14, 4, 15], [26, 4, 13, 1, 14]], [[24, 2, 97], [22, 2, 38, 2, 39], [22, 4, 18, 2, 19], [26, 4, 14, 2, 15]],
    [[30, 2, 116], [22, 3, 36, 2, 37], [20, 4, 16, 4, 17], [24, 4, 12, 4, 13]], [[18, 2, 68, 2, 69], [26, 4, 43, 1, 44], [24, 6, 19, 2, 20], [28, 6, 15, 2, 16]],
    [[20, 4, 81], [30, 1, 50, 4, 51], [28, 4, 22, 4, 23], [24, 3, 12, 8, 13]], [[24, 2, 92, 2, 93], [22, 6, 36, 2, 37], [26, 4, 20, 6, 21], [28, 7, 14, 4, 15]],
    [[26, 4, 107], [22, 8, 37, 1, 38], [24, 8, 20, 4, 21], [22, 12, 11, 4, 12]], [[30, 3, 115, 1, 116], [24, 4, 40, 5, 41], [20, 11, 16, 5, 17], [24, 11, 12, 5, 13]],
    [[22, 5, 87, 1, 88], [24, 5, 41, 5, 42], [30, 5, 24, 7, 25], [24, 11, 12, 7, 13]], [[24, 5, 98, 1, 99], [28, 7, 45, 3, 46], [24, 15, 19, 2, 20], [30, 3, 15, 13, 16]],
    [[28, 1, 107, 5, 108], [28, 10, 46, 1, 47], [28, 1, 22, 15, 23], [28, 2, 14, 17, 15]], [[30, 5, 120, 1, 121], [26, 9, 43, 4, 44], [28, 17, 22, 1, 23], [28, 2, 14, 19, 15]],
    [[28, 3, 113, 4, 114], [26, 3, 44, 11, 45], [26, 17, 21, 4, 22], [26, 9, 13, 16, 14]], [[28, 3, 107, 5, 108], [26, 3, 41, 13, 42], [30, 15, 24, 5, 25], [28, 15, 15, 10, 16]],
    [[28, 4, 116, 4, 117], [26, 17, 42], [28, 17, 22, 6, 23], [30, 19, 16, 6, 17]], [[28, 2, 111, 7, 112], [28, 17, 46], [30, 7, 24, 16, 25], [24, 34, 13]],
    [[30, 4, 121, 5, 122], [28, 4, 47, 14, 48], [30, 11, 24, 14, 25], [30, 16, 15, 14, 16]], [[30, 6, 117, 4, 118], [28, 6, 45, 14, 46], [30, 11, 24, 16, 25], [30, 30, 16, 2, 17]],
    [[26, 8, 106, 4, 107], [28, 8, 47, 13, 48], [30, 7, 24, 22, 25], [30, 22, 15, 13, 16]], [[28, 10, 114, 2, 115], [28, 19, 46, 4, 47], [28, 28, 22, 6, 23], [30, 33, 16, 4, 17]],
    [[30, 8, 122, 4, 123], [28, 22, 45, 3, 46], [30, 8, 23, 26, 24], [30, 12, 15, 28, 16]], [[30, 3, 117, 10, 118], [28, 3, 45, 23, 46], [30, 4, 24, 31, 25], [30, 11, 15, 31, 16]],
    [[30, 7, 116, 7, 117], [28, 21, 45, 7, 46], [30, 1, 23, 37, 24], [30, 19, 15, 26, 16]], [[30, 5, 115, 10, 116], [28, 19, 47, 10, 48], [30, 15, 24, 25, 25], [30, 23, 15, 25, 16]],
    [[30, 13, 115, 3, 116], [28, 2, 46, 29, 47], [30, 42, 24, 1, 25], [30, 23, 15, 28, 16]], [[30, 17, 115], [28, 10, 46, 23, 47], [30, 10, 24, 35, 25], [30, 19, 15, 35, 16]],
    [[30, 17, 115, 1, 116], [28, 14, 46, 21, 47], [30, 29, 24, 19, 25], [30, 11, 15, 46, 16]], [[30, 13, 115, 6, 116], [28, 14, 46, 23, 47], [30, 44, 24, 7, 25], [30, 59, 16, 1, 17]],
    [[30, 12, 121, 7, 122], [28, 12, 47, 26, 48], [30, 39, 24, 14, 25], [30, 22, 15, 41, 16]], [[30, 6, 121, 14, 122], [28, 6, 47, 34, 48], [30, 46, 24, 10, 25], [30, 2, 15, 64, 16]],
    [[30, 17, 122, 4, 123], [28, 29, 46, 14, 47], [30, 49, 24, 10, 25], [30, 24, 15, 46, 16]], [[30, 4, 122, 18, 123], [28, 13, 46, 32, 47], [30, 48, 24, 14, 25], [30, 42, 15, 32, 16]],
    [[30, 20, 117, 4, 118], [28, 40, 47, 7, 48], [30, 43, 24, 22, 25], [30, 10, 15, 67, 16]], [[30, 19, 118, 6, 119], [28, 18, 47, 31, 48], [30, 34, 24, 34, 25], [30, 20, 15, 61, 16]]];
  var LEVEL_OF_BITS = [1, 0, 3, 2];          // format bits 00 = M, 01 = L, 10 = H, 11 = Q -> index in L M Q H

  function alignPositions(ver) {
    if (ver === 1) return [];
    var num = Math.floor(ver / 7) + 2, size = ver * 4 + 17;
    var step = ver === 32 ? 26 : Math.ceil((ver * 4 + 4) / (num * 2 - 2)) * 2, out = [6];
    for (var pos = size - 7; out.length < num; pos -= step) out.splice(1, 0, pos);
    return out;
  }
  function bch(value, poly, polyBits) {
    var v = value << (polyBits - 1);
    for (var i = 31; i >= polyBits - 1; i--) if ((v >>> i) & 1) v ^= poly << (i - polyBits + 1);
    return v;
  }
  var FORMATS = [], VERSIONS = [];
  (function () {
    for (var f = 0; f < 32; f++) FORMATS.push({ bits: ((f << 10) | bch(f, 0x537, 11)) ^ 0x5412, data: f });
    for (var v = 7; v <= 40; v++) VERSIONS.push({ bits: (v << 12) | bch(v, 0x1f25, 13), ver: v });
  })();
  function popcount(x) { var c = 0; while (x) { c += x & 1; x >>>= 1; } return c; }
  function closest(list, bits, key) {
    var best = null, bestD = 99;
    list.forEach(function (e) { var dd = popcount(e.bits ^ bits); if (dd < bestD) { bestD = dd; best = e[key]; } });
    return bestD <= 3 ? best : null;
  }
  var MASKS = [
    function (x, y) { return (x + y) % 2 === 0; }, function (x, y) { return y % 2 === 0; },
    function (x) { return x % 3 === 0; }, function (x, y) { return (x + y) % 3 === 0; },
    function (x, y) { return (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0; },
    function (x, y) { return x * y % 2 + x * y % 3 === 0; },
    function (x, y) { return (x * y % 2 + x * y % 3) % 2 === 0; },
    function (x, y) { return ((x + y) % 2 + x * y % 3) % 2 === 0; }];

  function functionMap(ver) {
    var size = ver * 4 + 17, fn = [], i, j;
    for (i = 0; i < size; i++) fn.push(new Uint8Array(size));
    function rect(x0, y0, w, h) { for (var y = y0; y < y0 + h; y++) for (var x = x0; x < x0 + w; x++) if (x >= 0 && y >= 0 && x < size && y < size) fn[y][x] = 1; }
    rect(0, 0, 9, 9); rect(size - 8, 0, 8, 9); rect(0, size - 8, 9, 8);
    rect(6, 0, 1, size); rect(0, 6, size, 1);
    var al = alignPositions(ver);
    for (i = 0; i < al.length; i++) {
      for (j = 0; j < al.length; j++) {
        if ((i === 0 && j === 0) || (i === 0 && j === al.length - 1) || (i === al.length - 1 && j === 0)) continue;
        rect(al[i] - 2, al[j] - 2, 5, 5);
      }
    }
    if (ver >= 7) { rect(size - 11, 0, 3, 6); rect(0, size - 11, 6, 3); }
    return fn;
  }

  // ---------- Reed-Solomon (GF(256), polynomial 0x11d) ----------
  var EXP = new Uint8Array(512), LOG = new Uint8Array(256);
  (function () { var x = 1; for (var i = 0; i < 255; i++) { EXP[i] = x; LOG[x] = i; x <<= 1; if (x & 0x100) x ^= 0x11d; } for (i = 255; i < 512; i++) EXP[i] = EXP[i - 255]; })();
  function gmul(a, b) { return a && b ? EXP[LOG[a] + LOG[b]] : 0; }
  function ginv(a) { return EXP[255 - LOG[a]]; }
  // corrects the block in place (data then EC codewords, highest power first); false when it cannot
  function rsCorrect(block, ecLen) {
    var n = block.length, synd = [], i, j, bad = false;
    for (i = 0; i < ecLen; i++) {
      var s = 0;
      for (j = 0; j < n; j++) s = gmul(s, EXP[i]) ^ block[j];
      synd.push(s); if (s) bad = true;
    }
    if (!bad) return true;
    // Berlekamp-Massey: error locator sigma
    var C = [1], Bp = [1], L = 0, m = 1, b = 1;
    for (i = 0; i < ecLen; i++) {
      var delta = synd[i];
      for (j = 1; j <= L; j++) delta ^= gmul(C[j] || 0, synd[i - j]);
      if (delta === 0) { m++; continue; }
      var T = C.slice(), coef = gmul(delta, ginv(b));
      while (C.length < Bp.length + m) C.push(0);
      for (j = 0; j < Bp.length; j++) C[j + m] ^= gmul(coef, Bp[j]);
      if (2 * L <= i) { L = i + 1 - L; Bp = T; b = delta; m = 1; } else m++;
    }
    if (2 * L > ecLen) return false;
    // Chien search: positions where sigma(X^-1) = 0; X = alpha^(n-1-pos)
    var positions = [];
    for (var p = 0; p < n; p++) {
      var xinv = EXP[(255 - (n - 1 - p)) % 255], val = 0;
      for (j = C.length - 1; j >= 0; j--) val = gmul(val, xinv) ^ (C[j] || 0);
      if (val === 0) positions.push(p);
    }
    if (positions.length !== L) return false;
    // Forney: omega = S(x) * sigma(x) mod x^ecLen
    var omega = [];
    for (i = 0; i < ecLen; i++) {
      var o = 0;
      for (j = 0; j <= i; j++) o ^= gmul(synd[i - j], C[j] || 0);
      omega.push(o);
    }
    for (var e = 0; e < positions.length; e++) {
      var X = EXP[(n - 1 - positions[e]) % 255], Xi = ginv(X), num = 0, den = 0, pw = 1;
      for (i = 0; i < ecLen; i++) { num ^= gmul(omega[i], pw); pw = gmul(pw, Xi); }
      for (i = 1; i < C.length; i += 2) {             // formal derivative: odd terms
        var term = C[i] || 0, pw2 = 1;
        for (j = 0; j < i - 1; j++) pw2 = gmul(pw2, Xi);
        den ^= gmul(term, pw2);
      }
      if (den === 0) return false;
      block[positions[e]] ^= gmul(X, gmul(num, ginv(den)));      // first consecutive root alpha^0
    }
    for (i = 0; i < ecLen; i++) {                     // check
      var s2 = 0;
      for (j = 0; j < n; j++) s2 = gmul(s2, EXP[i]) ^ block[j];
      if (s2) return false;
    }
    return true;
  }

  // ---------- data segments ----------
  var ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:";
  function readData(bytes, ver) {
    var pos = 0, total = bytes.length * 8, out = "", raw = [];
    function bits(n) {
      var v = 0;
      for (var i = 0; i < n; i++) { if (pos >= total) throw new Error("end"); v = (v << 1) | ((bytes[pos >> 3] >> (7 - (pos & 7))) & 1); pos++; }
      return v;
    }
    function flushBytes() { if (raw.length) { out += utf8(raw); raw = []; } }
    var grp = ver <= 9 ? 0 : ver <= 26 ? 1 : 2;
    try {
      while (pos + 4 <= total) {
        var mode = bits(4), count, i;
        if (mode === 0) break;
        if (mode === 1) {
          flushBytes(); count = bits([10, 12, 14][grp]);
          for (i = 0; i + 3 <= count; i += 3) out += ("00" + bits(10)).slice(-3);
          if (count % 3 === 2) out += ("0" + bits(7)).slice(-2); else if (count % 3 === 1) out += String(bits(4));
        } else if (mode === 2) {
          flushBytes(); count = bits([9, 11, 13][grp]);
          for (i = 0; i + 2 <= count; i += 2) { var v = bits(11); out += ALNUM[Math.floor(v / 45)] + ALNUM[v % 45]; }
          if (count % 2) out += ALNUM[bits(6)];
        } else if (mode === 4) {
          count = bits([8, 16, 16][grp]);
          for (i = 0; i < count; i++) raw.push(bits(8));
        } else if (mode === 7) {                      // ECI: the text is read as UTF-8 anyway
          var first = bits(8);
          if ((first & 0xc0) === 0x80) bits(8); else if ((first & 0xe0) === 0xc0) bits(16);
        } else if (mode === 3) { bits(16); }          // structured append header
        else return null;                              // kanji and others: not used here
      }
    } catch (err) { /* ran out of bits: keep what was read */ }
    flushBytes();
    return out;
  }
  function utf8(arr) {
    var s = "";
    for (var i = 0; i < arr.length; i++) s += String.fromCharCode(arr[i]);
    try { return decodeURIComponent(escape(s)); } catch (e) { return s; }
  }

  // ---------- one grid ----------
  function sampleGrid(bm, m, size) {
    var g = [];
    for (var y = 0; y < size; y++) {
      var row = new Uint8Array(size);
      for (var x = 0; x < size; x++) { var p = apply(m, x + 0.5, y + 0.5); row[x] = dark(bm, p.x, p.y) ? 1 : 0; }
      g.push(row);
    }
    return g;
  }
  function decodeGrid(g, size, mirrored) {
    function at(x, y) { return mirrored ? g[x][y] : g[y][x]; }
    var f1 = 0, f2 = 0, i;
    for (i = 0; i <= 5; i++) f1 |= at(8, i) << i;
    f1 |= at(8, 7) << 6; f1 |= at(8, 8) << 7; f1 |= at(7, 8) << 8;
    for (i = 9; i < 15; i++) f1 |= at(14 - i, 8) << i;
    for (i = 0; i < 8; i++) f2 |= at(size - 1 - i, 8) << i;
    for (i = 8; i < 15; i++) f2 |= at(8, size - 15 + i) << i;
    var fmt = closest(FORMATS, f1, "data");
    if (fmt === null) fmt = closest(FORMATS, f2, "data");
    if (fmt === null) return null;
    var level = LEVEL_OF_BITS[fmt >> 3], mask = MASKS[fmt & 7];
    var ver = (size - 17) / 4;
    if (ver >= 7) {
      var v1 = 0, v2 = 0;
      for (i = 0; i < 18; i++) {
        var a = size - 11 + i % 3, b = Math.floor(i / 3);
        v1 |= at(a, b) << i; v2 |= at(b, a) << i;
      }
      var vv = closest(VERSIONS, v1, "ver");
      if (vv === null) vv = closest(VERSIONS, v2, "ver");
      if (vv !== null && vv * 4 + 17 !== size) return null;
    }
    var fn = functionMap(ver), cw = [], cur = 0, nb = 0;
    for (var right = size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (var vert = 0; vert < size; vert++) {
        for (var j = 0; j < 2; j++) {
          var x = right - j, upward = ((right + 1) & 2) === 0, y = upward ? size - 1 - vert : vert;
          if (fn[y][x]) continue;
          var bit = at(x, y) ^ (mask(x, y) ? 1 : 0);
          cur = (cur << 1) | bit; nb++;
          if (nb === 8) { cw.push(cur); cur = 0; nb = 0; }
        }
      }
    }
    var spec = EC[ver][level], ecLen = spec[0], blocks = [], k;
    for (k = 1; k < spec.length; k += 2) for (i = 0; i < spec[k]; i++) blocks.push({ dl: spec[k + 1], data: [] });
    var maxD = blocks[blocks.length - 1].dl, idx = 0, totalCw = 0;
    blocks.forEach(function (bl) { totalCw += bl.dl + ecLen; });
    if (cw.length < totalCw) return null;
    for (i = 0; i < maxD; i++) blocks.forEach(function (bl) { if (i < bl.dl) bl.data.push(cw[idx++]); });
    for (i = 0; i < ecLen; i++) blocks.forEach(function (bl) { bl.data.push(cw[idx++]); });
    var bytes = [];
    for (k = 0; k < blocks.length; k++) {
      if (!rsCorrect(blocks[k].data, ecLen)) return null;
      for (i = 0; i < blocks[k].dl; i++) bytes.push(blocks[k].data[i]);
    }
    return readData(bytes, ver);
  }

  // best alignment square near the expected place: the 5x5 module look (dark ring, light ring, dark centre)
  function findAlignment(bm, est, ux, uy, vx, vy, ms) {
    var best = -1, sx = 0, sy = 0, n = 0, r = Math.max(4, Math.round(ms * 5)), step = Math.max(1, Math.round(ms / 4));
    for (var dy = -r; dy <= r; dy += step) {
      for (var dx = -r; dx <= r; dx += step) {
        var cx = est.x + dx, cy = est.y + dy, score = 0;
        for (var j = -2; j <= 2; j++) {
          for (var i = -2; i <= 2; i++) {
            var want = (Math.max(Math.abs(i), Math.abs(j)) !== 1);
            if (dark(bm, cx + i * ux + j * vx, cy + i * uy + j * vy) === want) score++;
          }
        }
        if (score > best) { best = score; sx = cx; sy = cy; n = 1; }
        else if (score === best) { sx += cx; sy += cy; n++; }
      }
    }
    return best >= 23 ? { x: sx / n, y: sy / n } : null;
  }

  function tryTriple(bm, t) {
    var tl = t.tl, tr = t.tr, bl = t.bl;
    var dim = Math.round((dist(tl, tr) + dist(tl, bl)) / 2 / t.ms) + 7;
    switch (dim & 3) { case 0: dim++; break; case 2: dim--; break; case 3: dim -= 2; break; }
    var tries = [dim, dim + 4, dim - 4];
    for (var q = 0; q < tries.length; q++) {
      var size = tries[q];
      if (size < 21 || size > 177) continue;
      var ver = (size - 17) / 4, span = size - 7;
      var ux = (tr.x - tl.x) / span, uy = (tr.y - tl.y) / span, vx = (bl.x - tl.x) / span, vy = (bl.y - tl.y) / span;
      var br = { x: tr.x + bl.x - tl.x, y: tr.y + bl.y - tl.y }, brMod = size - 3.5, al = null;
      if (ver >= 2) {
        var am = size - 6.5, k = (am - 3.5);
        var est = { x: tl.x + ux * k + vx * k, y: tl.y + uy * k + vy * k };
        al = findAlignment(bm, est, ux, uy, vx, vy, t.ms);
        if (al) { br = al; brMod = am; }
      }
      // the fourth corner: the alignment square, or (version 1 / not found) the parallelogram corner, then small
      // moves around it because a tilted card is not a parallelogram
      var moves = [[0, 0]], rng = al || ver >= 2 ? 1.5 : 2.5;
      if (!al || ver < 7) {
        for (var my = -rng; my <= rng; my += 0.5) for (var mx = -rng; mx <= rng; mx += 0.5) if (mx || my) moves.push([mx, my]);
        moves.sort(function (p1, p2) { return p1[0] * p1[0] + p1[1] * p1[1] - p2[0] * p2[0] - p2[1] * p2[1]; });
      }
      for (var mv = 0; mv < moves.length; mv++) {
        var bx = br.x + moves[mv][0] * ux + moves[mv][1] * vx, by2 = br.y + moves[mv][0] * uy + moves[mv][1] * vy;
        var m = quadToQuad([3.5, 3.5, size - 3.5, 3.5, brMod, brMod, 3.5, size - 3.5],
                           [tl.x, tl.y, tr.x, tr.y, bx, by2, bl.x, bl.y]);
        var grid = sampleGrid(bm, m, size);
        var text = decodeGrid(grid, size, false);
        if (text === null) text = decodeGrid(grid, size, true);     // printed mirrored
        if (text !== null) return text;
      }
    }
    return null;
  }

  function decode(img) {
    var bm = binarize(img), found = findFinders(bm);
    if (found.length < 3) return null;
    var list = triples(found);
    for (var i = 0; i < list.length; i++) {
      var text = tryTriple(bm, list[i]);
      if (text !== null && text !== "") return text;
    }
    return null;
  }

  var api = { decode: decode, _rsCorrect: rsCorrect, _ec: EC };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.AOIQRDecode = api;
})(typeof window !== "undefined" ? window : this);
