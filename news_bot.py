#!/usr/bin/env python3
"""
WhatsApp News-Bot
=================
Funktionen:
  * Nachrichten-Digest um 08:00, 14:00 und 20:00 Uhr (NTV, WELT, Tagesschau).
    Eine Meldung, die heute schon in einem Digest war, wird nicht wiederholt.
  * EILMELDUNGEN werden sofort verschickt, sobald sie auftauchen.
  * Tagesübersicht jeden Tag um 21:00 Uhr (Zusammenfassung des Tages).
  * Wochenübersicht sonntags um 21:00 Uhr (Zusammenfassung der Woche).

Zwei Betriebsarten:
  * --daemon : dauerhaft laufender Prozess (z. B. auf einem kleinen Server),
               prüft Eilmeldungen alle paar Minuten und sendet zu den Zeiten.
  * --check  : einmaliger Lauf, entscheidet selbst was gerade fällig ist.
               Ideal für serverloses cron (z. B. GitHub Actions alle 5 Min).

Versand über Telegram (kostenlos, zuverlässig). WhatsApp über CallMeBot ist als
Alternative eingebaut. Optional KI-Zusammenfassung über die Anthropic-API (USE_LLM_SUMMARY=1).
"""

import os
import re
import sys
import json
import html
import time
import argparse
import datetime as dt
from difflib import SequenceMatcher

import requests
import feedparser

# ----------------------------------------------------------------------------
# Zeitzone festnageln (wichtig, da z. B. GitHub-Actions in UTC läuft)
# ----------------------------------------------------------------------------
BOT_TZ = os.environ.get("BOT_TZ", "Europe/Berlin")
if hasattr(time, "tzset"):
    os.environ["TZ"] = BOT_TZ
    time.tzset()

# ----------------------------------------------------------------------------
# Konfiguration
# ----------------------------------------------------------------------------
FEEDS = {
    "Tagesschau":      "https://www.tagesschau.de/index~rss2.xml",
    "ntv":             "https://www.n-tv.de/rss",
    "WELT":            "https://www.welt.de/feeds/latest.rss",
    "Financial Times": "https://www.ft.com/rss/home",
}

# Wörter, die eine Eilmeldung kennzeichnen (klein geschrieben prüfen)
BREAKING_KEYWORDS = ["eilmeldung", "eil:", "breaking", "+++", "live:"]

DIGEST_TIMES = {"morning": "08:00", "noon": "14:00", "evening": "20:00"}
DAILY_TIME   = "21:00"          # Tagesübersicht jeden Tag
WEEKLY_TIME  = "21:00"          # Wochenübersicht sonntags
BREAKING_POLL_MINUTES = 5       # nur im --daemon-Modus
GRACE_HOURS  = 3                # im --check-Modus: wie lange ein verpasster Slot noch nachgeholt wird

MAX_PER_SOURCE       = 8
MAX_ITEMS_PER_RUN    = 18
MAX_BREAKING_PER_RUN = 3        # nicht mehr Eilmeldungen auf einmal (Flut vermeiden)
SIMILARITY_THRESHOLD = 0.72
WHATSAPP_CHUNK_CHARS = 3500
RECAP_LIST_MAX       = 40       # Obergrenze für die Fallback-Liste in Tages-/Wochenübersicht
LLM_INPUT_MAX        = 120      # max. Meldungen, die an die KI gehen
HISTORY_KEEP_DAYS    = 8
FETCH_TIMEOUT        = 15       # Sekunden pro Feed (verhindert Hänger)

INCLUDE_LINKS = os.environ.get("INCLUDE_LINKS", "1") == "1"

# Optionaler Themenfilter: Meldungen mit einem dieser Wörter im Titel werden verworfen.
# z. B. EXCLUDE_KEYWORDS="sport,bundesliga,promi"
EXCLUDE_KEYWORDS = [w.strip().lower() for w in os.environ.get("EXCLUDE_KEYWORDS", "").split(",") if w.strip()]

# Optionale Ruhezeit für Eilmeldungen, z. B. "23-7" = nachts keine Eil-Pings
# (die Meldungen kommen dann im nächsten Digest mit). Leer = aus.
QUIET_HOURS = os.environ.get("QUIET_HOURS", "").strip()

STATE_FILE = os.environ.get(
    "STATE_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json"),
)

# --- WhatsApp via CallMeBot ---
# --- WhatsApp via CallMeBot (optionaler Alternativ-Versand) ---
CALLMEBOT_PHONE  = os.environ.get("CALLMEBOT_PHONE", "")
CALLMEBOT_APIKEY = os.environ.get("CALLMEBOT_APIKEY", "")

# --- Telegram (Standard-Versand) ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# --- Optionale KI-Zusammenfassung ---
USE_LLM_SUMMARY   = os.environ.get("USE_LLM_SUMMARY", "0") == "1"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL   = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")


# ----------------------------------------------------------------------------
# Hilfsfunktionen
# ----------------------------------------------------------------------------
def log(msg):
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", file=sys.stderr)


def clean(text):
    text = re.sub(r"<[^>]+>", "", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def norm_title(title):
    return re.sub(r"[^a-z0-9äöüß ]", "", title.lower()).strip()


def entry_id(entry):
    return entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title", "")


def is_similar(a, b):
    return SequenceMatcher(None, a, b).ratio() >= SIMILARITY_THRESHOLD


def parse_ts(s):
    return dt.datetime.fromisoformat(s)


# ----------------------------------------------------------------------------
# Zustand laden / speichern
# ----------------------------------------------------------------------------
def default_state():
    return {"history": [], "breaking_seen": [], "last_run": {}}


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            for k, v in default_state().items():
                data.setdefault(k, v)
            return data
        except Exception as e:
            log(f"Konnte {STATE_FILE} nicht lesen ({e}) – starte mit leerem Zustand.")
    return default_state()


def save_state(state):
    # alte Einträge ausmisten
    cutoff = dt.datetime.now() - dt.timedelta(days=HISTORY_KEEP_DAYS)
    state["history"] = [h for h in state["history"] if parse_ts(h["ts"]) >= cutoff][-1000:]
    state["breaking_seen"] = state["breaking_seen"][-500:]
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def add_to_history(state, it):
    state["history"].append({
        "id":      it["id"],
        "ntitle":  it.get("ntitle") or norm_title(it["title"]),
        "title":   it["title"],
        "summary": it.get("summary", ""),
        "source":  it["source"],
        "ts":      dt.datetime.now().isoformat(timespec="seconds"),
    })


def todays_items(state):
    today = dt.datetime.now().date()
    return [h for h in state["history"] if parse_ts(h["ts"]).date() == today]


def week_items(state):
    now = dt.datetime.now()
    start = (now - dt.timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return [h for h in state["history"] if parse_ts(h["ts"]) >= start]


# ----------------------------------------------------------------------------
# Feeds holen
# ----------------------------------------------------------------------------
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


def _load_feed(url):
    """Feed über requests mit Timeout holen (kein Hänger) und parsen."""
    try:
        r = requests.get(url, headers=HTTP_HEADERS, timeout=FETCH_TIMEOUT)
        r.raise_for_status()
        return feedparser.parse(r.content)
    except Exception as e:
        log(f"Feed nicht erreichbar: {url} ({e})")
        return None


def fetch_items():
    items = []
    for source, url in FEEDS.items():
        feed = _load_feed(url)
        if feed is None or not getattr(feed, "entries", None):
            continue
        count = 0
        for e in feed.entries:
            if count >= MAX_PER_SOURCE:
                break
            title = clean(e.get("title", ""))
            if not title:
                continue
            if EXCLUDE_KEYWORDS and any(k in title.lower() for k in EXCLUDE_KEYWORDS):
                continue
            items.append({
                "source":  source,
                "id":      entry_id(e),
                "title":   title,
                "ntitle":  norm_title(title),
                "summary": clean(e.get("summary", ""))[:400],
                "link":    e.get("link", ""),
            })
            count += 1
    return items


# ----------------------------------------------------------------------------
# Deduplizierung
# ----------------------------------------------------------------------------
def already_known(it, ref_items):
    """True, wenn it per ID oder ähnlichem Titel schon in ref_items steckt."""
    if any(it["id"] == r["id"] for r in ref_items):
        return True
    return any(is_similar(it["ntitle"], r["ntitle"]) for r in ref_items)


def dedupe_within_batch(items):
    result = []
    for it in items:
        if any(is_similar(it["ntitle"], r["ntitle"]) for r in result):
            continue
        result.append(it)
    return result


def is_breaking(it):
    t = it["title"].lower()
    return any(k in t for k in BREAKING_KEYWORDS)


def in_quiet_hours(now=None):
    """True, wenn die aktuelle Stunde in der konfigurierten Eil-Ruhezeit liegt."""
    if not QUIET_HOURS:
        return False
    try:
        start, end = (int(x) for x in QUIET_HOURS.split("-"))
    except Exception:
        return False
    h = (now or dt.datetime.now()).hour
    if start == end:
        return False
    if start < end:
        return start <= h < end
    return h >= start or h < end       # Zeitraum über Mitternacht


# ----------------------------------------------------------------------------
# Nachricht zusammenbauen
# ----------------------------------------------------------------------------
def use_html():
    """Telegram -> hübsches HTML-Format; sonst einfacher Text."""
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def esc(s):
    """Für HTML-Versand: nur die drei Sonderzeichen maskieren."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def short_summary(text, limit=300):
    """Kurzbeschreibung sauber am Wortende kürzen."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


def header_line(title):
    return f"{title} ({dt.datetime.now():%d.%m.%Y})"


def build_list(items, title, show_links=True, max_items=None):
    if max_items:
        items = items[:max_items]
    by_source = {}
    for it in items:
        by_source.setdefault(it["source"], []).append(it)

    if use_html():
        parts = [f"<b>{esc(header_line(title))}</b>"]
        for source, group in by_source.items():
            parts.append(f"\n<b>━━ {esc(source)} ━━</b>")
            for it in group:
                parts.append(f"\n<b>{esc(it['title'])}</b>")
                summ = short_summary(it.get("summary", ""))
                if summ:
                    parts.append(esc(summ))
                if show_links and it.get("link"):
                    parts.append(f'<a href="{esc(it["link"])}">→ Weiterlesen</a>')
        return "\n".join(parts).strip()

    # einfacher Text (Fallback, z. B. CallMeBot)
    lines = [header_line(title), ""]
    for source, group in by_source.items():
        lines.append(f"*{source}*")
        for it in group:
            lines.append(f"• {it['title']}")
            summ = short_summary(it.get("summary", ""))
            if summ:
                lines.append(f"  {summ}")
            if show_links and it.get("link"):
                lines.append(f"  {it['link']}")
        lines.append("")
    return "\n".join(lines).strip()


def llm_summary(items, kind):
    """KI-Zusammenfassung; gibt Text zurück oder None bei Fehler/deaktiviert."""
    if not (USE_LLM_SUMMARY and ANTHROPIC_API_KEY):
        return None
    instructions = {
        "digest": "Fasse die folgenden Schlagzeilen zu einem kompakten, neutralen Überblick zusammen.",
        "tag":    "Schreibe einen kompakten Tagesrückblick: Was ist heute passiert?",
        "woche":  "Schreibe einen Wochenrückblick mit den wichtigsten Themen der Woche.",
    }[kind]
    headlines = "\n".join(
        f"- [{it['source']}] {it['title']}. {it.get('summary', '')}" for it in items[:LLM_INPUT_MAX]
    )
    prompt = (
        f"{instructions} Gruppiere thematisch, maximal 8 Punkte, je 1–2 Sätze, "
        f"sachlich und neutral, auf Deutsch. Keine Einleitung, keine Wertung.\n\n{headlines}"
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        body = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return body.strip() or None
    except Exception as e:
        log(f"KI-Zusammenfassung fehlgeschlagen, nutze Liste: {e}")
        return None


def compose(items, title, kind, show_links):
    body = llm_summary(items, kind)
    if body:
        if use_html():
            return f"<b>{esc(header_line(title))}</b>\n\n{esc(body)}"
        return f"{header_line(title)}\n\n{body}"
    return build_list(items, title, show_links=show_links, max_items=RECAP_LIST_MAX)


# ----------------------------------------------------------------------------
# Versand (Standard: Telegram; alternativ: WhatsApp über CallMeBot)
# ----------------------------------------------------------------------------
def chunk_text(text, limit):
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit and current:
            chunks.append(current.rstrip())
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


def _send_telegram_one(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True}
    if use_html():
        payload["parse_mode"] = "HTML"
    r = requests.post(url, json=payload, timeout=30)
    if r.status_code == 200:
        return True
    # Falls die HTML-Formatierung mal stört: ohne Formatierung erneut senden,
    # damit die Nachricht trotzdem ankommt.
    if use_html() and r.status_code == 400:
        plain = html.unescape(re.sub(r"<[^>]+>", "", text))
        r2 = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": plain, "disable_web_page_preview": True},
            timeout=30,
        )
        if r2.status_code == 200:
            return True
        r = r2
    log(f"Telegram-Versand fehlgeschlagen: HTTP {r.status_code} – {r.text[:200]}")
    return False


def _send_callmebot_one(text):
    r = requests.get(
        "https://api.callmebot.com/whatsapp.php",
        params={"phone": CALLMEBOT_PHONE, "text": text, "apikey": CALLMEBOT_APIKEY},
        timeout=30,
    )
    if r.status_code == 200:
        return True
    log(f"WhatsApp-Versand fehlgeschlagen: HTTP {r.status_code} – {r.text[:200]}")
    return False


def send_message(message):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        sender = _send_telegram_one
    elif CALLMEBOT_PHONE and CALLMEBOT_APIKEY:
        sender = _send_callmebot_one
    else:
        log("Kein Versand konfiguriert (Telegram oder CallMeBot) – Vorschau:")
        print("\n----- Nachrichtenvorschau -----\n" + message + "\n-------------------------------\n")
        return False
    ok = True
    chunks = chunk_text(message, WHATSAPP_CHUNK_CHARS)
    for i, c in enumerate(chunks):
        if len(chunks) > 1:
            c = f"({i + 1}/{len(chunks)})\n{c}"
        if not sender(c):
            ok = False
        time.sleep(1)
    return ok


# ----------------------------------------------------------------------------
# Aufgaben (jede gibt True zurück, wenn "erledigt", False nur bei Sendefehler)
# ----------------------------------------------------------------------------
def task_breaking(state):
    items = fetch_items()
    if not items:
        return True
    seen = set(state["breaking_seen"])
    today = todays_items(state)
    new = [it for it in items
           if is_breaking(it) and it["id"] not in seen and not already_known(it, today)]
    new = dedupe_within_batch(new)
    if not new:
        return True
    if in_quiet_hours():
        # Nachts nicht pingen: als gesehen markieren, damit sie im nächsten Digest mitlaufen
        for it in new:
            seen.add(it["id"])
        state["breaking_seen"] = list(seen)
        log(f"Ruhezeit aktiv – {len(new)} Eilmeldung(en) folgen im nächsten Digest.")
        return True
    new = new[:MAX_BREAKING_PER_RUN]
    ok = True
    for it in new:
        summ = short_summary(it.get("summary", ""))
        if use_html():
            msg = f"🚨 <b>EILMELDUNG</b> · {esc(it['source'])}\n\n<b>{esc(it['title'])}</b>"
            if summ:
                msg += f"\n{esc(summ)}"
            if INCLUDE_LINKS and it.get("link"):
                msg += f'\n<a href="{esc(it["link"])}">→ Weiterlesen</a>'
        else:
            msg = f"🚨 *EILMELDUNG* · {it['source']}\n\n{it['title']}"
            if summ:
                msg += f"\n\n{summ}"
            if INCLUDE_LINKS and it.get("link"):
                msg += f"\n{it['link']}"
        if send_message(msg):
            add_to_history(state, it)
            seen.add(it["id"])
            log(f"Eilmeldung verschickt: {it['title'][:70]}")
        else:
            ok = False
    state["breaking_seen"] = list(seen)
    return ok


def task_digest(state, key):
    label = {"morning": "Morgens", "noon": "Mittags", "evening": "Abends"}[key]
    items = fetch_items()
    if not items:
        log("Digest: keine Meldungen abrufbar.")
        return True
    today = todays_items(state)
    fresh = [it for it in items if not already_known(it, today)]
    fresh = dedupe_within_batch(fresh)[:MAX_ITEMS_PER_RUN]
    if not fresh:
        log(f"Digest {label}: nichts Neues seit dem letzten Lauf.")
        return True
    if USE_LLM_SUMMARY and ANTHROPIC_API_KEY:
        msg = compose(fresh, f"📰 Nachrichten – {label}", "digest", INCLUDE_LINKS)
    else:
        msg = build_list(fresh, f"📰 Nachrichten – {label}", show_links=INCLUDE_LINKS)
    if send_message(msg):
        for it in fresh:
            add_to_history(state, it)
        log(f"Digest {label}: {len(fresh)} Meldungen verschickt.")
        return True
    return False


def task_daily(state):
    items = todays_items(state)
    if not items:
        log("Tagesübersicht: heute keine Meldungen erfasst.")
        return True
    msg = compose(items, "🌙 Tagesübersicht", "tag", show_links=False)
    return send_message(msg)


def task_weekly(state):
    items = week_items(state)
    if not items:
        log("Wochenübersicht: keine Meldungen erfasst.")
        return True
    title = f"📅 Wochenübersicht (KW {dt.datetime.now():%V})"
    msg = compose(items, title, "woche", show_links=False)
    return send_message(msg)


# ----------------------------------------------------------------------------
# --check : serverloser Einzellauf (entscheidet selbst, was fällig ist)
# ----------------------------------------------------------------------------
def _due_and_run(state, key, time_str, period_id, fn):
    """Führt fn aus, wenn der Slot heute/diese-Periode fällig und noch nicht erledigt ist."""
    if state["last_run"].get(key) == period_id:
        return
    now = dt.datetime.now()
    h, m = map(int, time_str.split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if now < target:
        return                                  # noch nicht so weit
    if now > target + dt.timedelta(hours=GRACE_HOURS):
        state["last_run"][key] = period_id      # verpasst -> stumm als erledigt markieren
        log(f"Slot '{key}' verpasst (außerhalb der Toleranz) – übersprungen.")
        return
    if fn():
        state["last_run"][key] = period_id


def run_check(state):
    task_breaking(state)                        # Eilmeldungen immer prüfen
    now = dt.datetime.now()
    today = now.date().isoformat()
    week  = now.strftime("%G-W%V")
    for key, t in DIGEST_TIMES.items():
        _due_and_run(state, f"digest_{key}", t, today, lambda k=key: task_digest(state, k))
    _due_and_run(state, "daily", DAILY_TIME, today, lambda: task_daily(state))
    if now.weekday() == 6:                      # Sonntag
        _due_and_run(state, "weekly", WEEKLY_TIME, week, lambda: task_weekly(state))


# ----------------------------------------------------------------------------
# --daemon : dauerhaft laufender Prozess
# ----------------------------------------------------------------------------
def run_daemon():
    import schedule

    def wrap(fn):
        st = load_state()
        try:
            fn(st)
        finally:
            save_state(st)

    schedule.every(BREAKING_POLL_MINUTES).minutes.do(lambda: wrap(task_breaking))
    for k, t in DIGEST_TIMES.items():
        schedule.every().day.at(t).do(lambda k=k: wrap(lambda s: task_digest(s, k)))
    schedule.every().day.at(DAILY_TIME).do(lambda: wrap(task_daily))
    schedule.every().sunday.at(WEEKLY_TIME).do(lambda: wrap(task_weekly))

    log(f"Daemon läuft (TZ={BOT_TZ}). Digests 08/14/20, Tagesübersicht 21:00, "
        f"Wochenübersicht So 21:00, Eilmeldungen alle {BREAKING_POLL_MINUTES} Min.")
    wrap(task_breaking)                          # einmal direkt zu Beginn
    while True:
        schedule.run_pending()
        time.sleep(15)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="WhatsApp News-Bot")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--daemon", action="store_true", help="dauerhaft laufen (eigener Server)")
    g.add_argument("--check", action="store_true", help="einmaliger Lauf für serverloses cron (Standard)")
    g.add_argument("--digest", choices=list(DIGEST_TIMES), help="einen Digest sofort senden (Test)")
    g.add_argument("--daily", action="store_true", help="Tagesübersicht sofort senden (Test)")
    g.add_argument("--weekly", action="store_true", help="Wochenübersicht sofort senden (Test)")
    g.add_argument("--breaking", action="store_true", help="nur auf Eilmeldungen prüfen (Test)")
    args = p.parse_args()

    if args.daemon:
        run_daemon()
        return

    state = load_state()
    try:
        if args.digest:
            task_digest(state, args.digest)
        elif args.daily:
            task_daily(state)
        elif args.weekly:
            task_weekly(state)
        elif args.breaking:
            task_breaking(state)
        else:                                    # Standard: --check
            run_check(state)
    finally:
        save_state(state)


if __name__ == "__main__":
    main()
