# HANDOVER — Lagebericht

**Stand:** 2026-10-16, SPEC-Entwurf steht (vierter Architektur-Pivot: dateibasierte Persistenz,
kein Service, reines Python), noch nicht freigegeben. Keine Implementierung.

## Leseordnung

1. `SPEC/SPEC.md` — fang bei **„Verworfene Vorstufen"** an (direkt nach den Revisionszeilen): vier
   echte Architektur-Pivots in einer Session, kurz zusammengefasst, damit du nicht die ganze
   Historie rekonstruieren musst. Danach: Zweck, Nicht-Ziele, **ADR „Dateibasierte Persistenz, kein
   Service"** (aktuellster Stand), Abschnitt „Dateiformat" (Layout, Frontmatter, ID-Schema).
2. `SPEC/project.schema.json`, `SPEC/task.schema.json` — maßgeblich für die Frontmatter-Struktur.
3. `SPEC/api-contract.yaml` — **überholt**, Kopfzeile sagt das auch. Service/H2/JDBC-Architektur,
   komplett verworfen. Nur noch historischer Beleg (z. B. falls Filterparameter-Namen wie
   `staleDays`, `projectName` als Referenz für die CLI-Flags gebraucht werden — fachlich identisch
   geblieben, nur nicht mehr über HTTP).
4. `PLAN.md` — Arbeitspakete, aktuell relevant: **AP1 (Setup, Struktur/git erledigt), AP2''
   (Dateiformat-Modul), AP4'' (CLI-Befehle), AP4d (MCP-Wrapper, optional), AP6 (Phase 2 Desktop-App,
   eigene Freigabe)**. Alles mit Strich-Präfix (AP2–AP5, AP3', AP3b, AP4', AP4c) ist verworfen,
   durchgestrichen stehen gelassen, nicht der Stand.
5. `Implementation/` — noch leer.

## Fallen

- **Nicht mit `-password ""` gegen H2-Tools aufrufen aus PowerShell `&`** — leere
  String-Argumente werden beim nativen Aufruf stillschweigend verschluckt. Nur noch historisch
  relevant (kein H2 mehr im Projekt), aber sachlich weiterhin wahr, falls H2 je wieder auftaucht.
- ~~OneDrive-Konfliktkopie-Muster unverifiziert~~ / ~~Service-Installation braucht UAC~~ /
  ~~DB-Pfad über `application.yaml`~~ — **alles gegenstandslos.** Kein Service, kein H2, kein
  OneDrive-Zwang mehr (vierter Pivot). Siehe SPEC.md „Verworfene Vorstufen" für den Kontext.
- **Daten-Root-Pfad ist konfigurierbar, aber noch nicht gesetzt.** SPEC legt nur fest: Wert kommt
  aus CLI-Config/ENV, kein Hardcoding. Vor dem ersten Lauf (AP2''/AP4'') muss der User einen
  konkreten Pfad festlegen.
- **Kein Lock bei gleichzeitigem Schreibzugriff** — bewusste Lücke (SPEC-ADR), nicht vergessen.
  Mitigation ist atomares Schreiben (Temp-Datei + Rename), nicht Verhinderung des Race selbst.
- Vorläufer-Prototyp als Cloud-Artifact: `claude.ai/code/artifact/ad6da2b6-5b14-488b-ae53-
  9e59e11f8467` („Lagebericht") — Optik/Struktur dort vom User abgenommen, dient Phase 2
  (Desktop-App) als Vorlage für den Look. Nicht die Datenquelle für dieses Projekt.
- Dieses Projekt entstand komplett in einer einzigen Claude-Code-Session (2026-10-16) — vier
  Architektur-Pivots nacheinander, jeder eine echte Vereinfachung. Eine neue Session, die hier
  weitermacht, hat diesen Verlauf nicht im Kontext — SPEC.md „Verworfene Vorstufen" plus dieses
  HANDOVER sollten reichen, um ohne Nachfragen direkt bei AP2'' weiterzumachen.
