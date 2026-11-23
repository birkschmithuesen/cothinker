#!/usr/bin/env python3
"""Build the synthetic speakers for the acceptance run.

Writes alex.wav and sam.wav (48 kHz mono s16, same length) plus script.json
(ground truth) into --out-dir. German sentences are rendered with espeak-ng
and normalised with ffmpeg. The speakers alternate in 8 s slots; a sentence
never fills more than slot - 4 s, so there are always >= 4 s of silence
between two utterances. Every sentence carries a unique keyword, which is
what score.py looks for in the transcript.

usage: make_tts.py --out-dir DIR [--espeak espeak-ng] [--ffmpeg ffmpeg]
                   [--per-speaker 24] [--lead-in 15]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

RATE = 48000
SLOT_S = 8
MIN_GAP_S = 4

# (sentence template with {kw}, keyword) -- keywords are unique, common
# German nouns that a recogniser spells reliably.
KEYWORDS = [
    "Apfelbaum", "Fahrrad", "Sonnenblume", "Kühlschrank", "Regenschirm",
    "Bibliothek", "Schmetterling", "Wasserfall", "Bahnhof", "Zitronenkuchen",
    "Leuchtturm", "Briefkasten", "Gewitter", "Kartoffelsalat", "Hubschrauber",
    "Schneemann", "Fernseher", "Marmelade", "Computertastatur", "Brunnen",
    "Kirschbaum", "Straßenbahn", "Wandkalender", "Teekanne", "Segelboot",
    "Gartenzaun", "Kaffeemühle", "Taschenlampe", "Fußballplatz", "Glockenspiel",
    "Holzbrücke", "Pfannkuchen", "Heißluftballon", "Mühlenrad", "Zahnbürste",
    "Eisenbahn", "Waschmaschine", "Löwenzahn", "Stundenplan", "Fensterbank",
    "Dachterrasse", "Notizbuch", "Rucksack", "Blumenwiese", "Schreibtisch",
    "Feuerwehr", "Baustelle", "Wetterbericht",
]
TEMPLATES = [
    "Wir klären den {kw} bis Freitag.",
    "Wir brauchen eine Lösung für den {kw}.",
    "Bitte denkt morgen an den {kw}.",
    "Der {kw} ist heute wichtig.",
]


def sentences(n_total: int) -> list[tuple[str, str]]:
    if n_total > len(KEYWORDS):
        raise ValueError("at most %d sentences available (%d per speaker), "
                         "got %d" % (len(KEYWORDS), len(KEYWORDS) // 2, n_total))
    out = []
    for i in range(n_total):
        kw = KEYWORDS[i]
        out.append((TEMPLATES[i % len(TEMPLATES)].format(kw=kw), kw))
    return out


def render(espeak: str, ffmpeg: str, text: str, tmp: Path) -> bytes:
    raw, norm = tmp / "raw.wav", tmp / "norm.wav"
    subprocess.run([espeak, "-v", "de", "-s", "160", "-w", str(raw), text],
                   check=True)
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(raw),
                    "-ar", str(RATE), "-ac", "1", "-sample_fmt", "s16",
                    str(norm)], check=True)
    with wave.open(str(norm), "rb") as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1
        return w.readframes(w.getnframes())


def place(total_s: float, items: list[tuple[float, bytes]]) -> bytes:
    buf = bytearray(int(total_s * RATE) * 2)
    for start_s, pcm in items:
        off = int(start_s * RATE) * 2
        buf[off:off + len(pcm)] = pcm
    return bytes(buf)


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--espeak", default="espeak-ng")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--per-speaker", type=int, default=24,
                    help="sentences per speaker, max %d (24 gives ~60 s of "
                         "speech at ~2.5 s per sentence)" % (len(KEYWORDS) // 2))
    ap.add_argument("--min-speech-s", type=float, default=55.0,
                    help="fail if a speaker has less speech than this")
    ap.add_argument("--lead-in", type=float, default=15.0,
                    help="silence before the first slot (absorbs join jitter)")
    a = ap.parse_args(argv)

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    try:
        sents = sentences(2 * a.per_speaker)
    except ValueError as exc:
        ap.error(str(exc))
    total_s = a.lead_in + 2 * a.per_speaker * SLOT_S
    items: dict[str, list[tuple[float, bytes]]] = {"Alex": [], "Sam": []}
    events = []
    speech_s = {"Alex": 0.0, "Sam": 0.0}
    with tempfile.TemporaryDirectory() as td:
        for i, (text, kw) in enumerate(sents):
            who = "Alex" if i % 2 == 0 else "Sam"
            pcm = render(a.espeak, a.ffmpeg, text, Path(td))
            dur = len(pcm) / 2 / RATE
            if dur > SLOT_S - MIN_GAP_S:
                sys.stderr.write("sentence too long (%.1f s): %s\n" % (dur, text))
                return 1
            start = a.lead_in + i * SLOT_S
            items[who].append((start, pcm))
            speech_s[who] += dur
            events.append({"speaker": who, "start_s": start, "text": text,
                           "keywords": [kw]})
    for who, secs in speech_s.items():
        if secs < a.min_speech_s:
            sys.stderr.write("%s has only %.1f s of speech, need >= %.1f s "
                             "(raise --per-speaker)\n"
                             % (who, secs, a.min_speech_s))
            return 1
    for who in items:
        write_wav(out / (who.lower() + ".wav"), place(total_s, items[who]))
    (out / "script.json").write_text(json.dumps(
        {"slot_s": SLOT_S, "total_s": total_s, "events": events},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("total_s=%.1f alex_speech_s=%.1f sam_speech_s=%.1f events=%d" % (
        total_s, speech_s["Alex"], speech_s["Sam"], len(events)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
