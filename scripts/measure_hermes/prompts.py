"""Fixed, invented prompts for M1-M3. No real interview material, no real
names -- P1..P4 are placeholders (AGENTS.md: a half-translated prompt is
worse than either, so the text itself stays German -- it is what the model
actually receives, same as a live table session)."""
from __future__ import annotations

SHORT = (
    "An Tisch 7 wurde gerade entschieden, dass die Szene im Jahr 2041 "
    "spielt. P1 sagt: 'Die Bühne ist leer, aber das Licht lebt.' "
    "Antworte in genau einem Satz: Was ist die wichtigste Beobachtung "
    "aus diesem Moment für das Protokoll?"
)

MEDIUM = (
    "Vier Personen haben soeben das Interview an ihrem Tisch beendet. "
    "Hier ihre Stichpunkte, so wie sie live mitgeschrieben wurden:\n"
    "P1: arbeitet seit zehn Jahren im Bühnenbild, vermisst den Geruch von "
    "frischer Farbe und das Kratzen der Säge im Fundus, fürchtet, dass "
    "ein Modell das Bühnenbild bald selbst entwirft und niemand mehr "
    "merkt, woher die Idee kam, erinnert sich an eine Produktion, bei "
    "der ein Fehler im Bauplan am Ende die schönste Lösung war.\n"
    "P2: ist Souffleur, sagt, die Probe sei der einzige Ort, an dem noch "
    "niemand ihm ins Wort fällt, wünscht sich einen Assistenten, der "
    "Einsätze flüstert, wenn die Stimme versagt, beschreibt eine Nacht, "
    "in der ein Ensemblemitglied den Text komplett vergaß und die ganze "
    "Gruppe improvisierend weitermachte, ohne dass das Publikum etwas "
    "merkte.\n"
    "P3: leitet die Technik, erzählt von einer Panne 1999, bei der das "
    "Licht mitten in der Premiere ausfiel und die Vorstellung im "
    "Dunkeln weiterlief, glaubt, Automatisierung mache die Technik "
    "unsichtbarer, nicht überflüssig, sorgt sich, dass niemand mehr "
    "lernt, wie man die Anlage von Hand repariert, wenn sie ausfällt.\n"
    "P4: schreibt Stücke, beschreibt einen Montagmorgen, an dem der "
    "Text noch nicht fertig ist und die Gruppe trotzdem improvisiert, "
    "hofft, dass die Maschine Lücken offen lässt statt sie zuzuschütten, "
    "erzählt von einer Szene, die erst beim Lesen am Tisch entstand und "
    "nie aufgeschrieben worden wäre, hätte jemand vorher durchgeplant.\n"
    "Schreibe daraus genau zwei Beobachtungen zum Rahmen (Traum oder "
    "Widerstand), je ein Satz, ohne Namen zu nennen."
)

TOOL = MEDIUM + (
    "\nSchreibe diese zwei Beobachtungen zusätzlich in die Datei obs.md "
    "in deinem Arbeitsordner (ein write_file-Aufruf, keine weiteren "
    "Werkzeuge)."
)

DELEGATE = (
    "Rufe delegate_task mit zwei Aufgaben auf (je: schreibe einen "
    "Absatz von 120 Wörtern über ein erfundenes Bühnenbild der Zukunft "
    "in die Datei draft-1.md bzw. draft-2.md). Antworte ausschließlich "
    "mit 'gestartet'."
)
