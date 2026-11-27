# Room display (issue #173)

Generic 16:9 room surface against the surface contract (ADR 0012, ADR 0019).
Open `index.html?api=<contract base>/`. Keys: `M` menu, `1`-`6` choose a view, `Esc` close,
`P` toggle the live partial (provisional) transcript line, off by default.

    python -m surfaces.contract.check --surface surfaces/room_display/index.html --out pr-shots/room_display/conformance
    python -m pytest -q surfaces/room_display/tests
