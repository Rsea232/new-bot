# News-Bot (Telegram)

Verschickt Nachrichten von **NTV, WELT, Tagesschau und Financial Times** per Telegram (WhatsApp optional):

| Wann | Was |
|------|-----|
| 08:00, 14:00, 20:00 Uhr | Nachrichten-Digest (neue Meldungen, **ohne Wiederholungen** innerhalb des Tages) |
| sofort | **Eilmeldungen**, sobald sie auftauchen |
| täglich 21:00 Uhr | **Tagesübersicht** (Zusammenfassung des Tages) |
| sonntags 21:00 Uhr | zusätzlich **Wochenübersicht** (Zusammenfassung der Woche) |

Eine Meldung, die schon einmal verschickt wurde, kommt am selben Tag nicht erneut –
das gilt über alle Sendungen und auch über Eilmeldungen hinweg. Erkennung über
Meldungs-ID **und** Titel-Ähnlichkeit (so wird dieselbe Story aus zwei Quellen nicht doppelt gesendet).

## Dateien

- `news_bot.py` – das Programm
- `requirements.txt` – Python-Pakete
- `.github/workflows/news-bot.yml` – fertiger Zeitplan für **GitHub Actions** (kostenlos, kein Server)
- `newsbot.service` – systemd-Dienst für einen **eigenen Server / VM**

---

## 1. Lokal testen

```bash
pip install -r requirements.txt
python3 news_bot.py --digest morning   # einen Digest sofort erzeugen
python3 news_bot.py --breaking         # nur Eilmeldungen prüfen
python3 news_bot.py --daily            # Tagesübersicht
python3 news_bot.py --weekly           # Wochenübersicht
```

Ohne gesetzte Zugangsdaten wird die Nachricht **nur in der Konsole** angezeigt.

## 2. Telegram einrichten (kostenlos, sofort, zuverlässig)

1. **Bot erstellen:** Öffne in Telegram den Chat mit **@BotFather**, sende `/newbot`
   und folge den Fragen (Name + Benutzername, der auf `bot` endet). Du bekommst einen
   **Token** der Form `123456789:ABCdef...` – das ist dein `TELEGRAM_BOT_TOKEN`.
2. **Chat starten:** Öffne deinen neuen Bot (über den Link von BotFather) und drücke
   **Start** bzw. schicke ihm irgendeine Nachricht. (Ohne das kann der Bot dir nicht schreiben.)
3. **Chat-ID herausfinden:** Rufe im Browser diese Adresse auf (Token einsetzen):
   `https://api.telegram.org/bot<DEIN_TOKEN>/getUpdates`
   Suche im Text nach `"chat":{"id":...}` – diese Zahl ist deine `TELEGRAM_CHAT_ID`.
   (Alternativ: in Telegram **@userinfobot** anschreiben, der nennt dir deine ID.)

Dann als Umgebungsvariablen setzen:

```bash
export TELEGRAM_BOT_TOKEN="123456789:ABCdef..."
export TELEGRAM_CHAT_ID="123456789"
```

> Hinweis: WhatsApp über CallMeBot ist weiterhin als Alternative eingebaut. Setzt du
> stattdessen `CALLMEBOT_PHONE` und `CALLMEBOT_APIKEY`, läuft der Versand darüber.
> Ist Telegram konfiguriert, hat es Vorrang.

## 3. Optional: echte KI-Zusammenfassung

Standardmäßig sind die Übersichten kompakte, nach Quelle gruppierte Listen.
Für einen flüssigen, thematisch zusammengefassten Text:

```bash
export USE_LLM_SUMMARY=1
export ANTHROPIC_API_KEY="sk-ant-..."
export ANTHROPIC_MODEL="claude-sonnet-4-6"   # optional; günstiger: claude-haiku-4-5-20251001
```

(Empfehlenswert besonders für die Tages- und Wochenübersicht.)

---

## 4. Den Bot dauerhaft & kostenlos laufen lassen

Es gibt zwei gute Wege. Kurzfassung:

| | GitHub Actions | Oracle Cloud „Always Free" |
|---|---|---|
| Kosten | kostenlos (öffentliches Repo = unbegrenzte Minuten) | dauerhaft kostenlos |
| Eigener Server nötig? | nein | ja (kleine VM, einmal einrichten) |
| Eilmeldung kommt | mit ~5–30 Min Verzögerung | nahezu in Echtzeit |
| Kreditkarte zur Anmeldung | nein | ja (nur zur Verifizierung, keine Abbuchung) |

➡️ **Für „einfach und ohne Server"**: GitHub Actions.
➡️ **Für „Eilmeldungen sofort"**: Oracle-VM mit `--daemon`.

### Option A – GitHub Actions (empfohlen, kein Server)

Der Bot läuft als geplanter Job in GitHub. Die Datei `.github/workflows/news-bot.yml`
ist schon fertig (Lauf alle 5 Minuten; das Skript entscheidet, was gerade dran ist).

1. Lege ein **öffentliches** GitHub-Repository an (öffentliche Repos haben unbegrenzte
   kostenlose Action-Minuten) und lade alle Dateien hoch.
2. Repo → **Settings → Secrets and variables → Actions**:
   - unter **Secrets**: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (und bei KI-Nutzung `ANTHROPIC_API_KEY`)
   - unter **Variables**: `USE_LLM_SUMMARY` = `1`, falls gewünscht
3. Reiter **Actions** öffnen und Workflows aktivieren. Fertig – der Bot läuft automatisch.

Hinweise:
- Deine Zugangsdaten liegen verschlüsselt in den Secrets, **nicht** im Code. Öffentlich
  sichtbar ist nur `state.json` (reine Schlagzeilen-Titel) – darin stehen keine Geheimnisse.
- GitHub führt geplante Läufe in **UTC** aus und kann sie unter Last verzögern; deshalb sind
  Eilmeldungen hier nicht sekundengenau. Die Uhrzeiten (08/14/20/21) rechnet das Skript selbst
  auf Berliner Zeit um (`BOT_TZ`).
- GitHub deaktiviert geplante Workflows nach **60 Tagen ohne Repo-Aktivität**. Da der Bot
  `state.json` regelmäßig zurückspeichert (=Aktivität), passiert das im Normalbetrieb nicht.

### Option B – Oracle Cloud „Always Free"-VM (Eilmeldungen in Echtzeit)

Eine dauerhaft kostenlose Mini-VM, auf der der Bot als Dienst durchläuft.

1. Account auf https://www.oracle.com/cloud/free/ anlegen (Kreditkarte nur zur
   Verifizierung, es wird nichts abgebucht).
2. VM erstellen. Tipp: Die **AMD-Shape `VM.Standard.E2.1.Micro`** (2 Stück immer frei)
   ist fast überall verfügbar; die größere Ampere-Shape ist oft „out of capacity".
   Als Betriebssystem Ubuntu wählen, SSH-Key hinterlegen.
3. Per SSH verbinden und einrichten:

   ```bash
   sudo apt update && sudo apt install -y python3-pip git
   git clone <dein-repo> news-bot && cd news-bot
   pip3 install -r requirements.txt
   ```

4. `newsbot.service` anpassen (Pfade, Telefonnummer, APIKEY) und installieren:

   ```bash
   sudo cp newsbot.service /etc/systemd/system/
   sudo systemctl enable --now newsbot
   sudo journalctl -u newsbot -f      # Live-Log ansehen
   ```

Der Dienst startet bei Reboot automatisch neu. Im `--daemon`-Modus prüft der Bot
Eilmeldungen alle 5 Minuten (`BREAKING_POLL_MINUTES` im Skript anpassbar).

Hinweis: Oracle darf **untätige** VMs zurückfordern (wenn die CPU über 7 Tage im
95-Perzentil unter 20 % liegt). Der Bot erzeugt nur wenig Last – sollte das je ein
Problem werden, hilft eine kleine periodische Aktivität (z. B. ein zusätzlicher cron-Job).

---

## Eilmeldungen – wie erkannt wird

Eine Meldung gilt als Eilmeldung, wenn ihr Titel eines dieser Wörter enthält
(anpassbar über `BREAKING_KEYWORDS` im Skript):

`eilmeldung`, `eil:`, `breaking`, `+++`, `live:`

Das ist eine Heuristik auf Basis der RSS-Titel. Falls eine Quelle Eilmeldungen anders
kennzeichnet, einfach das passende Stichwort ergänzen.

## Einstellungen (oben in `news_bot.py`)

| Variable | Bedeutung |
|----------|-----------|
| `FEEDS` | RSS-Quellen |
| `BREAKING_KEYWORDS` | Stichwörter für Eilmeldungen |
| `MAX_BREAKING_PER_RUN` | max. Eilmeldungen pro Prüflauf (gegen Flut) |
| `DIGEST_TIMES`, `DAILY_TIME`, `WEEKLY_TIME` | Sendezeiten |
| `BREAKING_POLL_MINUTES` | Prüfintervall für Eilmeldungen (nur `--daemon`) |
| `GRACE_HOURS` | wie lange ein verpasster Slot im `--check`-Modus nachgeholt wird |
| `SIMILARITY_THRESHOLD` | ab wann zwei Titel als gleiche Meldung gelten |
| `FETCH_TIMEOUT` | Sekunden Timeout pro Feed |
| `INCLUDE_LINKS` | `0` = ohne Links (kürzere Nachrichten) |

## Umgebungsvariablen

| Variable | Pflicht | Zweck |
|----------|---------|-------|
| `TELEGRAM_BOT_TOKEN` | ja* | Bot-Token von @BotFather |
| `TELEGRAM_CHAT_ID` | ja* | deine Chat-ID |
| `CALLMEBOT_PHONE` / `CALLMEBOT_APIKEY` | – | Alternativversand über WhatsApp (statt Telegram) |
| `BOT_TZ` | – | Zeitzone (Standard `Europe/Berlin`) |
| `USE_LLM_SUMMARY` | – | `1` aktiviert KI-Zusammenfassung |
| `ANTHROPIC_API_KEY` | bei KI | API-Schlüssel |
| `ANTHROPIC_MODEL` | – | Modellname |
| `EXCLUDE_KEYWORDS` | – | Komma-Liste; Meldungen mit diesen Wörtern im Titel werden verworfen (z. B. `sport,promi`) |
| `QUIET_HOURS` | – | Ruhezeit für Eil-Pings, z. B. `23-7` (nachts still, Meldung kommt im nächsten Digest) |
| `STATE_FILE` | – | Pfad zur Zustandsdatei (Standard: neben dem Skript) |

Die RSS-URLs sind die offiziellen Feeds, können sich aber ändern – dann einfach in `FEEDS` aktualisieren.

## Hinweis zu Financial Times

Die FT-Schlagzeilen sind **auf Englisch** (und die Artikel hinter dem Feed sind kostenpflichtig –
für einen Schlagzeilen-Überblick reicht der Feed aber). Mit aktivierter KI-Zusammenfassung
(`USE_LLM_SUMMARY=1`) werden auch die englischen Titel im deutschen Überblick zusammengefasst.
In der einfachen Listen-Ansicht erscheinen die FT-Titel im Original.

## Weitere Ideen (optional, nicht eingebaut)

- **Versand:** Standard ist Telegram (gratis, zuverlässig). Als Alternative ist WhatsApp über
  CallMeBot eingebaut – das ist aber ein kostenloser Drittdienst mit Rate-Limits und zeitweise
  vollen Bots. Für WhatsApp mit hoher Verlässlichkeit eignet sich Twilio (offiziell, kostenpflichtig);
  der Versand ist in `send_message` leicht austauschbar.
- **Fehler-Benachrichtigung:** GitHub meldet fehlgeschlagene geplante Läufe nicht von selbst –
  in den Repo-Einstellungen lässt sich eine Mail-Benachrichtigung aktivieren.
- **Mehr Quellen/Ressorts:** beliebige weitere RSS-Feeds in `FEEDS` ergänzen (z. B. FT-Ressorts
  wie `https://www.ft.com/markets?format=rss`).
