## premises
Das Setting eures Tisches steht. Entwerfe fuenf Praemissen fuer den Prolog, nach der
Methode in `{skill_path}`, mit der Eingabe `{input_path}`. Schreibe sie nach
`{premises_path}`. Antworte danach nur: geschrieben

## dispatch
Schreibe jetzt den Prolog. Die Methode steht in `{skill_path}`, die Eingabe in
`{input_path}`. Gib {drafts} Entwuerfe in Auftrag.{conditions} Antworte danach nur:
gestartet

## pick
Die Entwuerfe liegen vor: {draft_paths}. Waehle nach der Methode in `{skill_path}` und
schreibe das Ergebnis nach `{scene_path}`. Antworte danach nur: fertig

## fallback
Es liegen keine Entwuerfe vor. Arbeite nach der Methode in `{skill_path}`, mit der
Eingabe `{input_path}`, und schreibe nach `{scene_path}`. Antworte danach nur: fertig

## repair
Der Prolog in `{scene_path}` verstoesst gegen diese Regeln: {reasons}. Arbeite nach der
Methode in `{skill_path}`. Antworte danach nur: fertig

## revise
Diese Aenderungen werden an `{scene_path}` empfohlen: {recommendations}. Arbeite nach
der Methode in `{skill_path}` und lass das Ergebnis nach `{revise_path}` schreiben.
Antworte danach nur: gestartet

## reasons
placeholder: Es steht ein Platzhalter wie P1 im Text. Ersetze ihn durch den Kurznamen
der Rolle.
name: Es steht ein echter Name im Text. Ersetze ihn durch den Vornamen der Rolle.
length: Die Erzaehlung hat nicht 6 bis 22 Saetze und 150 bis 300 Woerter. Ist sie zu
lang, kuerze auf hoechstens 240 Woerter; ist sie zu kurz, erweitere auf mindestens
150 Woerter.
lines: Es sind nicht 4 bis 20 Dialogzeilen von mindestens zwei Rollen, jede hoechstens
40 Woerter. Sind es zu viele, kuerze auf hoechstens 16 Zeilen; ist eine Zeile zu lang,
kuerze sie auf hoechstens 30 Woerter; sind es zu wenige, ergaenze Zeilen, bis es
mindestens 8 sind.
dialogue_words: Die Dialogzeilen zusammen haben mehr als 200 Woerter. Kuerze einzelne
Zeilen oder lass eine weg, bis die Summe aller Dialogzeilen hoechstens 200 Woerter ist.
speaker: Eine Zeile beginnt nicht mit der Sprecherkennung einer Rolle aus der Eingabe
("Vorname · Kurzname" oder der Kurzname).
ending: Die letzte Zeile ist nicht "...".
extra: Es steht etwas ausserhalb der Form im Text (Titel, Ueberschrift, Klammer,
Erklaerung).
language: Der Text ist nicht in der Sprache des Tisches geschrieben.
links: Es fehlt eine `link:`-Zeile mit einer Stelle, die wirklich im Material steht.
setting: Ort oder Anfang aus dem Setting der Gruppe fehlen in der Erzaehlung.
verbatim: Die Zeichen » « passen nicht zu `quote_mode`.
quote: Die `quote:`-Zeile ist kein Satz aus dem Text oder laenger als 160 Zeichen.
method: Im Text steht etwas aus deinen Anweisungen. Entferne es.
story_copy: Der Text uebernimmt einen Satzteil woertlich aus der Selbstvorstellung
einer Rolle. Die Gruppe hat das gerade erst gehoert -- formuliere um, erzaehle die
Vorstellung nicht nach.

## words
someone: jemand
condition: Auflage für Entwurf {n}
