# Wartung / Maintenance

## Deutsch

- Die vorhandene Paketprüfung kontrolliert beide Sprachfassungen und baut die ZIP-Kandidaten aus den eingecheckten Dateien.
- Der neue Workflow **Image security** prüft täglich und bei relevanten Änderungen die vier im Paket verwendeten öffentlichen Container-Images für AMD64 und ARM64. Er startet keine Container und ändert keine NAS-Installation.
- Ein technisch unvollständiger Scan, ein nicht mehr unterstütztes Basisbetriebssystem oder behebbare hohe/kritische Befunde lassen die Prüfung fehlschlagen. Noch nicht behebbare Befunde bleiben im vollständigen Bericht und unter **Security → Code scanning** sichtbar.
- In den Workflow-Artefakten stehen Scanner-Version, tatsächlich untersuchte Image-Identität und vollständige Berichte. Die eingesetzte Scanner-Version wird vor jedem Lauf aus der offiziellen Quelle bezogen und für den gesamten Job beibehalten.
- Bei einem fehlgeschlagenen Scan zuerst den betroffenen Image-Bericht prüfen. Erst nach einem passenden Herstellerupdate und erfolgreichen Prüfungen ein Paket übernehmen; keine Befunde ausblenden.
- GitHub-Prüfungen ersetzen kein Update laufender Container. Für ein NAS-Update Konfiguration und Daten sichern, das aktuelle Image laden und anschließend die Funktion nach dem Handbuch prüfen.

## English

- Existing package checks validate both languages and build ZIP candidates from committed files.
- **Image security** checks the four public container images used by this package daily and after relevant changes for AMD64 and ARM64. It does not start containers or change a NAS installation.
- Incomplete scans, unsupported base operating systems and fixable high/critical findings fail the check. Findings without an available fix remain in the complete report and under **Security → Code scanning**.
- Workflow artifacts retain the scanner version, the identity of each scanned image and complete reports. The scanner is obtained from its official source for each run and retained throughout the job.
- When a scan fails, inspect the affected image report first. Accept an update after a suitable upstream release and successful checks; do not hide findings.
- GitHub checks do not update running containers. Back up configuration and data, pull the updated image and follow the manual's functional checks when updating a NAS.
