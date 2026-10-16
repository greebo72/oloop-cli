# oloop

oloop sammelt die offenen Punkte aus allen deinen Projekten an einem Ort: Todos, Fragen, Entscheidungen, Features und Bugs. So siehst du, was wo noch offen ist und was seit Wochen still liegt.

Die Daten liegen als Markdown-Dateien mit YAML-Frontmatter in einem normalen Ordner. Bedient wird oloop über ein CLI, das du und Claude Code nutzen. Es gibt keinen Server und keine Datenbank. Von Hand bearbeitet werden die Dateien nicht.

## Installation

Voraussetzung ist Python ab 3.11 und [uv](https://docs.astral.sh/uv/).

```
cd Implementation
uv build
uv tool install dist/*.whl
export OLOOP_ROOT=~/oloop-daten
```

## Benutzung

```
oloop project add meinprojekt --reference-path ~/dev/meinprojekt
oloop task add -p meinprojekt -t todo "Backup prüfen" -l infra
oloop task list -s open --stale-days 14
oloop task close <id>
oloop project find          # Projekt zum aktuellen Verzeichnis
```

Task-Typen sind `todo`, `question`, `decision`, `feature` und `bug`. Kommentare, Labels und Bilder hängen direkt am Task. Mit `--json` liefern `list` und `show` maschinenlesbare Ausgabe. Weitere Befehle zeigt `oloop --help`.

## Grenzen

Gedacht ist oloop für eine Person auf einem Rechner, ohne Locking und ohne Sync-Schutz. Tasks werden geschlossen, nie gelöscht. Eine Desktop-App ist für später geplant.

Details stehen in [SPEC/SPEC.md](SPEC/SPEC.md), der aktuelle Stand in [HANDOVER.md](HANDOVER.md).
