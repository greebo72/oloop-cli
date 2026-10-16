# SPEC — Lagebericht (lokaler Cross-Projekt-Tracker)

**Status:** Entwurf (2026-10-16) — noch nicht freigegeben

**Revision:** 2026-10-16 — initial draft, nach Technik-Grounding (JVM/H2/OneDrive gemessen) und einer User-Rückfrage (Multi-Device)
**Revision:** 2026-10-16 — Architektur-Schwenk (User-Wunsch): zentraler lokaler Service statt Direkt-DB-Zugriff aus jedem Client. Service-Sprache entschieden: Java/Spring Boot.
**Revision:** 2026-10-16 — vollständiger API-Contract ergänzt (`SPEC/api-contract.yaml`, OpenAPI 3.0.3).
**Revision:** 2026-10-16 — Vollständigkeits-Audit: DB-Pfad/Credentials/Zeitformat festgelegt, Backup/Recovery (Copy-on-Start) ergänzt, Zweck/Nicht-Ziele an Service-Architektur angepasst.
**Revision:** 2026-10-16 — Delete/Edit entschieden (User): kein Delete irgendwo (append-only, "mark as finished"), `PATCH /tasks/{id}`-Äquivalent für title/description ergänzt.
**Revision:** 2026-10-16 — Scope-Verengung (User-Wunsch): Server läuft nur auf einem Rechner. OneDrive-Sync-ADR, Lock-Datei, Konfliktkopie-Check entfallen ersatzlos. `StartupType` auf `Automatic`.
**Revision:** 2026-10-16 — **Architektur-Konsolidierung (4. Pivot).** Storage von H2 auf Markdown-Dateien mit YAML-Frontmatter umgestellt. Java/Spring-Boot-Service entfällt komplett: sein einziger
zwingender Grund (H2 spricht JDBC nativ, kein Treiber-Bridge-Risiko) ist mit dem Storage-Wechsel
hinfällig — Textdateien brauchen in keiner Sprache einen Treiber. Phase 2 bestätigt als
**Desktop-App** statt Browser-UI, braucht deshalb ebenfalls keinen HTTP-Service. Projekt ist damit
vollständig Python, keine JVM-Abhängigkeit mehr. Siehe „Verworfene Vorstufen" unten für die
Zusammenfassung aller vier Pivots; `SPEC/api-contract.yaml` bleibt als historischer Beleg liegen
(Kopfzeile markiert es als überholt), ersetzt durch `Implementation/lagebericht/schemas/*.schema.json`.

**Revision:** 2026-10-16 — Task-Felder `createdBy`, `updatedBy`, `labels` ergänzt (User-Wunsch).

## Verworfene Vorstufen

Kurzfassung für kalt einsteigende Leser — vier echte Architektur-Pivots in dieser Spec-Historie,
jeder eine Vereinfachung gegenüber dem vorigen Stand:

1. **Python-CLI mit H2-Direktzugriff** (`jaydebeapi`/JPype-Bridge). Verworfen: kein nativer
   Python-H2-Treiber existiert, jeder weitere Client (MCP, UI) hätte dieselbe Bridge gebraucht.
2. **Java/Spring-Boot-Service als einzige JDBC-Instanz**, CLI/MCP/UI als HTTP-Clients. Löste
   Problem 1, indem nur noch ein Prozess JDBC sprechen musste. Verworfen mit Pivot 4: das
   Treiber-Problem, das den Service begründet hat, existiert bei Textdateien gar nicht erst.
3. **OneDrive-Sync-Schutz + geräteübergreifendes Locking** (Sync-ADR, Konfliktkopie-Check,
   Lock-Datei, `StartupType=Manual`). Verworfen: Server läuft nur auf einem Rechner
   (User-Entscheidung), die ganze Problemklasse existiert nicht.
4. **HTTP-Service als solcher** (nicht nur die Java-Implementierung). Verworfen: Phase 2 wird eine
   Desktop-App (kein Browser-Backend-Zwang), und Textdateien haben kein zentrales
   Treiber-Problem, das einen Service noch rechtfertigen würde. CLI, MCP-Wrapper und die spätere
   Desktop-App lesen/schreiben jetzt direkt über ein gemeinsames, dokumentiertes Dateiformat.

Volltext der verworfenen Entscheidungen (Begründungen, abgewogene Alternativen) steht in den
jeweiligen Revisionszeilen oben bzw. im Session-Verlauf, mit dem diese Spec entstanden ist — hier
nur die Konsequenz, nicht die komplette Herleitung, damit das Dokument lesbar bleibt.

## Zweck

User arbeitet parallel an vielen Projekten (verschiedene Verzeichnisse, teils Wochen inaktiv).
Offene Todos, ungeklärte Fragen, getroffene Entscheidungen und Key Features gehen zwischen
Sessions/Projekten verloren — kein zentraler Überblick, was wo offen ist. `Lagebericht` ist das
lokale Tool dafür: Markdown-Dateien mit YAML-Frontmatter als Datenhaltung (vom Tool verwaltet, nie
von Hand editiert), ein Python-Modul, das CLI und MCP-Wrapper direkt nutzen (kein Netzwerk-Layer,
kein Treiber), später eine Desktop-App im Look des bereits gezeigten Claude-Artifacts, die
dieselben Dateien liest/schreibt.

Vorläufer: Claude-Artifact-Prototyp (`claude.ai/code/artifact/ad6da2b6-...`, 2026-10-16) — dort
wurde Datenmodell und Optik probeweise gezeigt und vom User bestätigt ("sieht schon sehr gut aus").
Dieses Projekt ersetzt die Cloud-DB des Artifacts durch lokale Dateien.

## Nicht-Ziele (diese Spec-Version)

- **Mehrbenutzer/Auth** — Einzelnutzer-Tool, kein Login, kein Rollenkonzept.
- **Mehrere Geräte** — Server/Tool läuft nur auf einem Rechner (User-Entscheidung). Kein
  Sync-Schutz, kein geräteübergreifendes Locking, keine Konfliktkopie-Erkennung.
- **UI** — Phase 2, eigenes Kapitel/eigene Revision. Diese Spec deckt Dateiformat + Python-Modul +
  CLI ab. Phase 2 ist als Desktop-App bestätigt (nicht Browser), Toolkit-Wahl bleibt offen.
- **Netzwerk-/Service-Layer** — bewusst keiner. CLI, MCP-Wrapper und Desktop-App greifen direkt
  auf die Dateien zu (bzw. auf ein gemeinsames Python-Modul, falls die Desktop-App Python-basiert
  wird — sonst gegen das dokumentierte Dateiformat neu implementiert).
- **Direktes Editieren der Dateien durch den User** — explizite Anforderung. Die Markdown-Dateien
  sind Implementierungsdetail des Tools, keine Nutzerschnittstelle.

## Messwerte (2026-10-16, dieses Gerät)

- Python 3.13.3 vorhanden — bleibt relevant, Projekt ist jetzt vollständig Python.
- ~~JVM vorhanden: OpenJDK Corretto 11.0.32.1.~~ ~~H2-Jar lokal vorhanden
  (`~/.m2/repository/com/h2database/h2/2.2.224/h2-2.2.224.jar`).~~ ~~Account `SAMPLE\user` ist
  Mitglied der lokalen Administratoren-Gruppe — Windows-Service-Installation möglich.~~ **Nicht mehr
  relevant** — Pivot 4 (kein Java-Service mehr, keine JVM-Abhängigkeit im Projekt).
- ~~H2 embedded (MVStore) erzeugt genau eine Datei, keine Zusatz-/Lock-Datei im geschlossenen
  Zustand (verifiziert per Testlauf in einem OneDrive-Ordner).~~ **Nicht mehr relevant** — kein H2
  mehr, siehe Pivot 1/4.

## Backup/Recovery

Viele kleine Dateien statt einer einzelnen Binärdatei sind von sich aus robuster: ein beschädigter
Task killt nicht den ganzen Datenbestand, anders als ein korruptes `.mv.db`. **In Scope für V1:**
das Python-Modul legt vor jedem Schreibzugriff eine Kopie der betroffenen Datei unter
`.backup/<dateiname>.bak` an (eine Generation, kein Rotationsschema) — billige Absicherung gegen
den einen Fehlerfall (Bug im Schreibpfad, Absturz mitten im Schreiben), der trotz atomarem
Rename-Schreiben theoretisch bleibt. Mehr (Versionierung, mehrere Generationen) ist nicht Scope
dieser Freigabe.

## ADR — Dateibasierte Persistenz, kein Service

**Kontext:** User fragte, was ein Wechsel von H2 auf Textdateien bedeuten würde (UI soll gleich
bleiben, Dateien nie direkt editiert), dann ob angesichts dessen ein separater HTTP-Service noch
Sinn macht.

**Entscheidung:** Kein Service. Markdown+YAML-Frontmatter-Dateien, verwaltet durch ein gemeinsames
Python-Modul, das CLI und MCP-Wrapper direkt importieren.

**Warum der Service wegfällt, nicht nur seine Java-Implementierung:** Der dominante Grund für den
Service war, das JDBC-Treiber-Problem auf eine Instanz zu konzentrieren (siehe Vorstufe 2 oben).
Textdateien haben dieses Problem in keiner Sprache. Der zweite Grund (Browser kann kein JDBC) wäre
ohnehin nur relevant gewesen, wenn Phase 2 eine Browser-UI würde — User hat Phase 2 stattdessen als
Desktop-App bestätigt („klein und schnell"), die lokale Dateien direkt lesen kann, kein
Backend-Zwang. Der dritte Grund (Zugriffscode bündeln) ist bei Datei-I/O (Frontmatter
parsen/schreiben) viel schwächer als bei Connection-Pooling/JDBC — wenig, das sich hinter einer
Schnittstelle zu verstecken lohnt.

**Was dadurch verloren geht, bewusst in Kauf genommen:** Ohne zentralen Service gibt es keine
eingebaute Serialisierung von Schreibzugriffen mehr. Mitigation: atomares Schreiben (Temp-Datei im
selben Verzeichnis + `os.replace`) verhindert kaputte/halb geschriebene Dateien. Ein echtes Race
zwischen zwei *gleichzeitigen* Schreibzugriffen auf denselben Task (z. B. CLI und eine künftige
Desktop-App im selben Moment) bleibt ein Restrisiko — last-writer-wins, kein Lock. Für ein
Einzelnutzer-Tool mit selten wirklich gleichzeitiger Nutzung akzeptiert, hier dokumentiert statt
stillschweigend offen gelassen.

**Beobachtung für Phase 2, keine Entscheidung jetzt:** Electron widerspricht dem genannten „klein
und schnell" (bündelt Chromium, hoher Speicherbedarf). Leichtere Alternativen, die den bereits
gestalteten HTML/CSS/JS-Look des Artifact-Prototyps weiterverwenden könnten: Tauri (Rust) oder
Wails (Go) mit System-WebView statt gebündeltem Chromium; alternativ eine native GUI (z. B.
.NET/WPF, da reines Windows-Target). Eigenes Spec-Kapitel, eigene Freigabe, hier nur vermerkt.

## Dateiformat

```
<daten-root>/                      -- Pfad konfigurierbar (CLI-Config/ENV, kein Hardcoding)
  <project-slug>/
    project.md                     -- Frontmatter: id (UUID), name, createdAt,
                                       optional referencePath, referenceHost
    tasks/
      <task-id>.md                 -- Dateiname = UUID, stabil über die Lebenszeit des Tasks
                                       (Titel-Änderung benennt die Datei NICHT um — Referenzen/
                                       Kommentare bleiben stabil)
    images/
      <task-id>.<ext>              -- optionales Bild je Task, neben der Task-Datei
```

Frontmatter je Task (YAML-Header, `python-frontmatter`-kompatibel):

```yaml
id: <uuid>
projectId: <uuid>
type: todo|question|decision|feature|bug
title: "..."
status: open|closed
imagePath: null | "images/<task-id>.<ext>"
createdAt: 2026-10-16T12:00:00Z
createdBy: user
updatedAt: 2026-10-16T12:00:00Z
updatedBy: user
labels: [backend, urgent]
comments:
  - body: "..."
    createdAt: 2026-10-16T12:05:00Z
```

Markdown-Body = `description`. **Maßgeblich für die genaue Struktur:** `Implementation/lagebericht/schemas/project.schema.json`,
`Implementation/lagebericht/schemas/task.schema.json` (JSON Schema, übernimmt die Rolle, die vorher `api-contract.yaml` hatte —
bei Widerspruch gelten die Schema-Dateien).

**`image_path` relativ, Datei statt BLOB:** unverändert aus den Vorstufen — jetzt ohnehin die
natürliche Lösung bei Dateibasierter Persistenz.

**`createdBy`/`updatedBy`/`labels`:** Akteur als Freitext-String (Quelle: Parameter, sonst
`$LAGEBERICHT_ACTOR`, sonst OS-Benutzername). `updatedBy` wird bei jeder Änderung zusammen mit
`updatedAt` gesetzt, `createdBy` nie. `labels` = Liste eindeutiger Strings, per Edit ersetzbar.

**Ein `status`-Feld, kein Enum pro Typ:** Label in der Darstellungsschicht variiert pro Typ (todo →
„Erledigt", question → „Geklärt", decision → „Entschieden", feature → „Umgesetzt", bug →
„Behoben"). Datenmodell bleibt `open`/`closed`.

**`updatedAt` ist Pflichtfeld, kein Beiwerk:** zentraler Zweck des Tools ist, nach 1-2 stillen
Wochen zu sehen, was zuletzt passiert ist. CLI (und später UI) filtern/sortieren danach.

**Kein Delete, nirgends.** User-Entscheidung: „Mark as finished statt delete ist der Weg."
Task/Comment/Project sind append-only — `close`/`reopen` (Status-Flip in der Frontmatter) ist der
einzige Abschluss-Mechanismus, keine Datei verschwindet je.

**Edit erlaubt, aber begrenzt.** `title`/`description` eines Tasks sind nachträglich korrigierbar
(überschreibt die Frontmatter-Felder bzw. den Markdown-Body, `updatedAt` wird aktualisiert,
Dateiname bleibt stabil). Projektname und Task-Typ sind NICHT editierbar in V1.

**ID-Schema:** UUID4 pro Entität (kein Auto-Increment mehr möglich ohne DB). Dateiname = ID.

**Zeitstempel:** UTC, ISO 8601 mit `Z`. Lokalisierung auf Anzeigeseite (CLI/UI), nicht im
Dateiformat. Relevant für `staleDays`-Filterung im CLI.

## Phase 2

Desktop-App (User-Entscheidung, „klein und schnell"), Look & Struktur analog zum gezeigten
Claude-Artifact (Sidebar-Projektfilter, Typ-Tabs/Stat-Kacheln, Karten mit Status-Toggle) — plus
"seit wann still" als neue Ansicht (folgt aus `updatedAt`). Toolkit TBD, siehe ADR-Beobachtung oben
(Electron vermutlich zu schwer für „klein und schnell", Tauri/Wails/native GUI als Kandidaten).
Eigenes Spec-Kapitel, eigene Freigabe, erst nach Freigabe/Nutzung dieser Version.

## Offene Punkte

Keine Freigabe-Blocker bekannt. Verbleibend — vor den jeweiligen Arbeitspaketen zu klären:

- **Konkreter Daten-Root-Pfad** — Spec legt nur fest, dass er konfigurierbar ist (CLI-Config/ENV,
  kein Hardcoding); tatsächlicher Wert kommt vom User vor dem ersten Lauf.
- CLI-Framework (Python, `click` vs. `argparse`) — Implementierungsdetail.
- MCP-Wrapper: importiert dasselbe Python-Modul — kann parallel zum CLI oder später entstehen.
- Phase-2-Toolkit (s. o.) — eigenes Kapitel, nicht Teil dieser Freigabe.
- Git-Remote für dieses Projekt (nur lokal vs. Backup-Remote) — nicht erfragt; `git init` reicht
  für die von CLAUDE.md geforderte Commit-Referenzierung, Remote kann später folgen.
