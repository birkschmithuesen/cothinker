"""Die eingemessenen Schwellen ueber einen Neustart retten.

Die vier Werte hier unten werden **vor Ort am realen Raum und am realen
Mikrofon** eingestellt — das ist die Arbeit, die man nicht jeden Morgen
wiederholen will. Bis jetzt schrieb weder `/vad_threshold` noch `/mic_gate`
irgendwohin: nach einem Absturz fuhr der Server stillschweigend wieder die
Kommandozeilen-Vorgaben, also eine andere Einstellung als die eingemessene.

Bewusst NICHT die `.env`: die ist von Hand gepflegt und enthaelt Schluessel.
Ein Programm, das dort hineinschreibt, zerstoert Kommentare und riskiert,
beim Speichern ein Geheimnis zu verlieren. Stattdessen eine kleine eigene
JSON-Datei, deren Pfad ueber `--settings-file` konfigurierbar ist.

Ebenfalls bewusst nicht gespeichert: Geraetewahl, Sprache und Backend. Die
gehoeren zum Aufbau, nicht zur Einmessung, und stehen auf der Kommandozeile.

Dieses Modul haelt ausserdem die Validierung der vier Werte — dieselbe
Funktion, die `POST /vad_threshold` und `POST /mic_gate` benutzen (sr.py ruft
sie von hier). Eine von Hand editierte Einstellungsdatei ist keine
vertrauenswuerdigere Quelle als ein HTTP-Body.
"""
import json
import logging
import os
from threading import Lock

# Genau diese vier ueberleben den Neustart.
PERSISTED_KEYS = (
    "vad_energy_threshold",
    "mic_gate_threshold",
    "mic_gate_release_ratio",
    "mic_gate_debounce_ms",
)

# Neben dem Log, unterhalb des Repos. os.path.join, nicht "/": Windows.
DEFAULT_SETTINGS_FILE = os.path.join("settings", "stt_runtime.json")

# Die eingebauten Vorgaben — Stufe 3 der Rangfolge in resolve_runtime_settings().
# Sie stehen hier und nicht mehr als argparse-`default=`, weil argparse sonst
# nicht mehr sagen koennte, ob ein Wert getippt oder vorgegeben war.
BUILT_IN_DEFAULTS = {
    "vad_energy_threshold": 0.01,
    "mic_gate_threshold": 0.002,
    "mic_gate_release_ratio": 0.5,
    "mic_gate_debounce_ms": 1500,
}

# .env-Namen fuer dieselben Werte. Sie sind aelter als die Einstellungsdatei
# und bleiben gueltig, stehen in der Rangfolge aber UNTER ihr: die .env ist
# Aufbau-Konfiguration, die Datei ist die Messung von heute frueh.
ENV_NAMES = {
    "mic_gate_threshold": "STT_MIC_GATE_THRESHOLD",
    "mic_gate_release_ratio": "STT_MIC_GATE_RELEASE_RATIO",
    "mic_gate_debounce_ms": "STT_MIC_GATE_DEBOUNCE_MS",
}


# --- Validierung ------------------------------------------------------------

def validate_threshold(value, name):
    """0 < v <= 1, und nichts, was nur wie eine Zahl aussieht.

    Beide Schwellen liegen auf derselben 0..1-Skala wie `level_rms`, ausserhalb
    davon kann nichts gemeint sein. Null wird mit abgelehnt: ein RMS von exakt 0
    waere dann noch Sprache, der VAD faende also nie wieder eine Pause.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {value!r}")
    # NaN faellt durch beide Vergleiche unten und wird von ihnen abgelehnt.
    if not (0.0 < v <= 1.0):
        raise ValueError(
            f"{name} must be greater than 0 and at most 1.0, got {value!r}"
        )
    return v


def validate_release_ratio(value, name="mic_gate_release_ratio"):
    """Strikt zwischen 0 und 1.

    Genau 1.0 nimmt die Hysterese heraus und laesst das Gate an der Schwelle
    flattern; 0 laesst es nie wieder loesen.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    v = float(value)
    if not 0.0 < v < 1.0:
        raise ValueError(
            f"{name} must be greater than 0 and below 1.0, got {value!r}"
        )
    return v


def validate_debounce_ms(value, name="mic_gate_debounce_ms"):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    v = float(value)
    if not 0 <= v <= 60000:
        raise ValueError(f"{name} must be between 0 and 60000, got {value!r}")
    return int(v)


VALIDATORS = {
    "vad_energy_threshold": validate_threshold,
    "mic_gate_threshold": validate_threshold,
    "mic_gate_release_ratio": validate_release_ratio,
    "mic_gate_debounce_ms": validate_debounce_ms,
}


# --- Datei ------------------------------------------------------------------

class RuntimeSettings:
    """Die Einstellungsdatei. Faellt nie um.

    Ein Server, der wegen einer beschaedigten Einstellungsdatei nicht startet,
    waere am Ausstellungstag ein Totalausfall — deshalb wirft weder `load()`
    noch `update()` jemals. Beide loggen laut und machen weiter.
    """

    def __init__(self, path=DEFAULT_SETTINGS_FILE):
        self.path = str(path)
        # Flask bedient mehrere Anfragen gleichzeitig; zwei POSTs duerfen sich
        # nicht ins selbe Temp-File schreiben.
        self._lock = Lock()
        # Was nach unserem letzten Schreiben in der Datei stehen soll. Nur damit
        # ein Schreibvorgang fuer die VAD-Schwelle die Gate-Werte nicht wegwirft.
        self._values = {}

    # -- lesen --

    def load(self):
        """Gelesen und validiert. Gibt {} zurueck, wenn nichts Brauchbares da ist.

        Fehlende Datei ist der normale Erstlauf und wird nicht gemeldet.
        Kaputtes JSON macht die ganze Datei unbrauchbar — dann gilt gar nichts
        aus ihr. Ein einzelner Wert ausserhalb des Bereichs kostet dagegen nur
        sich selbst: der Rest der Datei ist ja lesbar und war einmal richtig
        gemessen.
        """
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            self._values = {}
            return {}
        except (OSError, ValueError) as e:
            logging.error(
                f"Einstellungsdatei {self.path} ist nicht lesbar ({e}). "
                f"Sie wird ignoriert, es gelten die Vorgaben."
            )
            self._values = {}
            return {}

        if not isinstance(raw, dict):
            logging.error(
                f"Einstellungsdatei {self.path} enthaelt {type(raw).__name__} "
                f"statt eines Objekts. Sie wird ignoriert, es gelten die Vorgaben."
            )
            self._values = {}
            return {}

        clean = {}
        for key in PERSISTED_KEYS:
            if key not in raw:
                continue
            try:
                clean[key] = VALIDATORS[key](raw[key], key)
            except ValueError as e:
                logging.error(
                    f"Einstellungsdatei {self.path}: {e}. Der Wert wird "
                    f"verworfen, fuer {key} gilt die Vorgabe."
                )
        unknown = sorted(set(raw) - set(PERSISTED_KEYS))
        if unknown:
            logging.warning(
                f"Einstellungsdatei {self.path} enthaelt unbekannte Schluessel "
                f"{unknown}; sie werden ignoriert."
            )
        self._values = dict(clean)
        return dict(clean)

    # -- schreiben --

    def update(self, **values):
        """Werte pruefen, mit dem bisherigen Stand mischen, atomar schreiben.

        Gibt `(True, None)` oder `(False, grund)` zurueck und wirft nie: eine
        nicht beschreibbare Platte darf die Vorstellung nicht anhalten. Der
        Aufrufer hat die Laufzeitaenderung zu diesem Zeitpunkt schon angewandt,
        sie bleibt in jedem Fall gueltig — nur ueberlebt sie dann den Neustart
        nicht, und genau das meldet das `persisted: false` in der HTTP-Antwort.
        """
        clean = {}
        for key, value in values.items():
            if key not in VALIDATORS:
                return False, f"unbekannte Einstellung {key!r}"
            try:
                clean[key] = VALIDATORS[key](value, key)
            except ValueError as e:
                return False, str(e)

        with self._lock:
            merged = dict(self._values)
            merged.update(clean)
            # Auch bei einem Fehlschlag uebernommen: der Wert GILT ja bereits im
            # laufenden Server. Kommt die Platte spaeter zurueck, schreibt der
            # naechste erfolgreiche Aufruf ihn mit, statt ihn zu verlieren.
            self._values = merged
            try:
                self._write(merged)
            except OSError as e:
                logging.error(
                    f"Einstellungen konnten nicht nach {self.path} geschrieben "
                    f"werden ({e}). Die Aenderung gilt fuer diesen Lauf, "
                    f"ueberlebt aber keinen Neustart."
                )
                return False, str(e)
        logging.info(f"Einstellungen gespeichert in {self.path}: {merged}")
        return True, None

    def _write(self, values):
        """Erst in eine Nebendatei, dann `os.replace`.

        Direkt in die Zieldatei geschrieben bliebe bei einem Stromausfall
        mitten im Schreiben eine halbe Datei zurueck — genau die Sorte Ausfall,
        gegen die dieses Feature schuetzen soll. `os.replace` ist auf Windows
        wie auf POSIX atomar und ueberschreibt ein vorhandenes Ziel.
        """
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(values, fh, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise


# --- Rangfolge --------------------------------------------------------------

def resolve_runtime_settings(args, stored, env=None):
    """Was gilt beim Start — und warum.

    Rangfolge, von oben nach unten:

    1. **Auf der Kommandozeile getippt.** Gewinnt immer. Wer
       `--vad_energy_threshold 0.005` tippt, meint das auch; eine gespeicherte
       Datei, die den getippten Befehl aushebelt, waere eine unsichtbare Falle.
    2. Der gespeicherte Wert aus der Einstellungsdatei.
    3. Die passende Variable aus der `.env`.
    4. Die eingebaute Vorgabe.

    Der Knackpunkt ist Stufe 1: argparse sagt von sich aus nicht, ob ein Wert
    getippt oder ein Default war. Darum tragen die vier Optionen in args.py
    `default=argparse.SUPPRESS` — dann FEHLT das Attribut, wenn nichts getippt
    wurde, und `hasattr` beantwortet die Frage eindeutig. (Das ist die schaerfere
    Fassung des ueblichen `default=None`: sie erkennt auch den Fall, dass jemand
    genau den Vorgabewert tippt.)

    Nach diesem Aufruf traegt `args` alle vier Werte, und zwar als geprueften
    Zahlenwert — nie `None`. Ein `None`, das bis in `VadChunker.energy_threshold`
    durchrutscht, macht den VAD unbrauchbar und faellt erst im Betrieb auf.

    Gibt `{key: (wert, quelle)}` zurueck, damit der Start protokollieren kann,
    woher jeder Wert kommt. Genau das haelt die Datei davon ab, unsichtbar zu
    werden.
    """
    env = {} if env is None else env
    resolved = {}
    for key in PERSISTED_KEYS:
        value, source = _resolve_one(args, key, stored, env)
        setattr(args, key, value)
        resolved[key] = (value, source)
    return resolved


def _resolve_one(args, key, stored, env):
    if hasattr(args, key):                      # 1. getippt
        return getattr(args, key), "Kommandozeile"

    if key in stored:                           # 2. gespeichert
        return stored[key], "Einstellungsdatei"

    env_name = ENV_NAMES.get(key)               # 3. .env
    raw = (env.get(env_name) or "").strip() if env_name else ""
    if raw:
        try:
            # float() zuerst: in der .env steht alles als Text, und die
            # Validierer fuer Gate-Entprellung und -Ratio lehnen Text ab.
            return VALIDATORS[key](float(raw), key), f".env ({env_name})"
        except ValueError as e:
            logging.error(
                f"{env_name} in der .env ist unbrauchbar ({e}); "
                f"fuer {key} gilt die Vorgabe."
            )

    return BUILT_IN_DEFAULTS[key], "Vorgabe"    # 4. eingebaut


def log_resolved_settings(resolved):
    """Eine Zeile, aus der hervorgeht, welcher Wert woher kommt."""
    logging.info(
        "Laufzeit-Einstellungen: "
        + ", ".join(f"{k}={v} ({source})" for k, (v, source) in resolved.items())
    )
