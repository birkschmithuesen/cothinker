// CoThinker — mic level test: "move closer" rule capture (audio thread).
// Copyright (C) 2026 Birk Schmithuesen — AGPL-3.0-only, see LICENSE.
// Runs the SAME vad.js logic as the table mic page (./vad.js is a verbatim copy of
// workshop/static/vad.js; tests/test_pegeltest.py pins the two identical). Posts a
// "level" message every ~100 ms: {db, floor, speaking, dtMs} -- the main thread runs
// closer-warning.js (also a verbatim copy) on these to decide whether the "move
// closer" hint would fire, so the operator can re-test exactly the shipped rule.

import { EnergyVad, frameDb } from "./vad.js";

const FRAME_MS = 10;
const LEVEL_MS = 100;

class WarnCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions && options.processorOptions.vad) || {};
    this.frameSamples = Math.max(1, Math.round(sampleRate * FRAME_MS / 1000));
    this.vad = new EnergyVad(Object.assign({ frameMs: (this.frameSamples / sampleRate) * 1000 }, opts));
    this.levelFrames = Math.max(1, Math.round(LEVEL_MS / FRAME_MS));
    this.levelCount = 0;
    this.buf = new Float32Array(this.frameSamples);
    this.fill = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.buf[this.fill++] = ch[i];
      if (this.fill < this.frameSamples) continue;
      const f = this.buf;
      this.buf = new Float32Array(this.frameSamples);
      this.fill = 0;
      const db = frameDb(f);
      this.vad.push(db);
      this.levelCount += 1;
      if (this.levelCount >= this.levelFrames) {
        this.levelCount = 0;
        this.port.postMessage({ kind: "level", db, floor: this.vad.floor, speaking: this.vad.speaking, dtMs: LEVEL_MS });
      }
    }
    return true;
  }
}

registerProcessor("warn-capture", WarnCapture);
