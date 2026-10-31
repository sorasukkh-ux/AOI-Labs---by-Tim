/* Songkran music for the price billboard (update 104): an ORIGINAL cheerful tune in the Thai pentatonic scale
   (C D E G A), composed for AOI - not a copy of any existing Songkran song. Played with the Web Audio API, no audio
   files: a ranat-like xylophone melody, a soft khong (gong) bass, a klong drum and ching-chap finger cymbals in a
   bouncy ramwong-style beat. Same interface as AOIXmasMusic: start() after a tap, stop(), setVolume(), running(). */
(function () {
  "use strict";
  var NOTE = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
  function freq(n) {
    var m = /^([A-G])(\d)$/.exec(n);
    return 440 * Math.pow(2, (NOTE[m[1]] + (Number(m[2]) + 1) * 12 - 69) / 12);
  }
  function seq(text) { return text.trim().split(/\s+/).map(function (t) { var p = t.split(":"); return [p[0], Number(p[1])]; }); }
  // four 8-bar phrases (4/4); R = rest. Pentatonic only, so every part fits together.
  var PHRASES = [
    seq("E5:.5 G5:.5 A5:1 G5:.5 E5:.5 D5:1 E5:.5 D5:.5 C5:.5 D5:.5 E5:2 " +
        "G5:.5 A5:.5 C6:1 A5:.5 G5:.5 E5:1 D5:.5 E5:.5 G5:.5 E5:.5 D5:2 " +
        "E5:.5 G5:.5 A5:1 C6:.5 A5:.5 G5:1 A5:.5 G5:.5 E5:.5 D5:.5 C5:1 R:1 " +
        "D5:.5 E5:.5 G5:.5 A5:.5 G5:.5 E5:.5 D5:.5 E5:.5 C5:3 R:1"),
    seq("A5:1 C6:.5 A5:.5 G5:1 E5:1 G5:.5 A5:.5 G5:.5 E5:.5 D5:2 " +
        "C5:.5 D5:.5 E5:.5 G5:.5 A5:1 G5:1 E5:.5 G5:.5 E5:.5 D5:.5 E5:2 " +
        "A5:1 C6:.5 D6:.5 C6:1 A5:1 G5:.5 A5:.5 C6:.5 A5:.5 G5:2 " +
        "E5:.5 G5:.5 E5:.5 D5:.5 C5:.5 D5:.5 E5:.5 D5:.5 C5:3 R:1"),
    seq("G5:.5 G5:.5 A5:.5 G5:.5 E5:1 D5:1 E5:.5 E5:.5 G5:.5 E5:.5 D5:1 C5:1 " +
        "D5:.5 E5:.5 G5:1 A5:.5 G5:.5 E5:1 G5:.5 A5:.5 C6:.5 A5:.5 G5:2 " +
        "C6:.5 C6:.5 A5:.5 G5:.5 A5:1 G5:1 E5:.5 G5:.5 A5:.5 G5:.5 E5:1 D5:1 " +
        "E5:.5 D5:.5 C5:.5 D5:.5 E5:.5 G5:.5 E5:.5 D5:.5 C5:3 R:1"),
    seq("C6:1 A5:1 G5:.5 A5:.5 C6:1 D6:.5 C6:.5 A5:.5 G5:.5 A5:2 " +
        "G5:.5 E5:.5 G5:.5 A5:.5 G5:1 E5:1 D5:.5 E5:.5 D5:.5 C5:.5 D5:2 " +
        "E5:.5 G5:.5 A5:.5 C6:.5 D6:1 C6:1 A5:.5 C6:.5 A5:.5 G5:.5 E5:2 " +
        "G5:.5 E5:.5 D5:.5 E5:.5 G5:.5 A5:.5 G5:.5 E5:.5 C5:3 R:1")
  ];
  var BASS = ["C3", "G2", "A2", "G2", "C3", "E3", "G2", "C3"];     // one per bar, khong-like
  var BPM = 128;

  var ctx = null, master = null, wet = null, noise = null, playing = false, timer = null, part = 0, volume = 0.3;

  function setup() {
    if (ctx) return true;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return false;
    ctx = new AC();
    master = ctx.createGain(); master.gain.value = 0; master.connect(ctx.destination);
    var delay = ctx.createDelay(); delay.delayTime.value = 0.18;
    var fb = ctx.createGain(); fb.gain.value = 0.22;
    var lp = ctx.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 3000;
    wet = ctx.createGain(); wet.gain.value = 0.25;
    wet.connect(delay); delay.connect(lp); lp.connect(fb); fb.connect(delay); lp.connect(master);
    noise = ctx.createBuffer(1, ctx.sampleRate * 0.5, ctx.sampleRate);         // for the ching-chap
    var d = noise.getChannelData(0);
    for (var i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
    return true;
  }

  function ranat(f, t, dur) {                         // wooden xylophone: bright attack, quick decay, a roll on long notes
    var strikes = dur >= 1 ? Math.round(dur * 4) : 1;
    for (var k = 0; k < strikes; k++) {
      var s = t + k * (dur / strikes), g = ctx.createGain(), o = ctx.createOscillator(), o2 = ctx.createOscillator(), g2 = ctx.createGain();
      o.type = "triangle"; o.frequency.value = f; o2.type = "sine"; o2.frequency.value = f * 4.0; g2.gain.value = 0.18;
      var lvl = k === 0 ? 0.32 : 0.18;
      g.gain.setValueAtTime(0.0001, s); g.gain.exponentialRampToValueAtTime(lvl, s + 0.006);
      g.gain.exponentialRampToValueAtTime(0.0001, s + 0.32);
      o.connect(g); o2.connect(g2); g2.connect(g); g.connect(master); g.connect(wet);
      o.start(s); o2.start(s); o.stop(s + 0.36); o2.stop(s + 0.36);
    }
  }
  function khong(f, t) {                              // soft gong-like bass
    var g = ctx.createGain(), o = ctx.createOscillator(), o2 = ctx.createOscillator();
    o.type = "sine"; o.frequency.value = f; o2.type = "sine"; o2.frequency.value = f * 2.76;
    var g2 = ctx.createGain(); g2.gain.value = 0.25;
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.22, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + 1.6);
    o.connect(g); o2.connect(g2); g2.connect(g); g.connect(master);
    o.start(t); o2.start(t); o.stop(t + 1.7); o2.stop(t + 1.7);
  }
  function klong(t, level) {                          // drum: a short falling thump
    var g = ctx.createGain(), o = ctx.createOscillator();
    o.type = "sine"; o.frequency.setValueAtTime(150, t); o.frequency.exponentialRampToValueAtTime(55, t + 0.18);
    g.gain.setValueAtTime(level, t); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.25);
    o.connect(g); g.connect(master); o.start(t); o.stop(t + 0.28);
  }
  function cymbal(t, open) {                          // ching (open, rings) / chap (closed, short)
    var src = ctx.createBufferSource(), hp = ctx.createBiquadFilter(), bp = ctx.createBiquadFilter(), g = ctx.createGain();
    src.buffer = noise; hp.type = "highpass"; hp.frequency.value = 6000; bp.type = "bandpass"; bp.frequency.value = open ? 8200 : 7000; bp.Q.value = open ? 6 : 2;
    var len = open ? 0.45 : 0.06;
    g.gain.setValueAtTime(open ? 0.12 : 0.09, t); g.gain.exponentialRampToValueAtTime(0.0001, t + len);
    src.connect(hp); hp.connect(bp); bp.connect(g); g.connect(master);
    src.start(t); src.stop(t + len + 0.02);
  }

  function playPart() {
    if (!playing) return;
    var mel = PHRASES[part % PHRASES.length], beat = 60 / BPM, t0 = ctx.currentTime + 0.25, t = t0;
    mel.forEach(function (n) { if (n[0] !== "R") ranat(freq(n[0]), t, n[1] * beat); t += n[1] * beat; });
    var bars = Math.round((t - t0) / (4 * beat));
    for (var b = 0; b < bars; b++) {
      var bt = t0 + b * 4 * beat;
      khong(freq(BASS[b % BASS.length]), bt);
      klong(bt, 0.5); klong(bt + 1.5 * beat, 0.3); klong(bt + 2 * beat, 0.45); klong(bt + 3.5 * beat, 0.25);
      for (var q = 0; q < 4; q++) { cymbal(bt + q * beat + 0.5 * beat, true); cymbal(bt + q * beat, false); }   // ramwong: ching on the off-beat
    }
    part++;
    timer = setTimeout(playPart, (t - ctx.currentTime) * 1000 - 120);           // the next phrase follows without a gap
  }

  function fade(to) {
    var now = ctx.currentTime;
    master.gain.cancelScheduledValues(now); master.gain.setValueAtTime(master.gain.value, now); master.gain.linearRampToValueAtTime(to, now + 1.2);
  }

  window.AOISongkranMusic = {
    start: function () {
      if (!setup()) return false;
      if (ctx.state === "suspended" && ctx.resume) ctx.resume();
      if (!playing) { playing = true; playPart(); }
      fade(volume * 0.9);
      return ctx.state !== "suspended";
    },
    stop: function () { if (!ctx || !playing) return; playing = false; clearTimeout(timer); fade(0); },
    setVolume: function (pct) { volume = Math.max(0, Math.min(100, pct || 0)) / 100; if (ctx && playing) fade(volume * 0.9); },
    running: function () { return !!(ctx && playing && ctx.state === "running"); }
  };
})();
