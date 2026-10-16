# PLAN — Lagebericht

**Revision:** 2026-10-16 — initial, nach SPEC-Entwurf
**Revision:** 2026-10-16 — Architektur-Schwenk auf Service (Java/Spring Boot) statt Python-CLI mit
Direktzugriff. ~~AP3/AP4 (jaydebeapi-CLI)~~ ersetzt durch AP3'/AP4'.
**Revision:** 2026-10-16 — Scope-Verengung auf einen Rechner (User-Wunsch): ~~AP5~~ (Konfliktkopie-
Check) entfällt ersatzlos, AP3b vereinfacht (`StartupType=Automatic`, keine Lock-Datei mehr).
**Revision:** 2026-10-16 — **Architektur-Konsolidierung (4. Pivot):** Dateibasierte Persistenz statt
H2, kein HTTP-Service mehr. ~~AP3'/AP3b/AP4'~~ (Spring-Boot-Service, Windows-Service-Lifecycle,
Service-Endpunkte) entfallen ersatzlos — siehe SPEC.md „Verworfene Vorstufen". Ersetzt durch
AP2''/AP4'' unten.

Reihenfolge ist Abhängigkeitsreihenfolge, nicht zwingend zeitlich.

## Arbeitspakete

- [ ] **AP1 — Projekt-Setup**
  Verzeichnisstruktur (`SPEC/`, `Implementation/`), `git init`, `.gitignore` (venv, `__pycache__`,
  `*.mv.db`, `*.jar` — letztere zwei sind Altlasten aus der H2-Ära, schaden nicht, bleiben falls
  die Historie je gebraucht wird). Abhängig von: nichts. *(Struktur/git erledigt)*

- ~~[ ] **AP2 — Schema + DDL**~~ (H2-DDL) — entfällt, siehe AP2'' unten.
- ~~[ ] **AP3 — Python-CLI-Grundgerüst** (jaydebeapi)~~ — entfällt.
- ~~[ ] **AP4 — CLI-Befehle** (Direktzugriff via jaydebeapi)~~ — entfällt, siehe AP4'' unten.
- ~~[ ] **AP3' — Spring-Boot-Service-Grundgerüst**~~ — entfällt, kein Service mehr.
- ~~[ ] **AP3b — Service-Lifecycle (Windows Service)**~~ — entfällt, kein residenter Prozess mehr.
- ~~[ ] **AP4' — Service-Endpunkte**~~ — entfällt, kein HTTP mehr. Endpunkt-Liste lebt als
  CLI-Subcommands in AP4'' weiter (die fachliche Operationen bleiben dieselben).
- ~~[ ] **AP4c — CLI/MCP als HTTP-Client**~~ — entfällt, CLI/MCP rufen jetzt direkt ein
  Python-Modul statt HTTP.

- [x] **AP2'' — Dateiformat-Modul**
  Python-Paket (`lagebericht/store.py` o. ä.): Frontmatter lesen/schreiben (`python-frontmatter`
  oder eigene minimale Implementierung), atomares Schreiben (Temp-Datei + `os.replace`),
  UUID-Vergabe, Projekt-/Task-Auflösung (Name → Ordner, ID → Datei), Backup-Kopie vor Schreiben
  (SPEC „Backup/Recovery"). Validiert gegen `Implementation/lagebericht/schemas/*.schema.json`.
  Abhängig von: AP1, SPEC freigegeben.

- [x] **AP4'' — CLI-Befehle**
  `project add/list`, `task add/list/edit/close/reopen`, `comment add` — fachlich identisch zu den
  vormaligen HTTP-Endpunkten (SPEC „Dateiformat" / verworfene `api-contract.yaml` als Referenz für
  die Filterparameter: `projectName`, `type`, `status`, `staleDays`). `--json` auf lesenden
  Befehlen. Direkt gegen AP2'', keine Netzwerkschicht. Abhängig von: AP2''.

- [ ] **AP4d — MCP-Wrapper** *(optional, kann parallel oder später)*
  Dünne Schicht, importiert dasselbe Python-Modul wie das CLI (AP2''). Abhängig von: AP2''.

- [ ] **AP6 — Phase 2: Desktop-App** *(separates Spec-Kapitel, eigene Freigabe)*
  Toolkit-Entscheidung (SPEC-ADR-Beobachtung: Electron vermutlich zu schwer für „klein und
  schnell", Tauri/Wails/native GUI als Kandidaten), dann Umsetzung im Look des Artifact-Prototyps,
  liest/schreibt dieselben Dateien (direkt oder über AP2'', je nach Toolkit-Sprache). Abhängig
  von: AP4'', etwas gelebte Nutzung über CLI.

## Risikotabelle

| Risiko | Auswirkung | Status |
|---|---|---|
| ~~OneDrive synct `.mv.db` mitten im Schreibvorgang~~ | ~~DB-Korruption~~ | **Entfällt** — Scope-Verengung auf einen Rechner |
| ~~Konfliktkopie-Namensmuster unbekannt~~ | ~~AP5 falsch negativ~~ | **Entfällt** — AP5 gestrichen |
| ~~`jaydebeapi`/JPype-Installation plattformabhängig~~ | ~~CLI startet nicht ohne Setup~~ | **Entfällt** — kein Java/H2 mehr im Projekt |
| ~~Service-Installation braucht elevated Schritt (UAC)~~ | ~~Kein automatisierbarer Schritt~~ | **Entfällt** — kein Service/Windows-Service mehr |
| ~~Einzelne `.mv.db`-Datei wächst mit Bild-BLOBs~~ | ~~Datei wird groß/langsam~~ | **Entfällt** — nie BLOBs, jetzt ohnehin Dateien neben Dateien |
| Kein Lock bei gleichzeitigem Schreibzugriff (z. B. CLI + künftige Desktop-App im selben Moment) | Last-writer-wins auf Task-Ebene, Datenverlust des verlierenden Schreibvorgangs | Gemindert durch atomares Schreiben (keine kaputten Dateien), Race selbst bewusst in Kauf genommen (SPEC-ADR) — Einzelnutzer-Tool, selten wirklich gleichzeitig |
| Beliebige Dateibeschädigung (Bug, Absturz mitten im Schreiben) | Verlust einzelner Task-Datei, nicht des gesamten Bestands | Gemindert durch Backup-Kopie vor Schreiben (SPEC „Backup/Recovery") und dadurch, dass Schaden strukturell auf eine Datei begrenzt bleibt |
