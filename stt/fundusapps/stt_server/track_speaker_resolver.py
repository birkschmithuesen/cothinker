"""Issue #128: the live cross-process bridge between a bbb_tap --per-track
run and the STT server's `speaker_resolver` hook
(`fundusapps.stt_server.backends.speechmatics_rt_backend`).

bbb_tap and the STT server are separate processes. bbb_tap writes its
live state to disk -- one growing WAV per remote audio track plus a
`-tracks.json` sidecar (track number -> owner timeline), both already
implemented in `fundusapps.bbb_tap.__main__.PerTrack` -- and that shared
filesystem (same `--record-dir`) is this module's only input. There is no
socket, queue or shared memory between the two processes for this
feature; `TrackSpeakerResolver` just re-reads the sidecar and tails the
WAV files, feeding newly-appeared PCM into
`fundusapps.bbb_tap.speaker_attribution.TrackEnergy`.

Clock note: a word's `t_start`/`t_end` here are on the STT recognizer's
own `AudioClock` (`fundusapps.stt_server.audio_clock`), i.e. seconds
since that recognizer's own stream started -- the same frame axis
`TrackEnergy` expects (both start counting at the first PCM byte of the
one live mixed stream bbb_tap's tap sends, see `PerTrack`'s own
docstring), so no conversion is needed to rank tracks by energy. An
owner's `since`, however, is wall-clock (`PerTrack.update_owners`), and
converting a word's audio-clock `t_start` into the SAME wall-clock exactly
needs that recognizer's `AudioClock.epoch` -- not reachable from here (it
is created only once `STTServer` builds its recognizers, well after the
backend, and therefore this resolver, already exist; see
`fundusapps.stt_server.sr.STTServer`/`__main__.py`). Rather than guess at
that wiring, the owner lookup below uses the current wall-clock instead
of a converted `t_start`: owner identity changes are coarse-grained
(a participant joins/renames/leaves), seconds apart at the least, while
the gap between a word being spoken and this resolver being asked about
it is the STT pipeline's own small latency (low single-digit seconds) --
close enough for "whoever currently owns the loudest track" not to flip
mid-utterance in practice.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from fundusapps.bbb_tap.speaker_attribution import TrackEnergy, resolve_speaker

#: Every WAV `fundusapps.bbb_tap.pcm.WavRecorder` writes has this fixed
#: 16-bit-mono-PCM header size; body bytes start right after it.
WAV_HEADER_BYTES = 44


class TrackSpeakerResolver:
    """A `speaker_resolver(t_start, t_end)` callable, built from one
    bbb_tap --per-track run's sidecar path. Re-reads the sidecar and tails
    each track's WAV at most every `reload_every_s` seconds; between
    reloads it answers from whatever it already has.
    """

    def __init__(self, sidecar_path, reload_every_s: float = 2.0,
                 now: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic):
        self._sidecar_path = Path(sidecar_path)
        self._base_dir = self._sidecar_path.parent
        self._reload_every_s = float(reload_every_s)
        self._now = now
        self._monotonic = monotonic
        self._tracks: Dict[int, TrackEnergy] = {}
        self._owners_by_track: Dict[int, List[dict]] = {}
        self._track_files: Dict[int, str] = {}
        self._read_pos: Dict[int, int] = {}
        self._last_refresh: Optional[float] = None

    def _refresh_sidecar(self) -> None:
        try:
            doc = json.loads(self._sidecar_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        tracks = doc.get("tracks")
        if not isinstance(tracks, dict):
            return
        self._owners_by_track = {}
        self._track_files = {}
        for key, info in tracks.items():
            if not isinstance(info, dict):
                continue
            try:
                g = int(key)
            except ValueError:
                continue
            self._owners_by_track[g] = info.get("owners") or []
            filename = info.get("file")
            if filename:
                self._track_files[g] = filename

    def _pump_tracks(self) -> None:
        for g, filename in self._track_files.items():
            path = self._base_dir / filename
            try:
                size = path.stat().st_size
            except OSError:
                continue
            pos = self._read_pos.get(g, WAV_HEADER_BYTES)
            if size <= pos:
                continue
            try:
                with path.open("rb") as f:
                    f.seek(pos)
                    chunk = f.read(size - pos)
            except OSError:
                continue
            if not chunk:
                continue
            start_frame = (pos - WAV_HEADER_BYTES) // 2
            self._tracks.setdefault(g, TrackEnergy()).add_chunk(start_frame, chunk)
            self._read_pos[g] = pos + len(chunk)

    def _maybe_refresh(self) -> None:
        now = self._monotonic()
        if self._last_refresh is not None and now - self._last_refresh < self._reload_every_s:
            return
        self._last_refresh = now
        self._refresh_sidecar()
        self._pump_tracks()

    def __call__(self, t_start: float, t_end: float) -> Optional[str]:
        self._maybe_refresh()
        best_track, best_energy = None, 0.0
        for track_n, energy in self._tracks.items():
            value = energy.energy_between(t_start, t_end)
            if value > best_energy:
                best_energy, best_track = value, track_n
        if best_track is None:
            return None
        return resolve_speaker(self._owners_by_track.get(best_track, []), self._now())


def build_speaker_resolver(args) -> Optional[TrackSpeakerResolver]:
    """`None` unless `args.per_track_sidecar` is set -- the Jitsi/BBB
    `--per-track` path's own sidecar file. Every other input path (mic,
    feed_wav, other backends) never sets that attribute, so this stays a
    no-op for them, per `speaker_resolver`'s own contract."""
    sidecar = getattr(args, "per_track_sidecar", "")
    if not sidecar:
        return None
    return TrackSpeakerResolver(sidecar)
