// CoThinker — mic level test: capture worklet (audio thread).
// Copyright (C) 2026 Birk Schmithuesen — AGPL-3.0-only, see LICENSE.
// Posts raw input blocks to the main thread; all analysis happens there.

class PegelCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Float32Array(2048);
    this.fill = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.buf[this.fill++] = ch[i];
      if (this.fill === this.buf.length) {
        this.port.postMessage(this.buf, [this.buf.buffer]);
        this.buf = new Float32Array(2048);
        this.fill = 0;
      }
    }
    return true;
  }
}

registerProcessor("pegel-capture", PegelCapture);
