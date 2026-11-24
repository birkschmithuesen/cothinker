# Operator console (issue #173)

Page for the person running a session, against the operator contract (issue #159).
Open `index.html?api=<base>/operator/&token=<operator token>`. Everything is reachable by Tab/Enter; Esc closes the stop dialog.

    python -m surfaces.operator.check --surface surfaces/operator_console/index.html --out pr-shots/operator_console/conformance
    python -m pytest -q surfaces/operator_console/tests
