/* ===== QR code generator (runs in the page, no external library or service) =====
   QR Code Model 2, byte mode, error correction level M, versions 1-6 (up to 106 bytes of text).
   AOIQR.encode(text) returns a square array of booleans (true = dark module).
   AOIQR.toSvg(matrix, title) returns SVG markup. Written for this project; verified by decoding with OpenCV. */
window.AOIQR = (function () {
  "use strict";
  // Galois field GF(256) with the QR polynomial x^8 + x^4 + x^3 + x^2 + 1
  var EXP = new Array(512), LOG = new Array(256);
  (function () {
    var x = 1;
    for (var i = 0; i < 255; i++) { EXP[i] = x; LOG[x] = i; x <<= 1; if (x & 0x100) x ^= 0x11d; }
    for (var j = 255; j < 512; j++) EXP[j] = EXP[j - 255];
  })();
  function mul(a, b) { return (a === 0 || b === 0) ? 0 : EXP[LOG[a] + LOG[b]]; }

  // Reed-Solomon error correction codewords for one block
  function ecCodewords(data, n) {
    var gen = [1];
    for (var i = 0; i < n; i++) {
      var next = new Array(gen.length + 1).fill(0);
      for (var j = 0; j < gen.length; j++) { next[j] ^= gen[j]; next[j + 1] ^= mul(gen[j], EXP[i]); }
      gen = next;
    }
    var rem = new Array(n).fill(0);
    data.forEach(function (b) {
      var factor = b ^ rem.shift();
      rem.push(0);
      for (var k = 0; k < n; k++) rem[k] ^= mul(gen[k + 1], factor);
    });
    return rem;
  }

  // Level M block layout per version: number of blocks, data codewords per block, EC codewords per block
  var VERSIONS = [null,
    { blocks: 1, data: 16, ec: 10 }, { blocks: 1, data: 28, ec: 16 }, { blocks: 1, data: 44, ec: 26 },
    { blocks: 2, data: 32, ec: 18 }, { blocks: 2, data: 43, ec: 24 }, { blocks: 4, data: 27, ec: 16 }];

  function utf8Bytes(text) {
    var out = [], s = unescape(encodeURIComponent(String(text)));
    for (var i = 0; i < s.length; i++) out.push(s.charCodeAt(i));
    return out;
  }

  var MASKS = [
    function (r, c) { return (r + c) % 2 === 0; },
    function (r, c) { return r % 2 === 0; },
    function (r, c) { return c % 3 === 0; },
    function (r, c) { return (r + c) % 3 === 0; },
    function (r, c) { return (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0; },
    function (r, c) { return (r * c) % 2 + (r * c) % 3 === 0; },
    function (r, c) { return ((r * c) % 2 + (r * c) % 3) % 2 === 0; },
    function (r, c) { return ((r + c) % 2 + (r * c) % 3) % 2 === 0; }
  ];

  function penalty(m) {
    var n = m.length, score = 0, dark = 0, r, c;
    function lineScore(get) {
      var s = 0;
      for (var a = 0; a < n; a++) {
        var run = 1, bits = "";
        for (var b = 0; b < n; b++) {
          bits += get(a, b) ? "1" : "0";
          if (b > 0) {
            if (get(a, b) === get(a, b - 1)) { run++; if (run === 5) s += 3; else if (run > 5) s += 1; }
            else run = 1;
          }
        }
        var idx = -1;
        while ((idx = bits.indexOf("10111010000", idx + 1)) !== -1) s += 40;
        idx = -1;
        while ((idx = bits.indexOf("00001011101", idx + 1)) !== -1) s += 40;
      }
      return s;
    }
    score += lineScore(function (a, b) { return m[a][b]; });
    score += lineScore(function (a, b) { return m[b][a]; });
    for (r = 0; r < n; r++) for (c = 0; c < n; c++) {
      if (m[r][c]) dark++;
      if (r < n - 1 && c < n - 1 && m[r][c] === m[r][c + 1] && m[r][c] === m[r + 1][c] && m[r][c] === m[r + 1][c + 1]) score += 3;
    }
    score += Math.floor(Math.abs(dark * 20 - n * n * 10) / (n * n)) * 10;
    return score;
  }

  function encode(text, forceMask) {
    var bytes = utf8Bytes(text), version = 0;
    for (var v = 1; v < VERSIONS.length; v++) {
      if (bytes.length + 2 <= VERSIONS[v].blocks * VERSIONS[v].data) { version = v; break; }
    }
    if (!version) throw new Error("QR text too long: " + bytes.length + " bytes (max 106)");
    var spec = VERSIONS[version], capacity = spec.blocks * spec.data, n = version * 4 + 17;

    // data bit stream: byte mode indicator, 8-bit length, data, terminator, padding
    var bits = [];
    function push(value, len) { for (var i = len - 1; i >= 0; i--) bits.push((value >>> i) & 1); }
    push(4, 4); push(bytes.length, 8);
    bytes.forEach(function (b) { push(b, 8); });
    push(0, Math.min(4, capacity * 8 - bits.length));
    while (bits.length % 8 !== 0) bits.push(0);
    var codewords = [];
    for (var i = 0; i < bits.length; i += 8) codewords.push(parseInt(bits.slice(i, i + 8).join(""), 2));
    for (var pad = 0; codewords.length < capacity; pad++) codewords.push(pad % 2 === 0 ? 0xEC : 0x11);

    // split into blocks, add error correction, interleave
    var dataBlocks = [], ecBlocks = [];
    for (var b = 0; b < spec.blocks; b++) {
      var block = codewords.slice(b * spec.data, (b + 1) * spec.data);
      dataBlocks.push(block); ecBlocks.push(ecCodewords(block, spec.ec));
    }
    var stream = [];
    for (i = 0; i < spec.data; i++) dataBlocks.forEach(function (bl) { stream.push(bl[i]); });
    for (i = 0; i < spec.ec; i++) ecBlocks.forEach(function (bl) { stream.push(bl[i]); });
    var streamBits = [];
    stream.forEach(function (cw) { for (var k = 7; k >= 0; k--) streamBits.push((cw >>> k) & 1); });

    // function patterns
    var m = [], reserved = [], r, c;
    for (r = 0; r < n; r++) { m.push(new Array(n).fill(false)); reserved.push(new Array(n).fill(false)); }
    function set(row, col, dark) { if (row < 0 || col < 0 || row >= n || col >= n) return; m[row][col] = dark; reserved[row][col] = true; }
    function finder(row, col) {
      for (var dr = -1; dr <= 7; dr++) for (var dc = -1; dc <= 7; dc++) {
        var inside = dr >= 0 && dr <= 6 && dc >= 0 && dc <= 6;
        var dark = inside && (dr === 0 || dr === 6 || dc === 0 || dc === 6 || (dr >= 2 && dr <= 4 && dc >= 2 && dc <= 4));
        set(row + dr, col + dc, dark);
      }
    }
    finder(0, 0); finder(0, n - 7); finder(n - 7, 0);
    for (i = 8; i < n - 8; i++) { set(6, i, i % 2 === 0); set(i, 6, i % 2 === 0); }
    if (version >= 2) {
      for (var ar = -2; ar <= 2; ar++) for (var ac = -2; ac <= 2; ac++) {
        set(n - 7 + ar, n - 7 + ac, Math.max(Math.abs(ar), Math.abs(ac)) !== 1);
      }
    }
    // reserve the format information areas (filled in after masking)
    for (i = 0; i <= 8; i++) { if (!reserved[8][i]) set(8, i, false); if (!reserved[i][8]) set(i, 8, false); }
    for (i = 0; i < 8; i++) { set(8, n - 1 - i, false); set(n - 1 - i, 8, false); }
    set(n - 8, 8, true);   // dark module

    // place data in the zigzag pattern
    var bitIdx = 0, up = true;
    for (var right = n - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (var step = 0; step < n; step++) {
        var row = up ? n - 1 - step : step;
        for (var k = 0; k < 2; k++) {
          var col = right - k;
          if (reserved[row][col]) continue;
          m[row][col] = bitIdx < streamBits.length ? streamBits[bitIdx] === 1 : false;
          bitIdx++;
        }
      }
      up = !up;
    }

    function withMask(mask) {
      var out = m.map(function (line) { return line.slice(); });
      for (var rr = 0; rr < n; rr++) for (var cc = 0; cc < n; cc++) {
        if (!reserved[rr][cc] && MASKS[mask](rr, cc)) out[rr][cc] = !out[rr][cc];
      }
      // format information: level M (00) + mask, BCH(15,5), XOR 0x5412
      var data = mask, rem = data;
      for (var q = 0; q < 10; q++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
      var fmt = ((data << 10) | rem) ^ 0x5412;
      function bit(q2) { return ((fmt >>> q2) & 1) === 1; }
      for (q = 0; q <= 5; q++) out[q][8] = bit(q);
      out[7][8] = bit(6); out[8][8] = bit(7); out[8][7] = bit(8);
      for (q = 9; q < 15; q++) out[8][14 - q] = bit(q);
      for (q = 0; q < 8; q++) out[8][n - 1 - q] = bit(q);
      for (q = 8; q < 15; q++) out[n - 15 + q][8] = bit(q);
      out[n - 8][8] = true;
      return out;
    }

    if (typeof forceMask === "number") return withMask(forceMask);
    var best = null, bestScore = Infinity;
    for (var mk = 0; mk < 8; mk++) {
      var candidate = withMask(mk), sc = penalty(candidate);
      if (sc < bestScore) { bestScore = sc; best = candidate; }
    }
    return best;
  }

  // SVG markup for a module matrix, with the 4-module quiet zone the standard requires
  function toSvg(matrix, title) {
    var n = matrix.length, quiet = 4, size = n + quiet * 2, d = "";
    for (var r = 0; r < n; r++) for (var c = 0; c < n; c++) {
      if (matrix[r][c]) d += "M" + (c + quiet) + " " + (r + quiet) + "h1v1h-1z";
    }
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + size + ' ' + size + '" shape-rendering="crispEdges" role="img" aria-label="' + (title || "QR code") + '">' +
      '<rect width="' + size + '" height="' + size + '" fill="#ffffff"/><path d="' + d + '" fill="#000000"/></svg>';
  }

  return { encode: encode, toSvg: toSvg };
})();
