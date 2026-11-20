// CoThinker — phone page: "move closer" hint for the table mic.
// Copyright (C) 2026 Birk Schmithuesen — AGPL-3.0-only, see LICENSE.
// Pure; node-tested by tests/test_workshop_js.py. Fed by vad.js's EnergyVad: speaking
// (its hysteresis state), the current frame's raw dBFS and its floor (frozen while
// speaking -- the floor stays at the room level, not the mouth).
//
// Thresholds: measurement soundtest-2026-10-27.md / pegeltest-2026-10-27-xperia5.json;
// approved by the operator 2026-10-28 (comment ~1793177780):
// "threshold approved as proposed (SNR 12 dB / 3 s, flags)."
//
// v2 (2026-10-28): a single frame's SNR was too noisy to hold a
// timer against, and a pause between sentences reset the hold/hide timers to zero
// every time, so the hint almost never reached CLOSER_HOLD_S in real speech. Fixed
// by (a) comparing a ROBUST speech level -- a percentile over the last
// levelWindowMs of SPEAKING frames, not the current frame alone -- against the
// frozen floor, and (b) accumulating belowMs/aboveMs over SPEAKING TIME only: a
// pause leaves them exactly where they were, it never resets them.
//
// Known limitation, by design, not fixed here: the VAD classifies by energy, not by
// who is speaking. In this room, another table's babble can register as "speech"
// here too, freezing the floor and building the speech-level window on a non-silent
// segment that is not this table's speaker. That is an accepted VAD limitation;
// this file does not attempt to tell voices apart.

export const CLOSER_ABS_DB = -51;   // CLOSER_ABS_DB: Xperia 40/80 cm anchor, pegeltest-2026-10-27-xperia5.json

export const CLOSER_DEFAULTS = {
  snrDb: 12,              // CLOSER_SNR_DB: robust speech level over floor below this is "too quiet"
  holdS: 3,               // CLOSER_HOLD_S: how long that must hold, in speaking time, before the hint shows
  hideS: 1,               // how long above threshold, in speaking time, before the hint hides again
  levelWindowMs: 2000,    // CLOSER_LEVEL_WINDOW_MS: speech-level window, in SPEAKING time
  levelPercentile: 80,    // CLOSER_LEVEL_PERCENTILE: percentile of that window used as "the" speech level
  absDb: CLOSER_ABS_DB,   // CLOSER_ABS_DB override point
  mode: "snr",            // "snr" (robust level vs. frozen floor) | "abs" (robust level alone) drives the warning
};

export class CloserWarning {
  constructor(opts = {}) {
    this.o = Object.assign({}, CLOSER_DEFAULTS, opts);
    this.warning = false;
    this.belowMs = 0;
    this.aboveMs = 0;
    this.window = [];    // [{db, dtMs}, ...] of SPEAKING frames only, trimmed to levelWindowMs
    this.level = null;   // robust speech level (percentile over the window); null until first speaking frame
    this.snr = null;      // level - this frame's floor; null until level is known
  }

  _pushLevel(db, dtMs) {
    this.window.push({ db, dtMs });
    let total = 0;
    for (const w of this.window) total += w.dtMs;
    while (total > this.o.levelWindowMs && this.window.length > 1) {
      total -= this.window[0].dtMs;
      this.window.shift();
    }
    const sorted = this.window.map((w) => w.db).sort((a, b) => a - b);
    const idx = Math.min(sorted.length - 1, Math.floor(this.o.levelPercentile / 100 * sorted.length));
    this.level = sorted[idx];
  }

  // speaking: vad.js's EnergyVad hysteresis state for this frame.
  // db: this frame's raw dBFS level.
  // floorDb: vad.js's current noise floor (frozen while speaking).
  // dtMs: time this frame covers.
  // Returns the hint's state after this frame (also `this.warning`).
  push(speaking, db, floorDb, dtMs) {
    if (!speaking) return this.warning;   // a pause freezes everything; it is never a reset
    this._pushLevel(db, dtMs);
    this.snr = this.level - floorDb;
    const below = this.o.mode === "abs" ? this.level < this.o.absDb : this.snr < this.o.snrDb;
    if (below) {
      this.belowMs += dtMs;
      this.aboveMs = 0;
      if (this.belowMs >= this.o.holdS * 1000) this.warning = true;
    } else {
      this.aboveMs += dtMs;
      this.belowMs = 0;
      if (this.aboveMs >= this.o.hideS * 1000) this.warning = false;
    }
    return this.warning;
  }

  reset() {
    this.warning = false;
    this.belowMs = 0;
    this.aboveMs = 0;
    this.window = [];
    this.level = null;
    this.snr = null;
  }
}
