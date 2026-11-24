"""Design tokens (ADR 0023, issue #172 / #27): one token file,
`tokens.yaml`, a generator (`python -m surfaces.design.build`) turning it
into `tokens.css` and `tokens.json`. A surface reads a token through the
generated custom property or JSON value; it never writes a colour, font
name or size literal of its own. An instance overrides tokens via its own
`instances/<name>/look/tokens.yaml`, never by editing surface code.
"""
