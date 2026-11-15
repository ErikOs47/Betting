# Als Web-App auf dem Handy nutzen

Die App ist eine **installierbare Web-App (PWA)**: Sie läuft im Browser, lässt sich aber wie eine normale App auf den
Homescreen legen (eigenes Icon, Vollbild, untere Tab-Leiste). Dafür muss sie auf einem Rechner laufen, den dein Handy
erreicht. Es gibt drei Wege – vom einfachsten zum bequemsten:

| Weg | Kosten | Erreichbar | Als App installierbar | Voraussetzung |
|---|---|---|---|---|
| A. Eigener Server im Internet | ca. 4–6 € / Monat | überall | ja (HTTPS) | kleiner Cloud-Server, 15 Minuten Einrichtung |
| B. Eigener PC + Tailscale | kostenlos | überall | ja (HTTPS über Tailscale) | PC muss laufen |
| C. Eigener PC, nur WLAN | kostenlos | nur zu Hause | Android teilweise, iPhone nur als Lesezeichen | PC muss laufen |

**Empfehlung: A**, weil die App rund um die Uhr Ergebnisse, Verletzungen und Prognosen aktualisieren muss und das
nur zuverlässig funktioniert, wenn der Rechner dauerhaft läuft.

In allen Fällen gilt beim **ersten Start**: Die App lädt selbstständig drei Saisons Spieldaten (Basketball Reference
erlaubt höchstens 20 Anfragen pro Minute, daher ca. 4 Stunden), trainiert danach die Modelle und erstellt Prognosen.
Den Fortschritt siehst du unter *Mehr → Einstellungen → Jobs*. Bis dahin sind die Seiten leer.

---

## A. Eigener Server im Internet (empfohlen)

**1. Server mieten.** Jeder Anbieter mit Ubuntu und mindestens **2 CPU-Kernen und 4 GB RAM** reicht, z. B. ein
kleiner Cloud-Server bei Hetzner, Netcup, IONOS oder DigitalOcean. Beim Anlegen „Ubuntu 24.04“ wählen und die
**IP-Adresse** notieren.

**2. Per SSH verbinden** (Windows: PowerShell, Mac: Terminal):

```bash
ssh root@DEINE-IP
```

**3. Docker installieren und Projekt holen:**

Ist dein GitHub-Repository privat, fragt `git clone` nach Benutzername und Passwort: als Passwort ein
[Personal Access Token](https://github.com/settings/tokens) mit Leserecht auf das Repository eingeben.

```bash
curl -fsSL https://get.docker.com | sh
git clone https://github.com/ErikOs47/Betting.git
cd Betting
cp .env.example .env
nano .env
```

**4. In `.env` diese Zeilen ausfüllen** (Speichern in nano: Strg+O, Enter, Strg+X):

```ini
NBI_APP_PASSWORD=ein-langes-eigenes-passwort
NBI_SESSION_SECRET=beliebige-lange-zufallszeichen     # z. B. Ausgabe von: openssl rand -hex 32
DOMAIN=203-0-113-7.sslip.io                          # deine IP mit Bindestrichen + .sslip.io (oder eigene Domain)
WEB_PORT=127.0.0.1:3000                              # Web-App nur über HTTPS erreichbar machen
POSTGRES_PASSWORD=noch-ein-passwort
```

`sslip.io` ist ein kostenloser Dienst, der `203-0-113-7.sslip.io` auf die IP `203.0.113.7` zeigen lässt – so bekommst
du ohne eigene Domain ein gültiges HTTPS-Zertifikat. Hast du eine eigene Domain, trage sie ein und lege beim
Domain-Anbieter einen A-Eintrag auf die Server-IP an.

**5. Starten:**

```bash
docker compose --profile https up -d --build
```

Nach 2–5 Minuten ist die App unter `https://DEINE-DOMAIN` erreichbar.

**6. Auf dem Handy installieren:** Seite öffnen, mit dem Passwort anmelden, dann
- **iPhone (Safari):** Teilen-Symbol → „Zum Home-Bildschirm“
- **Android (Chrome):** Menü ⋮ → „App installieren“ bzw. „Zum Startbildschirm hinzufügen“

**Updates einspielen** (wenn es neuen Code auf GitHub gibt):

```bash
cd Betting && git pull && docker compose --profile https up -d --build
```

Die Daten liegen in Docker-Volumes und bleiben bei Updates erhalten.

**Sicherheit:** Ohne `NBI_APP_PASSWORD` wäre die App für jeden mit dem Link offen – deshalb immer setzen. Die
Datenbank und das Backend sind von außen nicht erreichbar, nur die Web-App über HTTPS. Nach 10 Fehlversuchen pro
15 Minuten wird die Anmeldung gesperrt.

---

## B. Eigener PC + Tailscale (kostenlos, von überall)

1. [Docker Desktop](https://www.docker.com/products/docker-desktop/) installieren, Projekt herunterladen
   (GitHub → „Code“ → „Download ZIP“, oder `git clone`), im Projektordner `.env.example` nach `.env` kopieren und
   `NBI_APP_PASSWORD` setzen.
2. Im Projektordner: `docker compose up -d --build`
3. [Tailscale](https://tailscale.com/download) auf dem PC **und** dem Handy installieren, beide mit demselben Konto
   anmelden.
4. Auf dem PC: `tailscale serve --bg 3000` – Tailscale zeigt eine Adresse wie `https://mein-pc.tail1234.ts.net`.
   Beim ersten Mal fordert Tailscale dich auf, in der Tailscale-Verwaltung „MagicDNS“ und „HTTPS“ zu aktivieren.
5. Diese Adresse auf dem Handy öffnen (Tailscale-App muss verbunden sein) und wie oben zum Homescreen hinzufügen.

Die App ist nur für Geräte in deinem Tailscale-Netz erreichbar, nicht öffentlich. Der PC muss eingeschaltet sein,
damit Daten aktualisiert werden.

---

### Muss der PC rund um die Uhr laufen?

**Nein.** Die App holt beim Start alles nach, was sie verpasst hat:

| Pause | Nachholen dauert ca. |
|---|---|
| 1 Tag | 1 Minute |
| 1 Woche | 5 Minuten (inkl. Neu-Training) |
| ganze Sommerpause | 10–15 Minuten |

Danach aktualisiert sie, solange der PC läuft: Ergebnisse alle 30 min, Injury Report alle 15 min. Den Stand siehst du
unter *Mehr → Datenquellen* (grün = aktuell).

Einschränkungen:
- **Nur einmal** muss der PC länger laufen: beim allerersten Start, ca. 4 Stunden für den Download von drei Saisons.
  Unterbrechen ist kein Problem, der Download setzt beim nächsten Start fort.
- Ist der PC aus oder im Standby, ist die App auch vom Handy aus **nicht erreichbar**.
- **Strom:** Ein PC, der 24 Stunden durchläuft, kostet je nach Gerät grob 10–30 € Strom im Monat und damit mehr als
  ein kleiner Mietserver. Sinnvoll ist daher: PC einschalten, wenn du die App nutzen willst, 5–10 Minuten warten.
- **Timing:** NBA-Spiele beginnen meist zwischen 1 und 4:30 Uhr deutscher Zeit. Die wichtigen Verletzungs-Updates
  kommen am Spieltag ab dem Nachmittag und bis kurz vor Spielbeginn. Wer am Abend wettet, sollte den PC also
  abends ein paar Minuten vorher einschalten.

Docker Desktop startet die App automatisch mit, wenn in Docker Desktop „Start Docker Desktop when you sign in“
aktiviert ist; die Container selbst starten dank `restart: unless-stopped` von allein.

---

## C. Nur im eigenen WLAN

Schritte 1–2 wie bei B, dann auf dem Handy `http://IP-DES-PCS:3000` öffnen (IP des PCs: Windows `ipconfig`,
Mac Systemeinstellungen → Netzwerk). Ohne HTTPS installiert das iPhone die Seite nur als Lesezeichen, und die
Anmeldung bleibt nicht so lange gespeichert. Für die volle App-Funktion Weg A oder B nutzen.

---

## Warum nicht Render, Vercel & Co.?

Die App besteht aus drei Teilen (Datenbank, Backend mit Zeitplaner, Web-Oberfläche) und muss dauerhaft laufen.
Bei Plattformen mit kostenlosem Tarif schläft der Dienst nach kurzer Inaktivität ein (dann stoppen die Updates),
kostenlose Datenbanken laufen nach einiger Zeit ab, und dauerhaft laufende Dienste kosten dort je Teil extra. Ein
kleiner eigener Server ist für diesen Fall günstiger und einfacher.
