/* Christmas music for the price billboard (update 87): public-domain carols played by a soft music-box sound made
   with the Web Audio API - no audio files, nothing downloaded. Songs: Jingle Bells (1857), Silent Night (1818),
   We Wish You a Merry Christmas and Deck the Halls (traditional). Browsers allow sound only after a tap / key press,
   so billboard.js calls start() from the first user action. Plain ES5. */
(function () {
  "use strict";
  var NOTE = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
  function freq(n) {                                   // "C#4" -> Hz
    var m = /^([A-G])(#|b)?(\d)$/.exec(n);
    var semi = NOTE[m[1]] + (m[2] === "#" ? 1 : m[2] === "b" ? -1 : 0) + (Number(m[3]) + 1) * 12;
    return 440 * Math.pow(2, (semi - 69) / 12);
  }
  function seq(text) {                                 // "E4:1 E4:1 R:1" -> [[note, beats], ...]
    return text.trim().split(/\s+/).map(function (t) { var p = t.split(":"); return [p[0], Number(p[1])]; });
  }
  // [name, beats per minute, melody, bass notes per bar, beats per bar]
  var SONGS = [
    ["Jingle Bells", 104, seq(
      "E4:1 E4:1 E4:2 E4:1 E4:1 E4:2 E4:1 G4:1 C4:1.5 D4:0.5 E4:4 " +
      "F4:1 F4:1 F4:1.5 F4:0.5 F4:1 E4:1 E4:1 E4:0.5 E4:0.5 E4:1 D4:1 D4:1 E4:1 D4:2 G4:2 " +
      "E4:1 E4:1 E4:2 E4:1 E4:1 E4:2 E4:1 G4:1 C4:1.5 D4:0.5 E4:4 " +
      "F4:1 F4:1 F4:1.5 F4:0.5 F4:1 E4:1 E4:1 E4:0.5 E4:0.5 G4:1 G4:1 F4:1 D4:1 C4:4"),
      "C3 C3 C3 C3 F3 C3 G3 G3 C3 C3 C3 C3 F3 C3 G3 C3", 4],
    ["Silent Night", 66, seq(
      "G4:1.5 A4:0.5 G4:1 E4:3 G4:1.5 A4:0.5 G4:1 E4:3 D5:2 D5:1 B4:3 C5:2 C5:1 G4:3 " +
      "A4:2 A4:1 C5:1.5 B4:0.5 A4:1 G4:1.5 A4:0.5 G4:1 E4:3 A4:2 A4:1 C5:1.5 B4:0.5 A4:1 G4:1.5 A4:0.5 G4:1 E4:3 " +
      "D5:2 D5:1 F5:1.5 D5:0.5 B4:1 C5:3 E5:3 C5:1 G4:1 E4:1 G4:1.5 F4:0.5 D4:1 C4:6"),
      "C3 C3 C3 C3 G2 G2 C3 C3 F2 F2 C3 C3 F2 F2 C3 C3 G2 G2 C3 C3 C3 G2 C3 C3", 3],
    ["We Wish You a Merry Christmas", 120, seq(
      "G4:1 C5:1 C5:0.5 D5:0.5 C5:0.5 B4:0.5 A4:1 A4:1 A4:1 D5:1 D5:0.5 E5:0.5 D5:0.5 C5:0.5 B4:1 G4:1 G4:1 " +
      "E5:1 E5:0.5 F5:0.5 E5:0.5 D5:0.5 C5:1 A4:1 G4:0.5 G4:0.5 A4:1 D5:1 B4:1 C5:3"),
      "G2 C3 F2 D3 G2 E3 A2 F2 G2 C3", 3],
    ["Deck the Halls", 100, seq(
      "G4:1.5 F4:0.5 E4:1 D4:1 C4:1 D4:1 E4:1 C4:1 D4:0.5 E4:0.5 F4:0.5 D4:0.5 E4:1.5 D4:0.5 C4:1 B3:1 C4:2 R:2 " +
      "G4:1.5 F4:0.5 E4:1 D4:1 C4:1 D4:1 E4:1 C4:1 D4:0.5 E4:0.5 F4:0.5 D4:0.5 E4:1.5 D4:0.5 C4:1 B3:1 C4:2 R:2"),
      "C3 C3 G2 C3 C3 C3 G2 C3", 4]
  ];

  var ctx = null, master = null, wet = null, playing = false, timer = null, song = 0, volume = 0.3;

  function setup() {
    if (ctx) return true;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return false;
    ctx = new AC();
    master = ctx.createGain(); master.gain.value = 0; master.connect(ctx.destination);
    // a soft room: a filtered feedback delay
    var delay = ctx.createDelay(); delay.delayTime.value = 0.23;
    var fb = ctx.createGain(); fb.gain.value = 0.32;
    var lp = ctx.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 2400;
    wet = ctx.createGain(); wet.gain.value = 0.35;
    wet.connect(delay); delay.connect(lp); lp.connect(fb); fb.connect(delay); lp.connect(master);
    return true;
  }

  function bell(f, t, dur, level) {                    // music box: sine + soft overtone, quick attack, long decay
    var g = ctx.createGain(), o1 = ctx.createOscillator(), o2 = ctx.createOscillator(), g2 = ctx.createGain();
    o1.type = "sine"; o1.frequency.value = f; o2.type = "sine"; o2.frequency.value = f * 3.01; g2.gain.value = 0.12;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(level, t + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, t + Math.max(0.6, dur * 2.2));
    o1.connect(g); o2.connect(g2); g2.connect(g); g.connect(master); g.connect(wet);
    o1.start(t); o2.start(t); o1.stop(t + dur * 2.4 + 0.7); o2.stop(t + dur * 2.4 + 0.7);
  }
  function pad(f, t, dur, level) {                     // a quiet warm bass under the melody
    var g = ctx.createGain(), o = ctx.createOscillator(), lp = ctx.createBiquadFilter();
    o.type = "triangle"; o.frequency.value = f; lp.type = "lowpass"; lp.frequency.value = 600;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(level, t + 0.15);
    g.gain.linearRampToValueAtTime(0.0001, t + dur);
    o.connect(lp); lp.connect(g); g.connect(master);
    o.start(t); o.stop(t + dur + 0.1);
  }

  function playSong() {
    if (!playing) return;
    var s = SONGS[song % SONGS.length], beat = 60 / s[1], t = ctx.currentTime + 0.3, start = t;
    s[2].forEach(function (n) {
      if (n[0] !== "R") bell(freq(n[0]), t, n[1] * beat, 0.32);
      t += n[1] * beat;
    });
    var bar = s[4] * beat;
    s[3].split(" ").forEach(function (b, i) { pad(freq(b), start + i * bar, bar * 0.95, 0.06); });
    song++;
    timer = setTimeout(playSong, (t - ctx.currentTime + 3) * 1000);      // 3 seconds between songs
  }

  function fade(to) {
    var now = ctx.currentTime;
    master.gain.cancelScheduledValues(now);
    master.gain.setValueAtTime(master.gain.value, now);
    master.gain.linearRampToValueAtTime(to, now + 1.5);
  }

  window.AOIXmasMusic = {
    start: function () {                               // must be called from a tap / key press the first time
      if (!setup()) return false;
      if (ctx.state === "suspended" && ctx.resume) ctx.resume();
      if (!playing) { playing = true; playSong(); }
      fade(volume * 0.9);
      return ctx.state !== "suspended";
    },
    stop: function () {
      if (!ctx || !playing) return;
      playing = false; clearTimeout(timer); fade(0);
    },
    setVolume: function (pct) { volume = Math.max(0, Math.min(100, pct || 0)) / 100; if (ctx && playing) fade(volume * 0.9); },
    running: function () { return !!(ctx && playing && ctx.state === "running"); },
    songs: SONGS.map(function (s) { return s[0]; })
  };
})();
