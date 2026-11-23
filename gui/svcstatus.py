"""Stato dei servizi esterni usati dalle persone configurate (3.28.0): Apple, Google, Microsoft.

Fonti pubbliche, senza chiavi (verificate il 23/11/2026):
  Apple      https://www.apple.com/support/systemstatus/data/system_status_<it_IT|en_US>.js  (JSON: servizi con eventi)
  Google     https://www.google.com/appsstatus/dashboard/incidents.json                    (incidenti di Workspace/Gmail)
  Microsoft  https://status.cloud.microsoft/api/posts/m365Consumer                         (Outlook.com, OneDrive, Microsoft 365 consumer)
Le risposte restano in memoria SVC_TTL secondi; una fonte che non risponde non blocca le altre.
Stati: ok, info (avviso), issue (problema), maint (manutenzione), outage (interruzione), unknown (fonte non raggiungibile).
"""
import calendar, gzip, json, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

SVC_TTL = 300
LINKS = {"apple": {"it": "https://www.apple.com/it/support/systemstatus/", "en": "https://www.apple.com/support/systemstatus/"},
         "google": {"it": "https://www.google.com/appsstatus/dashboard/?hl=it", "en": "https://www.google.com/appsstatus/dashboard/"},
         "microsoft": {"it": "https://status.cloud.microsoft/", "en": "https://status.cloud.microsoft/"}}
NAMES = {"apple": "Apple (iCloud)", "google": "Google", "microsoft": "Microsoft"}
# Servizi che usiamo davvero (Drive, Foto, posta, calendari, contatti, note, promemoria e l'accesso), nelle due lingue di Apple.
APPLE_WATCH = {"Account e Accesso iCloud", "iCloud Account & Sign In", "Apple Account", "iCloud Drive", "Foto", "Photos",
               "Mail di iCloud", "iCloud Mail", "Calendario di iCloud", "iCloud Calendar", "Contatti di iCloud", "iCloud Contacts",
               "Note di iCloud", "iCloud Notes", "Promemoria di iCloud", "iCloud Reminders",
               "Applicazioni web di iCloud (iCloud.com)", "iCloud Web Apps (iCloud.com)"}
GOOGLE_WATCH = ["Gmail", "Google Calendar", "Google Sign In", "Google Drive"]   # i contatti Gmail non hanno una voce a sé
MS_WATCH = ["Outlook.com", "Microsoft 365 (Consumer)", "OneDrive"]
RANK = {"ok": 0, "info": 1, "maint": 2, "issue": 3, "outage": 4, "unknown": 1}
RECENT = 86400   # eventi chiusi da meno di 24 ore: si mostrano come «risolto»

_C = {}; _L = threading.Lock()


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (iCloud Control)", "Accept-Encoding": "gzip"})
    d = urllib.request.urlopen(req, timeout=12).read()
    return json.loads(gzip.decompress(d) if d[:2] == b"\x1f\x8b" else d)


def _norm(s): return " ".join((s or "").replace("\xa0", " ").split())


def apple(lang):
    d = _get(f"https://www.apple.com/support/systemstatus/data/system_status_{'it_IT' if lang == 'it' else 'en_US'}.js")
    now = time.time(); watched, others = [], []
    for s in d.get("services", []):
        name = _norm(s.get("serviceName")); st, msgs = "ok", []
        for e in s.get("events") or []:
            end = (e.get("epochEndDate") or 0) / 1000; typ = (e.get("statusType") or "").lower(); live = e.get("eventStatus") in ("ongoing", "upcoming") or (not end)
            if live:
                k = "maint" if "maint" in typ else "outage" if "outage" in typ else "issue"
                if RANK[k] > RANK[st]: st = k
                msgs.append({"state": k, "text": e.get("message") or "", "since": e.get("startDate") or "", "until": e.get("endDate") or "", "live": True})
            elif now - end < RECENT:
                msgs.append({"state": "ok", "text": e.get("message") or "", "since": e.get("startDate") or "", "until": e.get("endDate") or "", "live": False})
        row = {"name": name, "state": st, "msgs": msgs}
        if name in {_norm(x) for x in APPLE_WATCH}: watched.append(row)
        elif st != "ok": others.append(row)
    return watched, others


def google():
    inc = _get("https://www.google.com/appsstatus/dashboard/incidents.json"); now = time.time()
    rows = {n: {"name": n, "state": "ok", "msgs": []} for n in GOOGLE_WATCH}; others = {}
    imp = {"SERVICE_OUTAGE": "outage", "SERVICE_DISRUPTION": "issue", "SERVICE_INFORMATION": "info"}
    for i in inc:
        end = i.get("end")
        try: end_ts = calendar.timegm(time.strptime(end[:19], "%Y-%m-%dT%H:%M:%S")) if end else None   # orari in UTC
        except ValueError: end_ts = None
        if end and (end_ts is None or now - end_ts > RECENT): continue
        k = imp.get(i.get("status_impact"), "issue") if not end else "ok"
        text = ((i.get("most_recent_update") or {}).get("text") or i.get("external_desc") or "").replace("**", "").strip().split("\n")[0][:300]
        msg = {"state": k, "text": text, "since": (i.get("begin") or "")[:16].replace("T", " "), "until": (end or "")[:16].replace("T", " "), "live": not end,
               "url": "https://www.google.com/appsstatus/dashboard/" + (i.get("uri") or "")}
        for p in i.get("affected_products") or [{"title": i.get("service_name")}]:
            t = p.get("title") or ""
            row = rows.get(t) or others.setdefault(t, {"name": t, "state": "ok", "msgs": []})
            row["msgs"].append(msg)
            if RANK[k] > RANK[row["state"]]: row["state"] = k
    return list(rows.values()), [r for r in others.values() if r["state"] != "ok"]


def microsoft():
    d = _get("https://status.cloud.microsoft/api/posts/m365Consumer"); watched, others = [], []
    for x in d:
        s = x.get("Status") or ""; k = "ok" if s == "Operational" else "outage" if ("Interruption" in s or "Outage" in s) else "maint" if "Maintenance" in s else "issue"
        msgs = [{"state": k, "text": _norm(x.get("Title") or x.get("Message") or s), "since": "", "until": "", "live": True}] if k != "ok" else []
        row = {"name": x.get("ServiceDisplayName") or "", "state": k, "msgs": msgs}
        (watched if row["name"] in MS_WATCH else others).append(row)
    watched.sort(key=lambda r: MS_WATCH.index(r["name"]) if r["name"] in MS_WATCH else 99)
    return watched, [r for r in others if r["state"] != "ok"]


def status(lang="it", force=False):
    """{provider: {"state", "services", "others", "error", "when"}}, in cache SVC_TTL secondi per lingua."""
    with _L:
        hit = _C.get(lang)
        if hit and not force and time.time() - hit["when"] < SVC_TTL: return hit
    jobs = {"apple": lambda: apple(lang), "google": google, "microsoft": microsoft}
    out = {}
    with ThreadPoolExecutor(3) as ex:
        fut = {k: ex.submit(f) for k, f in jobs.items()}
        for k, f in fut.items():
            try:
                w, o = f.result(timeout=20)
                worst = max([r["state"] for r in w] or ["ok"], key=lambda s: RANK[s])
                out[k] = {"state": worst, "services": w, "others": o, "error": ""}
            except Exception as e:
                out[k] = {"state": "unknown", "services": [], "others": [], "error": str(e)[:200]}
    res = {"when": time.time(), "providers": out}
    with _L: _C[lang] = res
    return res
