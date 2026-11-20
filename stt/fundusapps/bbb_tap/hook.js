// stt/fundusapps/bbb_tap/hook.js
// Injected before any page script (Page.addScriptToEvaluateOnNewDocument).
// Wraps RTCPeerConnection so every REMOTE audio track -- BBB's listen-only
// stream or full audio -- is mixed into one 16 kHz AudioContext and handed
// to Python as int16 PCM through the CDP binding window.__roboAudio.
// This is the "virtual sound card": no OS audio device is involved.
(function () {
  var w = window;
  if (w.__roboTap) { return; }
  var RATE = 16000, FRAME = 2048;
  var ctx = null, mix = null, seen = {}, keep = [], nTracks = 0, nChunks = 0, tapped = [];
  // Per-track mode (tap flag --per-track): Python sets
  // window.__roboPerTrack = true in a script injected before this one.
  // Each tapped track then additionally gets its own ScriptProcessor whose
  // chunks go out as "<n>:<base64>" through window.__roboTrack. The mixed
  // path above is unchanged.
  var trackIds = [];

  // Tapped tracks still delivering. When Robo is removed from the room the
  // remote tracks end, but the ScriptProcessor keeps firing with zeros --
  // the chunk count alone cannot see that. Python polls this number.
  function liveTracks() {
    var n = 0;
    for (var i = 0; i < tapped.length; i++) { if (tapped[i].readyState === 'live') { n++; } }
    return n;
  }

  function event(obj) { try { if (w.__roboEvent) { w.__roboEvent(JSON.stringify(obj)); } } catch (e) {} }

  function floatToInt16(f32) {
    var out = new Int16Array(f32.length);
    for (var i = 0; i < f32.length; i++) {
      var s = f32[i] > 1 ? 1 : (f32[i] < -1 ? -1 : f32[i]);
      out[i] = s < 0 ? Math.max(-32768, Math.round(s * 32768)) : Math.min(32767, Math.round(s * 32768));
    }
    return out;
  }

  function toBase64(u8) {
    var s = '';
    for (var i = 0; i < u8.length; i += 0x8000) {
      s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
    }
    return w.btoa(s);
  }

  function ensureGraph() {
    if (ctx) { return; }
    ctx = new w.AudioContext({ sampleRate: RATE });
    mix = ctx.createGain();
    var proc = ctx.createScriptProcessor(FRAME, 1, 1);
    proc.onaudioprocess = function (e) {
      var pcm = floatToInt16(e.inputBuffer.getChannelData(0));
      nChunks++;
      if (w.__roboAudio) { w.__roboAudio(toBase64(new Uint8Array(pcm.buffer))); }
    };
    // A ScriptProcessor only runs while connected to the destination; the
    // zero gain keeps the tap silent on whatever output Chromium has.
    var mute = ctx.createGain();
    mute.gain.value = 0;
    mix.connect(proc); proc.connect(mute); mute.connect(ctx.destination);
    keep.push(proc, mute);
    event({ kind: 'ctx', state: ctx.state });
  }

  function tapTrack(track) {
    if (!track || track.kind !== 'audio' || seen[track.id]) { return; }
    seen[track.id] = true;
    nTracks++;
    tapped.push(track);
    ensureGraph();
    var stream = new w.MediaStream([track]);
    // Chromium only pulls audio from a remote WebRTC track while some media
    // element plays it; a muted element is enough.
    var el = new w.Audio();
    el.muted = true;
    el.srcObject = stream;
    var p = el.play(); if (p && p.catch) { p.catch(function () {}); }
    var src = ctx.createMediaStreamSource(stream);
    src.connect(mix);
    keep.push(el, src);
    var n = nTracks;               // 1-based track number, stable for this page
    trackIds.push({ n: n, id: track.id });
    if (w.__roboPerTrack) {
      var tproc = ctx.createScriptProcessor(FRAME, 1, 1);
      tproc.onaudioprocess = function (e) {
        var pcm = floatToInt16(e.inputBuffer.getChannelData(0));
        if (w.__roboTrack) { w.__roboTrack(n + ':' + toBase64(new Uint8Array(pcm.buffer))); }
      };
      var tmute = ctx.createGain();
      tmute.gain.value = 0;
      src.connect(tproc); tproc.connect(tmute); tmute.connect(ctx.destination);
      keep.push(tproc, tmute);
    }
    var r = ctx.resume(); if (r && r.catch) { r.catch(function () {}); }
    event({ kind: 'track' });
  }

  var Orig = w.RTCPeerConnection;
  if (Orig) {
    var Wrapped = function (cfg, constraints) {
      var pc = new Orig(cfg, constraints);
      pc.addEventListener('track', function (ev) { tapTrack(ev.track); });
      return pc;
    };
    // Sharing the prototype object (not copying its properties onto a new
    // one) is what keeps `pc instanceof RTCPeerConnection` (and instanceof
    // the original class) true for objects built by Wrapped.
    Wrapped.prototype = Orig.prototype;
    Object.setPrototypeOf(Wrapped, Orig);   // keeps static generateCertificate
    w.RTCPeerConnection = Wrapped;
    w.webkitRTCPeerConnection = Wrapped;
  }

  w.__roboTap = {
    floatToInt16: floatToInt16,
    toBase64: toBase64,
    stats: function () { return { tracks: nTracks, live: liveTracks(), ctx: ctx ? ctx.state : null, chunks: nChunks }; },
    // [{n, id}] -- tap track number -> MediaStreamTrack id, for the
    // per-track sidecar (Python maps the id to a participant).
    trackIds: function () { return trackIds.slice(); },
    // The context is born inside a 'track' event, which carries no user
    // activation, so Chrome's autoplay policy keeps it 'suspended' and the
    // ScriptProcessor never fires. The join step calls this from a CDP
    // Runtime.evaluate with userGesture:true, which is what lets it start.
    // Returns the state *before* the async resume settles.
    resume: function () {
      if (ctx && ctx.state !== 'running') {
        var r = ctx.resume(); if (r && r.catch) { r.catch(function () {}); }
      }
      return ctx ? ctx.state : null;
    }
  };
})();
