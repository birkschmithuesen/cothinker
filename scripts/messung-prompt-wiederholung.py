#!/usr/bin/env python3
"""Was kostet die Prompt-Wiederholung -- gemessen an einer echten Sitzung.

Deterministisch, kein API-Aufruf, kein Modell: die 233 User-Nachrichten
der Sitzung liegen in `state.db`. Wir rechnen nach, wie gross der Kontext
bei jedem der Aufrufe WAR, und wie gross er waere, wenn der statische
Prompt-Anteil nur einmal gesendet wuerde.

Der Kernpunkt ist die KUMULATIVE Summe: jeder Aufruf liest den ganzen
bisherigen Verlauf mit. Eine Nachricht, die in Turn 10 zu viel ist, wird
von allen 223 folgenden Aufrufen erneut gelesen und bezahlt. Darum waechst
der Schaden quadratisch, nicht linear -- und darum reicht es nicht, die
Groesse einer einzelnen Nachricht zu betrachten.

Aufruf:
    python3 messung-prompt-wiederholung.py <session_id>
"""
from __future__ import annotations

import difflib
import getpass
import os
import sqlite3
import statistics
import sys

DB = os.path.expanduser(f"~/.hermes/profiles/{getpass.getuser()}/state.db")

# Opus-Preise, USD je Million Token.
PREIS_CACHE_READ = 1.50


def zeichen_zu_token(z: int) -> float:
    """Grobe Umrechnung. 4 Zeichen je Token ist die uebliche Faustregel
    fuer deutschen Text; die Zahl taugt fuer den VERGLEICH zweier
    Varianten, nicht als absolute Abrechnung."""
    return z / 4


def gemeinsamer_anteil(a: str, b: str) -> int:
    """Wie viele Zeichen teilen sich zwei Nachrichten?

    Nicht `==` auf Teilstrings: der Prompt wird mit wechselnden Werten
    formatiert (Pfade, Zustand, Redeblock), die Kopien sind also aehnlich,
    nicht identisch. SequenceMatcher findet die gemeinsamen Bloecke.
    """
    sm = difflib.SequenceMatcher(None, a, b)
    return sum(block.size for block in sm.get_matching_blocks())


def lies(session: str) -> list[str]:
    if not os.path.exists(DB):
        sys.exit(f"state.db nicht gefunden: {DB}")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = [r[0] for r in con.execute(
        "select content from messages "
        "where session_id=? and role='user' order by id", (session,))]
    con.close()
    if not rows:
        sys.exit(f"keine User-Nachrichten fuer {session}")
    return rows


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: messung-prompt-wiederholung.py <session_id>")
    session = sys.argv[1]
    nachrichten = lies(session)
    n = len(nachrichten)
    laengen = [len(m) for m in nachrichten]

    print(f"Sitzung: {session}")
    print(f"User-Nachrichten: {n}")
    print(f"  min {min(laengen):,}  median {statistics.median(laengen):,.0f} "
          f"  max {max(laengen):,}  Summe {sum(laengen):,} Zeichen")
    print()

    # Wie viel teilen sich zwei Nachrichten aus der Mitte der Sitzung?
    # Bewusst nicht die erste und letzte: am Anfang ist der Zustand leer,
    # am Ende steckt die Kompression drin -- beide sind untypisch.
    i, j = n // 4, (n * 3) // 4
    geteilt = gemeinsamer_anteil(nachrichten[i], nachrichten[j])
    neu_je_turn = max(len(nachrichten[i]), len(nachrichten[j])) - geteilt

    print(f"Vergleich Nachricht {i} gegen {j}:")
    print(f"  identisch:  {geteilt:,} Zeichen "
          f"({geteilt / max(len(nachrichten[i]), len(nachrichten[j])) * 100:.0f} %)")
    print(f"  wirklich neu je Turn: ~{neu_je_turn:,} Zeichen")
    print()

    # --- kumulativ: jeder Aufruf liest alles Fruehere mit ---------------
    # IST: Nachricht k steht in voller Laenge im Verlauf.
    ist = 0
    laufend = 0
    for laenge in laengen:
        laufend += laenge
        ist += laufend        # dieser Aufruf liest den Verlauf bis hier

    # WAERE: der statische Anteil steht einmal, danach nur das Neue.
    # Konservativ gerechnet: der statische Anteil ist der GETEILTE Anteil
    # zweier mittlerer Nachrichten, nicht der ganze Prompt -- was sich
    # zwischen zwei Turns aendert, muss weiterhin mitgesendet werden.
    waere = 0
    laufend = geteilt          # einmal am Anfang
    for k, laenge in enumerate(laengen):
        laufend += (laenge if k == 0 else min(neu_je_turn, laenge))
        waere += laufend

    print("=== kumulativ gelesener Kontext (die Groesse, die zaehlt) ===")
    print(f"  heute:      {zeichen_zu_token(ist):15,.0f} Token")
    print(f"  moeglich:   {zeichen_zu_token(waere):15,.0f} Token")
    print(f"  Ersparnis:  {(1 - waere / ist) * 100:14.0f} %")
    print()
    k_ist = zeichen_zu_token(ist) / 1e6 * PREIS_CACHE_READ
    k_neu = zeichen_zu_token(waere) / 1e6 * PREIS_CACHE_READ
    print(f"  bei Opus-Cache-Read (${PREIS_CACHE_READ:.2f}/Mio):")
    print(f"    heute ${k_ist:,.2f}   moeglich ${k_neu:,.2f}   "
          f"gespart ${k_ist - k_neu:,.2f}")
    print()

    # --- Gegenprobe an der Abrechnung ----------------------------------
    # Die Rechnung oben schaetzt aus Zeichen. Die Abrechnung in
    # session_model_usage ist die Wahrheit -- wenn beide grob
    # uebereinstimmen, traegt die Schaetzung.
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute(
        "select sum(cache_read_tokens), sum(api_call_count) "
        "from session_model_usage where session_id=? and task=''",
        (session,)).fetchone()
    con.close()
    if row and row[0]:
        echt, calls = row
        print("=== Gegenprobe an der Abrechnung ===")
        print(f"  cache_read_tokens (state.db): {echt:,} bei {calls} Calls")
        print(f"  Schaetzung oben:              {zeichen_zu_token(ist):,.0f}")
        faktor = echt / zeichen_zu_token(ist)
        print(f"  Faktor {faktor:.2f} -- ", end="")
        if 0.5 <= faktor <= 2.0:
            print("gleiche Groessenordnung, die Schaetzung traegt.")
        else:
            print("WEICHT AB. Die Schaetzung taugt nur als Richtung.")
        print()
        print("  Hinweis: der Abrechnungswert enthaelt auch System-Prompt,")
        print("  Werkzeug-Definitionen und Assistenz-Antworten -- er liegt")
        print("  naturgemaess ueber der reinen User-Nachrichten-Rechnung.")


if __name__ == "__main__":
    main()
