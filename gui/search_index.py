"""Indice di ricerca di iCloud Dashboard (dalla 3.6): file, note, promemoria, posta, eventi e contatti di ogni persona.

SQLite in /app/state/iclouddb.db con FTS5 (tokenizer unicode61 senza accenti). Una riga per elemento in `items`, il testo in
`fts` (rowid = items.id). Ogni giro rilegge solo ciò che è cambiato (percorso + mtime + dimensione) e toglie ciò che non
c'è più. Le sorgenti sono le cartelle che i container iclouddr scrivono sul NAS, montate qui in sola lettura:
  file       <persona>/iCloud Drive/**            nome, cartella, tipo, dimensione, data
  foto       media/photos/<persona>/<album>/**    nome, album, tipo, dimensione, data (3.9)
  note       <persona>/iCloud/Note/**/*.html      titolo e testo
  promemoria <persona>/iCloud/Promemoria/*.json   titolo, note, lista, scadenza, fatto
  posta      <persona>/<account>/Posta (Maildir)  da, a, cc, oggetto, data, testo, allegati
  eventi     <persona>/<account>/Calendario/**/*.ics  titolo, luogo, descrizione, inizio, fine, coordinate
  contatti   <persona>/<account>/Contatti/**/*.vcf    nome, e-mail, telefoni, organizzazione
"""
import datetime, email, email.policy, html, json, os, re, sqlite3, threading, time

DB = os.path.join(os.environ.get("APP_DIR", "/app"), "state", "iclouddb.db")   # «iclouddb»: un file, nessun container a parte
_lock = threading.Lock()
KINDS = ("file", "photo", "note", "reminder", "mail", "event", "contact")
MAX_BODY = 20000


def connect():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("pragma journal_mode=wal")
    c.executescript("""
    create table if not exists items(id integer primary key, kind text, person text, account text, path text unique,
        title text, ext text, size integer, mtime real, date real, date_end real, extra text);
    create index if not exists items_kind on items(kind, person);
    create index if not exists items_date on items(kind, date);
    create virtual table if not exists fts using fts5(title, body, meta, tokenize='unicode61 remove_diacritics 2');
    create table if not exists runs(source text primary key, finished real, count integer, seconds real, error text);
    """)
    # foto (3.9): coordinate e data di scatto lette dall'EXIF in background (geo: null = da leggere, 1 = letto)
    cols = {r[1] for r in c.execute("pragma table_info(items)")}
    for col, typ in (("lat", "real"), ("lon", "real"), ("geo", "integer")):
        if col not in cols:
            c.execute(f"alter table items add column {col} {typ}")
    c.execute("create index if not exists items_geo on items(kind, geo)")
    return c


def _strip_html(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s or "")
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _upsert(c, row, body="", meta=""):
    cur = c.execute("select id from items where path=?", (row["path"],)).fetchone()
    cols = ("kind", "person", "account", "path", "title", "ext", "size", "mtime", "date", "date_end", "extra")
    vals = [row.get(k) for k in cols]
    if cur:
        c.execute(f"update items set {', '.join(k + '=?' for k in cols)}, geo=null where id=?", vals + [cur["id"]])
        c.execute("delete from fts where rowid=?", (cur["id"],))
        rid = cur["id"]
    else:
        rid = c.execute(f"insert into items({', '.join(cols)}) values({', '.join('?' * len(cols))})", vals).lastrowid
    c.execute("insert into fts(rowid, title, body, meta) values(?,?,?,?)", (rid, row.get("title") or "", body[:MAX_BODY], meta))


def _prune(c, kind, person, seen, prefix=None):
    q = "select id, path from items where kind=? and person=?"
    for r in c.execute(q, (kind, person)).fetchall():
        if r["path"] not in seen and (prefix is None or r["path"].startswith(prefix)):
            c.execute("delete from items where id=?", (r["id"],))
            c.execute("delete from fts where rowid=?", (r["id"],))


def _known(c, kind, person):
    return {r["path"]: (r["mtime"], r["size"]) for r in c.execute("select path, mtime, size from items where kind=? and person=?", (kind, person))}


def _walk(root):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not d.startswith(".")]
        for f in fns:
            if not f.startswith(".") and not f.endswith((".tmp", ".partial")):
                yield os.path.join(dp, f)


# ---------------------------------------------------------------------------------------------------- sorgenti
def index_files(c, person, local_root, host_root):
    known, seen, n = _known(c, "file", person), set(), 0
    for p in _walk(local_root):
        try:
            st = os.stat(p)
        except OSError:
            continue
        key = host_root + p[len(local_root):]
        seen.add(key)
        if known.get(key) == (st.st_mtime, st.st_size):
            continue
        rel = p[len(local_root):].lstrip("/")
        name = os.path.basename(p)
        ext = os.path.splitext(name)[1].lower().lstrip(".")
        _upsert(c, {"kind": "file", "person": person, "account": "iCloud Drive", "path": key, "title": name, "ext": ext,
                    "size": st.st_size, "mtime": st.st_mtime, "date": st.st_mtime, "extra": json.dumps({"rel": rel, "local": p})},
                body=rel.replace("/", " "), meta=ext)
        n += 1
    _prune(c, "file", person, seen)
    return len(seen)


def index_photos(c, person, album, local_root, host_root):
    """Foto e video scaricati da iclouddr (media/photos/<persona>/<album>): nome, cartella, tipo, dimensione, data del file."""
    known, seen = _known(c, "photo", person), set()
    for p in _walk(local_root):
        try:
            st = os.stat(p)
        except OSError:
            continue
        key = host_root + p[len(local_root):]
        seen.add(key)
        if known.get(key) == (st.st_mtime, st.st_size):
            continue
        rel = p[len(local_root):].lstrip("/")
        name = os.path.basename(p)
        ext = os.path.splitext(name)[1].lower().lstrip(".")
        _upsert(c, {"kind": "photo", "person": person, "account": album, "path": key, "title": name, "ext": ext,
                    "size": st.st_size, "mtime": st.st_mtime, "date": st.st_mtime, "extra": json.dumps({"rel": rel, "local": p, "album": album})},
                body=rel.replace("/", " "), meta=f"{ext} {album}")
    _prune(c, "photo", person, seen, host_root)
    return len(seen)


def index_notes(c, person, note_root, host_root):
    known, seen = _known(c, "note", person), set()
    titles = {}
    try:
        for e in json.load(open(os.path.join(note_root, "indice.json"))).get("note", []):
            if e.get("file"):
                titles[e["file"]] = e
    except (FileNotFoundError, ValueError):
        pass
    for p in _walk(note_root):
        if not p.endswith(".html"):
            continue
        st = os.stat(p)
        key = host_root + p[len(note_root):]
        seen.add(key)
        if known.get(key) == (st.st_mtime, st.st_size):
            continue
        rel = p[len(note_root):].lstrip("/")
        meta = titles.get(rel, {})
        text = _strip_html(open(p, encoding="utf-8", errors="replace").read())
        folder = rel.split("/")[0] if "/" in rel else ""
        _upsert(c, {"kind": "note", "person": person, "account": "iCloud", "path": key,
                    "title": meta.get("title") or os.path.splitext(os.path.basename(p))[0], "ext": "html", "size": st.st_size,
                    "mtime": st.st_mtime, "date": _ts(meta.get("modified")) or st.st_mtime, "extra": json.dumps({"folder": folder, "local": p, "rel": rel})},
                body=text, meta=folder)
    _prune(c, "note", person, seen)
    return len(seen)


def _ts(v):
    if not v:
        return None
    try:
        if isinstance(v, (int, float)):
            return v / 1000 if v > 1e11 else v
        return datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def index_reminders(c, person, rem_root, host_root):
    known, seen = _known(c, "reminder", person), set()
    for p in _walk(rem_root):
        if not p.endswith(".json"):
            continue
        st = os.stat(p)
        try:
            d = json.load(open(p))
        except ValueError:
            continue
        lst = d.get("lista") or os.path.splitext(os.path.basename(p))[0]
        for r in d.get("promemoria", []):
            key = f"{host_root}/{lst}#{r.get('id')}"
            seen.add(key)
            if known.get(key) == (st.st_mtime, st.st_size):
                continue
            _upsert(c, {"kind": "reminder", "person": person, "account": "iCloud", "path": key, "title": r.get("title") or "",
                        "ext": "", "size": st.st_size, "mtime": st.st_mtime, "date": _ts(r.get("due_date")),
                        "extra": json.dumps({"list": lst, "completed": bool(r.get("completed")), "desc": r.get("desc") or "",
                                             "priority": r.get("priority"), "flagged": r.get("flagged"), "tags": [t.get("name") for t in r.get("tag", []) if isinstance(t, dict)]}, ensure_ascii=False)},
                    body=(r.get("desc") or ""), meta=lst)
    _prune(c, "reminder", person, seen)
    return len(seen)


def _addr(v):
    return ", ".join(f"{n} <{a}>" if n else a for n, a in email.utils.getaddresses([str(v or "")]))


def _att_size(part):
    """Dimensione dell'allegato stimata dal testo codificato (base64 = 3/4): decodificarlo costa molto su gigabyte di posta."""
    raw = part.get_payload()
    if not isinstance(raw, str): return 0
    return len(raw) * 3 // 4 if (part.get("Content-Transfer-Encoding") or "").lower().strip() == "base64" else len(raw)


def index_mail(c, person, account, mail_root, host_root):
    known, seen = _known(c, "mail", person), set()
    prefix = host_root; done = 0
    for p in _walk(mail_root):
        if "/cur/" not in p and "/new/" not in p:
            continue
        st = os.stat(p)
        key = host_root + p[len(mail_root):]
        seen.add(key)
        if known.get(key) == (st.st_mtime, st.st_size):
            continue
        try:
            msg = email.message_from_binary_file(open(p, "rb"), policy=email.policy.default)
        except Exception:
            continue
        folder = p[len(mail_root):].lstrip("/").split("/cur/")[0].split("/new/")[0]
        atts, text = [], ""
        for part in msg.walk():
            fn = part.get_filename()
            if fn:
                atts.append({"name": fn, "type": part.get_content_type(), "size": _att_size(part)})
            elif part.get_content_type() == "text/plain" and not text:
                try: text = part.get_content()
                except Exception: pass
        if not text:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    try: text = _strip_html(part.get_content()); break
                    except Exception: pass
        try:
            date = email.utils.parsedate_to_datetime(msg.get("Date")).timestamp()
        except Exception:
            date = st.st_mtime
        frm, to, cc, subj = _addr(msg.get("From")), _addr(msg.get("To")), _addr(msg.get("Cc")), str(msg.get("Subject") or "")
        _upsert(c, {"kind": "mail", "person": person, "account": account, "path": key, "title": subj, "ext": "eml",
                    "size": st.st_size, "mtime": st.st_mtime, "date": date,
                    "extra": json.dumps({"from": frm, "to": to, "cc": cc, "folder": folder, "atts": atts, "local": p}, ensure_ascii=False)},
                body=text, meta=f"{frm} {to} {cc} {' '.join(a['name'] for a in atts)} {folder}")
        done += 1
        if done % 2000 == 0:   # salva ogni 2000 messaggi: se il container si riavvia, il giro dopo riparte da qui
            c.commit()
    _prune(c, "mail", person, seen, prefix)
    return len(seen)


def _ics_props(block):
    lines = re.sub(r"\r?\n[ \t]", "", block).splitlines()
    out = {}
    for ln in lines:
        if ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        name, _, params = k.partition(";")
        out.setdefault(name.upper(), (params, v))
    return out


def _ics_dt(pv):
    if not pv:
        return None, False
    params, v = pv
    v = v.strip()
    try:
        if re.fullmatch(r"\d{8}", v):
            return datetime.datetime.strptime(v, "%Y%m%d").timestamp(), True
        if v.endswith("Z"):
            return datetime.datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc).timestamp(), False
        return datetime.datetime.strptime(v[:15], "%Y%m%dT%H%M%S").timestamp(), False
    except ValueError:
        return None, False


def _unesc(v):
    return (v or "").replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def index_events(c, person, account, cal_root, host_root):
    known, seen = _known(c, "event", person), set()
    for p in _walk(cal_root):
        if not p.endswith(".ics"):
            continue
        st = os.stat(p)
        rel = p[len(cal_root):].lstrip("/")
        calname = rel.split("/")[0] if "/" in rel else os.path.splitext(rel)[0]
        try:
            disp = open(os.path.join(cal_root, calname, "displayname")).read().strip()
        except OSError:
            disp = calname
        raw = open(p, encoding="utf-8", errors="replace").read()
        for i, ev in enumerate(re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", raw, re.S)):
            pr = _ics_props(ev)
            uid = (pr.get("UID") or ("", f"{rel}-{i}"))[1].strip()
            key = f"{host_root}/{rel}#{uid}#{(pr.get('RECURRENCE-ID') or ('', ''))[1]}"
            seen.add(key)
            if known.get(key) == (st.st_mtime, st.st_size):
                continue
            start, allday = _ics_dt(pr.get("DTSTART"))
            end, _ = _ics_dt(pr.get("DTEND"))
            geo = None
            if pr.get("GEO"):
                try: geo = [float(x) for x in pr["GEO"][1].split(";")[:2]]
                except ValueError: pass
            sl = pr.get("X-APPLE-STRUCTURED-LOCATION")
            if not geo and sl and "geo:" in sl[1]:
                try: geo = [float(x) for x in sl[1].split("geo:")[1].split(",")[:2]]
                except ValueError: pass
            title, loc, desc = _unesc(pr.get("SUMMARY", ("", ""))[1]), _unesc(pr.get("LOCATION", ("", ""))[1]), _unesc(pr.get("DESCRIPTION", ("", ""))[1])
            _upsert(c, {"kind": "event", "person": person, "account": account, "path": key, "title": title, "ext": "ics",
                        "size": st.st_size, "mtime": st.st_mtime, "date": start, "date_end": end,
                        "extra": json.dumps({"calendar": disp, "cal_id": f"{person}/{account}/{calname}", "location": loc, "desc": desc[:4000],
                                             "allday": allday, "geo": geo, "rrule": (pr.get("RRULE") or ("", ""))[1], "url": (pr.get("URL") or ("", ""))[1],
                                             "organizer": (pr.get("ORGANIZER") or ("", ""))[1].replace("mailto:", ""), "local": p}, ensure_ascii=False)},
                    body=f"{loc} {desc}", meta=disp)
    _prune(c, "event", person, seen, host_root)
    return len(seen)


def index_contacts(c, person, account, card_root, host_root):
    known, seen = _known(c, "contact", person), set()
    for p in _walk(card_root):
        if not p.endswith(".vcf"):
            continue
        st = os.stat(p)
        key = host_root + p[len(card_root):]
        seen.add(key)
        if known.get(key) == (st.st_mtime, st.st_size):
            continue
        raw = re.sub(r"\r?\n[ \t]", "", open(p, encoding="utf-8", errors="replace").read())
        def all_(name):
            return [_unesc(m.group(1)).strip() for m in re.finditer(rf"(?im)^(?:item\d+\.)?{name}(?:;[^:]*)?:(.*)$", raw)]
        fn = (all_("FN") or [""])[0]
        emails = [e.lower() for e in all_("EMAIL") if e]
        tels = [re.sub(r"[^\d+]", "", t) for t in all_("TEL") if t]
        org = (all_("ORG") or [""])[0].replace(";", " ").strip()
        _upsert(c, {"kind": "contact", "person": person, "account": account, "path": key, "title": fn or (emails or tels or ["?"])[0],
                    "ext": "vcf", "size": st.st_size, "mtime": st.st_mtime, "date": None,
                    "extra": json.dumps({"emails": emails, "tels": tels, "org": org, "adr": [a.replace(";", " ").strip() for a in all_("ADR")],
                                         "note": (all_("NOTE") or [""])[0], "local": p}, ensure_ascii=False)},
                body=" ".join(emails + tels + [org]), meta=org)
    _prune(c, "contact", person, seen, host_root)
    return len(seen)


# ---------------------------------------------------------------------------------------------------- giro completo
def run(sources, log=print):
    """sources: lista di dict {person, drive:(local, host), person_dir:(local, host), accounts:[label…]} (vedi app.py)."""
    if not _lock.acquire(blocking=False):
        return False
    try:
        c = connect()
        for s in sources:
            person = s["person"]
            jobs = []
            if s.get("drive"):
                jobs.append(("file", lambda s=s: index_files(c, person, *s["drive"])))
            for album, pl_, ph_ in s.get("photos", []):
                if os.path.isdir(pl_):
                    jobs.append(("photo", lambda album=album, pl_=pl_, ph_=ph_: index_photos(c, person, album, pl_, ph_)))
            pl, ph = s.get("person_dir") or (None, None)
            if pl and os.path.isdir(pl):
                if os.path.isdir(os.path.join(pl, "iCloud", "Note")):
                    jobs.append(("note", lambda: index_notes(c, person, os.path.join(pl, "iCloud", "Note"), ph + "/iCloud/Note")))
                if os.path.isdir(os.path.join(pl, "iCloud", "Promemoria")):
                    jobs.append(("reminder", lambda: index_reminders(c, person, os.path.join(pl, "iCloud", "Promemoria"), ph + "/iCloud/Promemoria")))
                for acc in s.get("accounts", []):
                    for sub, kind, fn in (("Posta", "mail", index_mail), ("Calendario", "event", index_events), ("Contatti", "contact", index_contacts)):
                        d = os.path.join(pl, acc, sub)
                        if os.path.isdir(d):
                            jobs.append((kind, lambda d=d, acc=acc, sub=sub, fn=fn: fn(c, person, acc, d, f"{ph}/{acc}/{sub}")))
            for kind, job in jobs:
                t = time.time(); err = None; n = 0
                try:
                    n = job(); c.commit()
                except Exception as e:
                    err = f"{type(e).__name__}: {str(e)[:200]}"; c.rollback(); log(f"indice {person} {kind}: {err}")
                c.execute("insert or replace into runs values(?,?,?,?,?)", (f"{person}:{kind}", time.time(), n, round(time.time() - t, 1), err))
                c.commit()
            # posta, eventi e contatti rimasti da una cartella che non c'è più (es. «iCloud - Simone» spostata in «iCloud»)
            if ph:
                live = tuple(f"{ph}/{acc}/" for acc in s.get("accounts", []))
                for r in c.execute("select id, path from items where kind in ('mail','event','contact') and person=?", (person,)).fetchall():
                    if not r["path"].startswith(live):
                        c.execute("delete from items where id=?", (r["id"],)); c.execute("delete from fts where rowid=?", (r["id"],))
                c.commit()
        c.close()
        return True
    finally:
        _lock.release()


def fts_query(q):
    """Testo libero → query FTS5 sicura: ogni parola come prefisso, frasi tra virgolette mantenute."""
    parts = re.findall(r'"[^"]+"|\S+', q or "")
    out = []
    for p in parts:
        if p.startswith('"'):
            out.append('"' + p.strip('"').replace('"', "") + '"')
        else:
            w = re.sub(r"[^\w@.\-]", "", p, flags=re.U)
            if w:
                out.append('"' + w.replace('"', "") + '"*')
    return " ".join(out)


def stats():
    c = connect()
    try:
        by = {(r["kind"], r["person"]): r["n"] for r in c.execute("select kind, person, count(*) n from items group by kind, person")}
        runs = {r["source"]: dict(r) for r in c.execute("select * from runs")}
        return by, runs
    finally:
        c.close()
