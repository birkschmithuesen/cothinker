"""Per-track energy speaker attribution for the Jitsi/BBB tap.

In a Jitsi/BBB meeting every participant already arrives on their own
audio track (`PerTrack` in `__main__.py`); speech-to-text, though, runs
once on the MIXED stream (one Speechmatics session, not one per
person -- see docs/stt.md). This module bridges the two: for the time
span of one recognised word or utterance, it decides which participant's
track actually carried the energy, and resolves that track to the
participant's current display name.

All spans here are in SECONDS ON THE SAME AUDIO CLOCK that produced them
(`PerTrack`'s per-track WAVs share the mixed WAV's frame axis, padded to
match at the moment each track appears -- see `PerTrack.write()`). An
owner's `since` is wall-clock time (`time.time()`, as `PerTrack` stores
it); a caller that also has a recognised word's `t_start` on the audio
clock must convert one side before calling `resolve_speaker` -- e.g. via
`TranscriptionEvent.audio_epoch` (`audio_epoch + t_start` is the
word's own wall-clock instant). That conversion is the live-wiring
caller's job, not this module's: here, everything passed in is already
on one consistent clock.
"""
import numpy as np

RATE = 16000


class TrackEnergy:
    """RMS energy of one track's audio, as fixed chunks on a shared frame
    axis (frame 0 = the shared recording's own start)."""

    def __init__(self):
        self._windows = []  # list of (start_frame, end_frame, rms)

    def add_chunk(self, start_frame, pcm):
        """Record one chunk of raw 16-bit mono PCM starting at
        `start_frame` on the shared frame axis."""
        n = len(pcm) // 2
        if n == 0:
            return
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
        rms = float(np.sqrt(np.mean(samples * samples)) / 32768.0)
        self._windows.append((start_frame, start_frame + n, rms))

    def energy_between(self, start_s, end_s):
        """Energy-time integral (rms * overlapping frame count) over every
        recorded chunk that overlaps [start_s, end_s) seconds -- enough to
        RANK tracks against each other for the same span, not an absolute
        loudness measure."""
        f0, f1 = start_s * RATE, end_s * RATE
        total = 0.0
        for start_frame, end_frame, rms in self._windows:
            overlap = min(end_frame, f1) - max(start_frame, f0)
            if overlap > 0:
                total += rms * overlap
        return total


def resolve_speaker(owners, at):
    """The display name active `at` a given time, from an owner timeline
    `[{"display_name", "since", ...}, ...]` sorted by `since` (the shape
    `PerTrack`'s sidecar stores per track). The latest entry whose `since`
    is not after `at`; the earliest entry if `at` precedes all of them
    (a track's first owner is who was speaking before any later change
    was even recorded); `None` for an empty timeline."""
    if not owners:
        return None
    best = owners[0]
    for owner in owners:
        if owner["since"] <= at:
            best = owner
        else:
            break
    return best.get("display_name") or None


def attribute_word(tracks, owners_by_track, t_start, t_end):
    """The display name of whichever track carried the most energy in
    [t_start, t_end) -- "every recognised word goes to the participant
    whose track carries the energy at that moment" (issue #128). `tracks`
    maps track number -> TrackEnergy; `owners_by_track` maps the same
    track numbers -> an owner timeline for `resolve_speaker`. `None` when
    no track has any energy in the span (e.g. a recognition artifact with
    no real speech, or data gaps)."""
    best_track, best_energy = None, 0.0
    for track_n, energy in tracks.items():
        value = energy.energy_between(t_start, t_end)
        if value > best_energy:
            best_energy, best_track = value, track_n
    if best_track is None:
        return None
    return resolve_speaker(owners_by_track.get(best_track, []), t_start)
