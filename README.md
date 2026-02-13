# Jellyfin + Telerising + EasyEPG DockerPack für UGREEN NAS

Dieses Repository enthält ein fertig vorbereitetes Docker-Projekt für **UGREEN NAS (UGOS Pro)**, das eine komplette **Jellyfin Live-TV Umgebung** inkl. **Reverse-Proxy (SWAG)**, **EPG (EasyEPG)** und **Telerising** bereitstellt.

- Sprache: **Deutsch** (zusätzlich sind die Handbücher auch auf Englisch enthalten)
- Varianten: **DXP-Serie (Intel)** und **DH-Serie (Rockchip/ARM)** mit separaten Compose-Dateien

➡️ **Download:** Nutze die GitHub-**Releases** und lade die ZIP-Datei herunter.  
➡️ **Handbuch:** Siehe PDF im jeweiligen Sprachordner (DE/EN).

---

## Inhalt

- **Jellyfin** (Media Server, Live TV)
- **SWAG** (Nginx Reverse Proxy + Let's Encrypt, optional Fail2Ban)
- **EasyEPG** (EPG-Generator / XMLTV)
- **Telerising** (API/Integration)

---

## Projektstruktur

- `v1/de/JellyfinServer/`  
  - `.env` (Zentrale Konfiguration)  
  - `docker-composeDXP.yaml` (DXP-Serie, Intel iGPU/QSV)  
  - `docker-composeDH.yaml` (DH-Serie, Rockchip/ARM, RKMPP)  
  - Unterordner mit Container-Configs (Jellyfin, SWAG, EasyEPG, Telerising)
- `v1/de/JellyfinServer_Handbuch_DE_v1_jellyfin.pdf` (Handbuch DE)
- `v1/en/...` (englische Variante + Handbuch EN)
- `release-assets/` (lokal: ZIPs für Releases, wird **nicht** committet)

---

## Installation (Quickstart, UGOS Pro)

1) **ZIP aus Releases** herunterladen und entpacken, z.B. nach:  
   `/volume2/docker/JellyfinServer/`

2) **Konfiguration anpassen**  
   Datei `.env` öffnen und mindestens diese Werte setzen:
   - `TZ`, `PUID`, `PGID`
   - `MEDIA_DIR`, `JELLYFIN_SYSTEM_DIR`
   - `SWAG_URL`, `SWAG_SUBDOMAINS`, `SWAG_EMAIL`
   - Ports nach Bedarf (`JELLYFIN_PORT_HTTP`, `SWAG_PORT_HTTP`, `SWAG_PORT_HTTPS`, ...)

3) **Richtige Compose-Datei wählen**
   - **DXP-Serie (Intel):** `docker-composeDXP.yaml`
   - **DH-Serie (Rockchip/ARM):** `docker-composeDH.yaml`

   Danach die passende Datei in **`docker-compose.yaml`** umbenennen.

4) **Docker Projekt importieren**
   - UGREEN Docker App → **Projekte** → **Importieren**
   - Ordner `JellyfinServer` auswählen
   - Projekt starten

5) **Aufrufen**
   - Lokal: `http://<NAS-IP>:8096`
   - Über SWAG: `https://jellyfin.<deine-domain>`

---

## Hinweise zu Reverse Proxy (SWAG)

- Für externen Zugriff müssen i.d.R. **80/443** am Router auf die in `.env` definierten SWAG-Ports weitergeleitet werden.
- Wenn du Jellyfin hinter dem Reverse Proxy betreibst, setze in Jellyfin unter  
  **Dashboard → Erweitert → Netzwerk** den Reverse-Proxy als „Bekannter Proxy“ (Container-IP).

---

## Updates

- Container-Images aktualisieren (z.B. in der Docker App) und Projekt neu starten.
- Änderungen an `.env` und `docker-compose.yaml` beibehalten.

---

## Lizenz

MIT License (siehe `LICENSE`).

## Autor

Roman Glos (Railsimulatornet)
