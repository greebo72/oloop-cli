# HANDOVER — oloop (früher „Lagebericht")

**Stand:** 2026-10-16, v0.1.0 getaggt und gepusht (`greebo72/oloop`, `origin/main`). SPEC-Entwurf
weiterhin formal nicht freigegeben, aber AP1, AP2'' und AP4'' sind umgesetzt und getestet
(18 Tests). Das CLI ist per `uv tool install` global installiert und für Claude Code
eingerichtet (siehe „Einrichtung"). Noch kein einziges echtes Projekt im Daten-Root angelegt.

## Leseordnung

1. `SPEC/SPEC.md` — fang bei **„Verworfene Vorstufen"** an: vier Architektur-Pivots, kurz
   zusammengefasst. Danach Zweck, Nicht-Ziele, ADR „Dateibasierte Persistenz, kein Service",
   Abschnitt „Dateiformat".
2. `Implementation/oloop/schemas/{project,task}.schema.json` — maßgeblich für die
   Frontmatter-Struktur (liegen im Paket, damit die installierte Wheel validieren kann).
3. `SPEC/api-contract.yaml` — **überholt** (HTTP/H2-Architektur). Nur noch historischer Beleg.
4. `PLAN.md` — AP1, AP2'', AP4'' erledigt. Offen: **AP4d (MCP-Wrapper, optional, zurückgestellt)**
   und **AP6 (Phase 2 Desktop-App, eigene Freigabe)**. Durchgestrichene APs sind verworfen.
5. `Implementation/` — `oloop/store.py` (Store), `oloop/cli.py` (CLI), `tests/`.

## Implementierungsstand

- **Store** (`store.py`): Projekte, Tasks, Kommentare, Hauptbild (`set_image`), zusätzliche Bilder
  (`add_attachment`), Edit (Titel/Beschreibung/Labels), close/reopen, Filter (`project`, `type`,
  `status`, `label`, `stale_days`), ID-Präfix-Auflösung (`resolve_task_id`, min. 4 Zeichen).
  Atomares Schreiben, Backup `<projekt>/.backup/<datei>.bak` (eine Generation), Validierung gegen
  die Schemas beim Lesen und Schreiben. Kein Delete.
- **Task-Felder** zusätzlich zur ursprünglichen Spec: `createdBy`, `updatedBy` (Pflicht, Quelle:
  Parameter, sonst `$OLOOP_ACTOR`, sonst OS-Benutzer) und `labels` (Liste eindeutiger Strings,
  Pflicht, leer erlaubt).
- **CLI**: `project add/list`, `task add/list/show/edit/close/reopen/image`, `comment add`.
  `--json` auf `project list`, `task list`, `task show`. `task image <id> <datei>` setzt das
  Hauptbild, `--embed` legt ein Zusatzbild als `images/<task-id>-<n>.<ext>` ab und gibt den
  Markdown-Link (`../images/...`, relativ zur Task-Datei) für die Description aus.
- **Release**: `uv build` in `Implementation/` erzeugt `dist/` (nicht im Git). Geprüft: Wheel in
  frischer venv außerhalb des Workspace installiert, CLI läuft.

## Einrichtung (diese Maschine)

- Installiert über `uv tool install <wheel>`, Befehl unter `~/.local/bin/oloop`. Nach
  Code-Änderungen neu bauen und `uv tool install --force <wheel>`.
- **Daten-Root:** `/Users/user/Google Drive/Meine Ablage/30_User/99_MD_NOTES` (Google-Drive-Ordner).
- `~/.zshrc`: `OLOOP_ROOT`. `~/.claude/settings.json` (Backup: `settings.json.bak`):
  `env.OLOOP_ROOT`, `env.OLOOP_ACTOR=claude`, `permissions.allow` `Bash(oloop *)`.
- `~/.claude/CLAUDE.md` (neu angelegt): Anleitung, wann/wie Claude `oloop` nutzt.
- Claude Code liest `env` und `CLAUDE.md` nur beim Start. Nach dem Neustart zuerst
  `oloop project add <name>` und einen Test-Task anlegen, um den Ablauf mit echtem Root zu
  prüfen (wurde bisher nur gegen Temp-Verzeichnisse getestet).

## Entscheidungen dieser Session

- **Umbenennung Lagebericht → oloop** (Kurzform von „open loops"; PyPI, Homebrew und GitHub frei
  geprüft). Paket, Befehl und Env-Variablen heißen `oloop` / `OLOOP_ROOT` / `OLOOP_ACTOR`; die alten
  `LAGEBERICHT_*` gelten als Fallback. Der lokale Repo-Ordner heißt weiter `lagebericht`; GitHub-Repo ist von `md_todo` auf `oloop` umbenannt.
  Das Tracker-Projekt `lagebericht` im Daten-Root behält seinen Namen (Zuordnung läuft über
  `referencePath`).
- Projekt-Frontmatter: optionale Felder `referencePath`/`referenceHost`; `oloop project find`
  ordnet ein Verzeichnis einem Projekt zu (längster Pfad, passender Host).

- MCP-Wrapper vorerst nicht nötig: Claude Code ruft das CLI direkt per Bash auf (`--json`). MCP nur
  sinnvoll, falls Claude Desktop o. Ä. ohne Shell angebunden werden soll.
- Mehrere Bilder pro Task über Links in der Description statt `images: [..]` im Schema. Ob das
  Einbetten vereinfacht wird (z. B. `--embed` hängt den Link selbst an die Description an), entscheidet
  der User, nachdem er die Desktop-App benutzt hat.
- Schemas liegen im Paket statt in `SPEC/`; Doku-Verweise sind angepasst.
- Ordnername eines Projekts = Slug des Namens (nicht die UUID). Das Schema beschrieb die `id` früher
  als „identisch mit dem Ordnernamen", die Beschreibung im Schema ist noch ungenau.
- Backup liegt pro Projekt (`<projekt>/.backup/`), weil `project.md` in jedem Projekt gleich heißt.

## Fallen

- **Google Drive als Daten-Root:** Die SPEC schließt Mehrgeräte-Betrieb aus (kein Lock, keine
  Konfliktkopie-Erkennung). Auf einem Rechner meist unkritisch, aber der Sync kann bei vielen kleinen
  Schreibvorgängen kurz alte Versionen oder Konfliktkopien erzeugen. Nicht von zwei Rechnern
  gleichzeitig nutzen.
- **Kein Lock bei gleichzeitigem Schreibzugriff** — bewusste Lücke (SPEC-ADR). Mitigation: atomares
  Schreiben, nicht Verhinderung des Race.
- **Bestehende Task-Dateien ohne `createdBy`/`updatedBy`/`labels` sind ungültig** (Pflichtfelder).
  Gab zum Zeitpunkt der Änderung keine echten Daten. Falls doch welche existieren: Migration nötig
  oder Felder optional machen.
- **`task edit -d` ersetzt die komplette Description** — eingebettete Bild-Links gehen dabei verloren,
  wenn sie nicht im neuen Text stehen.
- Ein zweites Hauptbild mit anderer Endung überschreibt das erste nicht, die alte Datei bleibt
  verwaist liegen (kein Delete).
- Vorläufer-Prototyp als Cloud-Artifact: `claude.ai/code/artifact/ad6da2b6-5b14-488b-ae53-
  9e59e11f8467` („Lagebericht") — Vorlage für den Look der Phase-2-Desktop-App, nicht Datenquelle.
- Historisch: H2-Tools aus PowerShell nicht mit `-password ""` aufrufen (leere Argumente werden
  verschluckt). Kein H2 mehr im Projekt.

## Nächste Schritte

1. Nach Neustart: erstes Projekt und Task gegen den echten Daten-Root anlegen, prüfen, dass Claude
   das Tool anhand der `CLAUDE.md` selbstständig nutzt.
2. Etwas gelebte Nutzung über das CLI, dann entscheiden: AP4d (MCP) überhaupt nötig?
3. AP6 (Desktop-App): eigenes Spec-Kapitel, Toolkit-Wahl (Tauri/Wails/native statt Electron),
   „seit wann still"-Ansicht aus `updatedAt`. Erst nach Nutzung und eigener Freigabe.
4. Offen/klein: Schema-Beschreibung von `project.id` korrigieren, SPEC-Status auf „freigegeben"
   setzen, falls gewünscht.
