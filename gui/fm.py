"""File manager della scheda File (3.20): le cartelle di ogni persona sul NAS (/documents/<persona>), come in Finder o in un
commander a due pannelli.

Lavora direttamente sul disco, non sull'indice di ricerca. Ogni persona vede solo le proprie radici (allowed_persons).
Copiare, spostare, rinominare, creare cartelle ed eliminare sono «lavori» in background con avanzamento. Ogni lavoro
finito entra nella cronologia dell'utente, che si può annullare e ripetere. Eliminare non cancella: sposta nel cestino
/documents/.cestino-dashboard/<lavoro>/, svuotato dopo CESTINO_GIORNI giorni. Così anche «annulla» di una copia o di
una nuova cartella è uno spostamento nel cestino, e «ripeti» lo rimette a posto.

Ogni operazione si riduce a una lista di spostamenti (da, a):
  sposta / rinomina        [(origine, destinazione)]
  elimina                  [(origine, cestino)]
  copia / nuova cartella   [(cestino, nuovo)]: il nuovo elemento «arriva dal cestino», quindi annulla = rimandarlo lì
Annulla applica gli spostamenti al contrario e in ordine inverso, ripeti li riapplica.

Nella cartella iCloud la copia è a senso unico (iCloud → NAS): le modifiche lì non arrivano a iCloud e iclouddr può
rimettere gli originali al giro dopo. La pagina lo dice con un avviso; scelta dell'utente del 9/11/2026.
"""
import hashlib, os, re, json, time, shutil, stat, threading, uuid, mimetypes
from flask import Blueprint, request, jsonify, abort, send_file, session

bp = Blueprint("fm", __name__)
CESTINO_GIORNI = int(os.environ.get("FM_TRASH_DAYS", "30"))
UID, GID = int(os.environ.get("FM_UID", "99")), int(os.environ.get("FM_GID", "100"))   # nobody:users, come le share di Unraid
HIST_MAX = 50
TEXT_EXT = {"txt", "md", "csv", "json", "xml", "yml", "yaml", "log", "ini", "conf", "cfg", "py", "sh", "js", "css", "html", "htm", "sql",
            "swift", "go", "c", "h", "cpp", "java", "kt", "ts", "toml", "env", "srt", "vtt", "tex", "rtf", "plist"}
_cfg = {}            # roots(): {persona: percorso locale}; icloud(persona): sottocartella iCloud; thumb(src, size, ext): file jpeg; user(): chi
_jobs = {}           # id → stato del lavoro
_hist = {}           # utente → {"undo": [voci], "redo": [voci]}
_lock = threading.Lock()


def init(app, roots, icloud_sub, thumb, user, cloud=None, scan=None, persons=None, person_roots=None, trash_bases=None, is_admin=None, display=None):
    _cfg.update(roots=roots, icloud=icloud_sub, thumb=thumb, user=user, cloud=cloud, scan=scan, persons=persons or (lambda: list(roots())),
                person_roots=person_roots or (lambda: list(roots().values())), trash_bases=trash_bases or (lambda: []), is_admin=is_admin or (lambda: False), display=display or (lambda p: p))
    app.register_blueprint(bp)
    threading.Thread(target=_purge_loop, daemon=True).start()


# ---- percorsi ----------------------------------------------------------------------------------------------------
def _roots(): return _cfg["roots"]()
def _trash_base(p):
    """Cestino della share che contiene p: /documents/.cestino-dashboard (stessa share, quindi spostare è una rinomina)."""
    for r in _roots_all():
        rr = os.path.realpath(r)
        if p == rr or p.startswith(rr + "/"): return os.path.join(os.path.dirname(rr), ".cestino-dashboard")
    return os.path.join(os.path.dirname(p), ".cestino-dashboard")


def resolve(person, rel):
    """(persona, percorso relativo) → percorso locale assoluto dentro la radice della persona, o 404/403."""
    roots = _roots()
    if person not in roots: abort(404)
    base = os.path.realpath(roots[person]); rel = (rel or "").strip("/")
    if any(p in ("..", ".") for p in rel.split("/") if p): abort(400)
    p = os.path.realpath(os.path.join(base, rel)) if rel else base
    if p != base and not p.startswith(base + "/"): abort(403)
    return p


def _rel(person, p):
    base = os.path.realpath(_roots()[person]); return "" if p == base else p[len(base) + 1:]


def _valid_name(n):
    return bool(n) and n not in (".", "..") and "/" not in n and "\x00" not in n and len(n.encode()) <= 255


def _free_name(folder, name, copy=False, is_dir=False):
    """Nome libero in folder: «nome», poi «nome copia», «nome copia 2»… (copia) oppure «nome 2», «nome 3»…"""
    if not os.path.lexists(os.path.join(folder, name)): return name
    base, ext = (name, "") if is_dir or (name.startswith(".") and name.count(".") == 1) else os.path.splitext(name)
    for i in range(1, 10000):
        cand = (f"{base} copia{ext}" if i == 1 else f"{base} copia {i}{ext}") if copy else f"{base} {i + 1}{ext}"
        if not os.path.lexists(os.path.join(folder, cand)): return cand
    raise OSError("troppi nomi uguali")


def _chown(p):
    try:
        if os.geteuid() == 0: os.lchown(p, UID, GID)
    except OSError: pass


def _chown_tree(p):
    _chown(p)
    if os.path.isdir(p) and not os.path.islink(p):
        for d, dns, fns in os.walk(p):
            for n in dns + fns: _chown(os.path.join(d, n))


def _entry(p, name):
    try: st = os.lstat(p)
    except OSError: return None
    is_dir = stat.S_ISDIR(st.st_mode)
    if stat.S_ISLNK(st.st_mode):
        try: is_dir = os.path.isdir(p)
        except OSError: pass
    n = None
    if is_dir:
        try:
            with os.scandir(p) as it: n = sum(1 for e in it if not e.name.startswith("."))
        except OSError: n = None
    ext = "" if is_dir else (os.path.splitext(name)[1][1:].lower())
    return {"name": name, "dir": is_dir, "size": None if is_dir else st.st_size, "mtime": st.st_mtime, "atime": st.st_atime,
            "added": getattr(st, "st_birthtime", None) or st.st_ctime, "ext": ext, "n": n, "link": stat.S_ISLNK(st.st_mode)}


# ---- lettura -----------------------------------------------------------------------------------------------------
@bp.get("/fm/api/roots")
def api_roots():
    r = _roots()
    return jsonify([{"name": n, "icloud": _cfg["icloud"](n)} for n in r])


@bp.get("/fm/api/list")
def api_list():
    person = request.args.get("root", ""); rel = request.args.get("path", "")
    p = resolve(person, rel); hidden = request.args.get("hidden") == "1"
    if not os.path.isdir(p): abort(404)
    out = []
    try:
        with os.scandir(p) as it: names = [e.name for e in it]
    except PermissionError: abort(403)
    for n in names:
        if not hidden and (n.startswith(".") or n in ("@eaDir", "#recycle")): continue
        e = _entry(os.path.join(p, n), n)
        if e:
            if _cfg.get("cloud"):   # 3.21: su iCloud o solo sul NAS
                try: e["cloud"], e["cloud_t"] = _cfg["cloud"](os.path.join(p, n), e["dir"])
                except Exception: pass
            out.append(e)
    return jsonify({"root": person, "path": _rel(person, p), "entries": out, "icloud": _cfg["icloud"](person)})


@bp.get("/fm/raw")
def raw():
    p = resolve(request.args.get("root", ""), request.args.get("path", ""))
    if not os.path.isfile(p): abort(404)
    r = send_file(p, as_attachment=request.args.get("dl") == "1", download_name=os.path.basename(p), conditional=True)
    r.headers["X-Content-Type-Options"] = "nosniff"
    if (mimetypes.guess_type(p)[0] or "").startswith(("text/html", "image/svg", "application/xhtml")):   # niente HTML attivo dalla share
        r.headers["Content-Security-Policy"] = "sandbox"
    return r


@bp.get("/fm/thumb")
def thumb():
    p = resolve(request.args.get("root", ""), request.args.get("path", ""))
    if not os.path.isfile(p): abort(404)
    size = 1200 if request.args.get("s") == "big" else 240
    out = _cfg["thumb"](p, size)
    if not out: abort(404)
    r = send_file(out, mimetype="image/jpeg"); r.headers["Cache-Control"] = "private, max-age=604800"; return r


@bp.get("/fm/api/text")
def text():
    p = resolve(request.args.get("root", ""), request.args.get("path", ""))
    if not os.path.isfile(p): abort(404)
    with open(p, "rb") as f: b = f.read(200_000)
    if b"\x00" in b[:4096]: return jsonify({"binary": True})
    return jsonify({"text": b.decode("utf-8", "replace"), "cut": os.path.getsize(p) > len(b)})


# ---- lavori ------------------------------------------------------------------------------------------------------
def _job(kind, label, total):
    jid = uuid.uuid4().hex[:12]
    j = {"id": jid, "kind": kind, "label": label, "state": "run", "done": 0, "total": total, "bytes": 0, "errors": [], "renamed": [],
         "user": _cfg["user"](), "started": time.time(), "created": []}
    with _lock:
        for k in [k for k, v in _jobs.items() if v["state"] != "run" and time.time() - v["started"] > 3600]: _jobs.pop(k, None)
        _jobs[jid] = j
    return j


def _record(j, moves):
    if not moves: return
    with _lock:
        h = _hist.setdefault(j["user"], {"undo": [], "redo": []})
        h["undo"].append({"label": j["label"], "kind": j["kind"], "moves": moves, "at": time.time()}); h["undo"][:] = h["undo"][-HIST_MAX:]; h["redo"].clear()


def _move(a, b):
    """Sposta a → b (b non deve esistere). Rinomina se possibile, altrimenti copia e cancella (dischi diversi)."""
    if os.path.lexists(b): raise FileExistsError(b)
    os.makedirs(os.path.dirname(b), exist_ok=True)
    try: os.rename(a, b)
    except OSError as e:
        if e.errno != 18: raise   # EXDEV: su un altro disco
        shutil.move(a, b)


def _copy(a, b, j):
    if os.path.isdir(a) and not os.path.islink(a):
        os.makedirs(b); _chown(b)
        for n in os.listdir(a): _copy(os.path.join(a, n), os.path.join(b, n), j)
        try: shutil.copystat(a, b)
        except OSError: pass
    elif os.path.islink(a): os.symlink(os.readlink(a), b)
    else:
        shutil.copy2(a, b); _chown(b); j["bytes"] += os.path.getsize(b)
    j["done"] += 1


def _count(p):
    if os.path.isdir(p) and not os.path.islink(p): return 1 + sum(len(dns) + len(fns) for _d, dns, fns in os.walk(p))
    return 1


def _trash_slot(p, j, name, base=None):
    """Posto nel cestino per un elemento del lavoro j: <cestino>/<data>-<lavoro>/<n>/<nome> (n evita i nomi uguali)."""
    j["slots"] = j.get("slots", 0) + 1
    return os.path.join(base or _trash_base(p), time.strftime("%Y%m%d-%H%M%S", time.localtime(j["started"])) + "-" + j["id"], str(j["slots"]), name)


def _run(j, fn):
    def go():
        try: fn()
        except Exception as e: j["errors"].append(str(e)[:300])
        j["state"] = "error" if j["errors"] and not j["done"] else "done"; j["ended"] = time.time()
    threading.Thread(target=go, daemon=True).start()


def _srcs(items):
    out = []
    for it in items or []:
        p = resolve(it.get("root", ""), it.get("path", ""))
        if p == os.path.realpath(_roots()[it.get("root", "")]): abort(400)   # mai la radice della persona
        if not os.path.lexists(p): abort(404)
        out.append(p)
    return out


@bp.post("/fm/api/op")
def api_op():
    if not request.is_json: abort(415)   # solo fetch dalla pagina: un modulo di un altro sito non può mandare JSON
    d = request.get_json(silent=True) or {}; op = d.get("op", "")
    if op == "mkdir":
        folder = resolve(d.get("root", ""), d.get("path", "")); name = (d.get("name") or "").strip()
        if not _valid_name(name): return jsonify({"error": "nome"}), 400
        if not os.path.isdir(folder): abort(404)
        final = os.path.join(folder, _free_name(folder, name))
        j = _job("mkdir", name, 1)
        def mk():
            os.mkdir(final); _chown(final); j["done"] = 1; j["created"].append(final)
            _record(j, [(_trash_slot(final, j, os.path.basename(final)), final)])
        _run(j, mk); return jsonify({"job": j["id"], "name": os.path.basename(final)})
    if op == "rename":
        src = _srcs([{"root": d.get("root"), "path": d.get("path")}])[0]; name = (d.get("name") or "").strip()
        if not _valid_name(name): return jsonify({"error": "nome"}), 400
        dst = os.path.join(os.path.dirname(src), name)
        if dst == src: return jsonify({"job": None})
        if os.path.lexists(dst): return jsonify({"error": "esiste"}), 409
        j = _job("rename", os.path.basename(src) + " → " + name, 1)
        def rn(): _move(src, dst); j["done"] = 1; _record(j, [(src, dst)])
        _run(j, rn); return jsonify({"job": j["id"]})
    if op in ("copy", "move"):
        srcs = _srcs(d.get("items")); folder = resolve(d.get("dst", {}).get("root", ""), d.get("dst", {}).get("path", ""))
        if not os.path.isdir(folder): abort(404)
        for s in srcs:
            if os.path.isdir(s) and (folder == s or folder.startswith(s + "/")): return jsonify({"error": "dentro_se_stessa"}), 400
        names = [os.path.basename(s) for s in srcs]
        j = _job(op, (names[0] if len(names) == 1 else f"{len(names)}"), sum(_count(s) for s in srcs) if op == "copy" else len(srcs))
        def cp():
            moves = []
            for s in srcs:
                try:
                    if op == "move" and os.path.dirname(s) == folder: j["done"] += 1; continue   # già lì
                    name = _free_name(folder, os.path.basename(s), copy=(op == "copy" and os.path.dirname(s) == folder), is_dir=os.path.isdir(s))
                    if name != os.path.basename(s): j["renamed"].append([os.path.basename(s), name])
                    dst = os.path.join(folder, name)
                    if op == "copy":
                        _copy(s, dst, j); j["created"].append(dst); moves.append((_trash_slot(dst, j, name), dst))
                    else:
                        _move(s, dst); j["done"] += 1; moves.append((s, dst))
                except Exception as e: j["errors"].append(f"{os.path.basename(s)}: {str(e)[:200]}")
            _record(j, moves)
        _run(j, cp); return jsonify({"job": j["id"]})
    if op == "delete":
        srcs = _srcs(d.get("items"))
        j = _job("delete", os.path.basename(srcs[0]) if len(srcs) == 1 else f"{len(srcs)}", len(srcs))
        def rm():
            moves = []
            for s in srcs:
                try:
                    t = _trash_slot(s, j, os.path.basename(s)); _move(s, t); _manifest(t, s, j); moves.append((s, t)); j["done"] += 1
                except Exception as e: j["errors"].append(f"{os.path.basename(s)}: {str(e)[:200]}")
            _record(j, moves)
        _run(j, rm); return jsonify({"job": j["id"]})
    abort(400)


# ---- pulizia (3.24, generalizzata nella 3.25): File, Foto, Posta ----------------------------------------------------------
# File e Foto. A = doppioni: non più su iCloud, con una copia identica che RESTA (su iCloud o fuori dalla copia di iCloud) → eliminabili.
# B = esistono solo qui: non più su iCloud e nessun'altra copia. C = copie in altre cartelle di file che sono ancora su iCloud.
# Stesso contenuto = stessa dimensione e stesso SHA-1, calcolato solo per i file con la stessa dimensione di un altro.
# Posta. B = messaggi cancellati dal server (flag T del Maildir): esistono solo qui. A = doppioni (stesso Message-ID nello stesso account).
SKIP = {".cestino-dashboard", "@eaDir", "#recycle", "#snapshot", ".Trash", ".Trashes", ".Spotlight-V100", ".fseventsd"}
PIM = {"Posta", "Note", "Promemoria", "Calendario", "Contatti"}   # cartelle del modulo PIM dentro <persona>/<account>/
_hash = {}


def _sha1(p):
    try: st = os.stat(p)
    except OSError: return None
    k = (p, st.st_size, int(st.st_mtime))
    if k in _hash: return _hash[k]
    h = hashlib.sha1()
    try:
        with open(p, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    except OSError: return None
    if len(_hash) > 200000: _hash.clear()
    _hash[k] = h.hexdigest(); return _hash[k]


def _walk(base, skip=()):
    for dp, dns, fns in os.walk(base):
        rel = dp[len(base):].strip("/"); parts = rel.split("/") if rel else []
        dns[:] = [d for d in dns if d not in SKIP and not d.startswith(".") and os.path.join(dp, d) not in skip
                  and not (len(parts) == 1 and d in PIM)]
        for n in fns:
            if n.startswith("."): continue
            f = os.path.join(dp, n)
            try: st = os.lstat(f)
            except OSError: continue
            if stat.S_ISREG(st.st_mode) and st.st_size > 0: yield f, st.st_size


def _scan_files(ctx, j, rel):
    inside = [x for d in ctx["inside"] if os.path.isdir(d) for x in _walk(d)]
    outside = list(_walk(ctx["base"], skip=set(ctx["inside"])))
    j["total"] = len(inside) + len(outside)
    present = ctx["present"]; by_size = {}
    for f, sz in inside + outside: by_size.setdefault(sz, []).append(f)
    gone = [(f, sz) for f, sz in inside if f not in present]; gone_set = {f for f, _sz in gone}
    out = {"A": [], "B": [], "C": []}
    for f, sz in gone:
        j["done"] += 1
        # una copia vale solo se resta: su iCloud o fuori dalla copia di iCloud (mai un altro file «non più su iCloud»,
        # altrimenti eliminando il gruppo sparirebbero tutte le copie)
        same = [o for o in by_size.get(sz, []) if o != f and o not in gone_set]
        h = _sha1(f) if same else None
        copies = [o for o in same if h and _sha1(o) == h]
        row = {"path": rel(f), "size": sz, "state": ctx["state"](f) if ctx.get("state") else "gone", "_abs": f}
        if copies: row["copies"] = [rel(o) for o in copies[:3]]; out["A"].append(row)
        else: out["B"].append(row)
    for f, sz in outside:
        j["done"] += 1
        on_cloud = [o for o in by_size.get(sz, []) if o in present]
        if not on_cloud: continue
        h = _sha1(f); copies = [o for o in on_cloud if h and _sha1(o) == h]
        if copies: out["C"].append({"path": rel(f), "size": sz, "copies": [rel(o) for o in copies[:3]], "_abs": f})
    j["done"] += len(inside) - len(gone)
    return out, len(inside) + len(outside)


_MSGID = re.compile(rb"^message-id:\s*(<[^>\r\n]+>|\S+)", re.I | re.M)
_SUBJ = re.compile(rb"^subject:\s*(.*)$", re.I | re.M)


def _scan_mail(ctx, j, rel):
    files = []
    for acc, d in ctx["maildirs"]:
        for dp, _dns, fns in os.walk(d):
            if os.path.basename(dp) in ("cur", "new"): files += [(acc, os.path.join(dp, n)) for n in fns if not n.startswith(".")]
    j["total"] = len(files); out = {"A": [], "B": [], "C": []}; seen = {}
    for acc, f in sorted(files, key=lambda x: x[1]):
        j["done"] += 1
        try: st = os.stat(f)
        except OSError: continue
        flags = f.rsplit(":2,", 1)[1] if ":2," in f else ""
        try:
            with open(f, "rb") as fh: head = fh.read(16384).split(b"\r\n\r\n", 1)[0].split(b"\n\n", 1)[0]
        except OSError: continue
        m = _MSGID.search(head); sj = _SUBJ.search(head)
        subj = sj.group(1).decode("utf-8", "replace").strip()[:120] if sj else ""
        try:
            from email.header import decode_header, make_header
            subj = str(make_header(decode_header(subj)))[:120]
        except Exception: pass
        row = {"path": rel(f), "size": st.st_size, "subject": subj, "account": acc, "_abs": f}
        if "T" in flags: out["B"].append(row); continue
        if m:
            k = (acc, m.group(1).strip().lower())
            if k in seen: row["copies"] = [seen[k]]; out["A"].append(row)
            else: seen[k] = rel(f)
    return out, len(files)


@bp.post("/fm/api/cleanup")
def api_cleanup():
    if not request.is_json: abort(415)
    d = request.get_json(silent=True) or {}; kind = d.get("kind", "files"); person = d.get("root", "")
    if person not in _cfg["persons"](): abort(403)
    ctx = _cfg["scan"](person, kind) if _cfg.get("scan") else None
    if not ctx: return jsonify({"error": "elenco" if kind != "mail" else "posta"}), 409
    base = os.path.realpath(ctx["base"]); rel = lambda f: "" if f == base else f[len(base) + 1:]   # il thread non può leggere la sessione
    j = _job("scan", person, 0); j.update(_kind=kind, _trash=ctx["trash"], _person=person)
    def scan():
        out, n = (_scan_mail if kind == "mail" else _scan_files)(ctx, j, rel)
        j["_abs"] = {g: [r.pop("_abs") for r in rows] for g, rows in out.items()}
        j["result"] = {g: rows for g, rows in out.items()}
        for g in ("A", "B", "C"):   # dal più grande; l'ordine di _abs segue quello dei risultati
            order = sorted(range(len(j["result"][g])), key=lambda i: -j["result"][g][i]["size"])[:5000]
            j["result"][g] = [j["result"][g][i] for i in order]; j["_abs"][g] = [j["_abs"][g][i] for i in order]
        j["result"].update(when=ctx.get("when"), files=n, kind=kind)
    _run(j, scan); return jsonify({"job": j["id"]})


@bp.post("/fm/api/cleanup/delete")
def api_cleanup_delete():
    """Sposta nel cestino gli elementi scelti fra i risultati di un'analisi (mai percorsi arbitrari: solo quelli trovati)."""
    if not request.is_json: abort(415)
    d = request.get_json(silent=True) or {}; s = _jobs.get(d.get("job", ""))
    if not s or s["user"] != _cfg["user"]() or s["kind"] != "scan" or "_abs" not in s: abort(404)
    if s["_person"] not in _cfg["persons"](): abort(403)
    srcs = []
    for g, i in d.get("items") or []:
        try: p = s["_abs"][g][int(i)]
        except (KeyError, IndexError, ValueError, TypeError): abort(400)
        if os.path.lexists(p) and p not in srcs: srcs.append(p)
    if not srcs: return jsonify({"job": None})
    j = _job("delete", os.path.basename(srcs[0]) if len(srcs) == 1 else f"{len(srcs)}", len(srcs)); trash = s["_trash"]
    def rm():
        moves = []
        for p in srcs:
            try:
                t = _trash_slot(p, j, os.path.basename(p), base=trash); _move(p, t); _manifest(t, p, j); moves.append((p, t)); j["done"] += 1
            except Exception as e: j["errors"].append(f"{os.path.basename(p)}: {str(e)[:200]}")
        _record(j, moves)
    _run(j, rm); return jsonify({"job": j["id"]})


# ---- cestino (3.25): elenco, ripristino e svuotamento dalla dashboard --------------------------------------------------
def _manifest(trash_path, orig, j):
    """Nel cartella del lavoro nel cestino annota da dove veniva ogni elemento (serve per ripristinarlo anche dopo un riavvio)."""
    jd = os.path.dirname(os.path.dirname(trash_path)); mf = os.path.join(jd, "manifest.json")
    with _lock:
        try: m = json.load(open(mf))
        except (OSError, ValueError): m = {"when": j["started"], "user": j["user"], "label": j["label"], "items": []}
        m["items"].append({"orig": orig, "trash": trash_path}); tmp = mf + ".tmp"
        json.dump(m, open(tmp, "w"), ensure_ascii=False); os.replace(tmp, mf)


def _trash_jobs():
    out = []
    for base in _cfg["trash_bases"]():
        if not os.path.isdir(base): continue
        for n in sorted(os.listdir(base), reverse=True):
            jd = os.path.join(base, n)
            if not os.path.isdir(jd): continue
            try: m = json.load(open(os.path.join(jd, "manifest.json")))
            except (OSError, ValueError): m = None
            out.append((base, n, jd, m))
    return out


def _visible(m):
    if _cfg["is_admin"](): return True
    if not m: return False
    roots = [os.path.realpath(r) for r in _cfg["person_roots"]()]
    return all(any(it["orig"] == r or it["orig"].startswith(r + "/") for r in roots) for it in m["items"])


@bp.get("/fm/api/trash")
def api_trash():
    rows = []
    for base, n, jd, m in _trash_jobs():
        if not _visible(m): continue
        nf = sz = 0
        for dp, _d, fns in os.walk(jd):
            for f in fns:
                if dp == jd and f == "manifest.json": continue
                try: sz += os.lstat(os.path.join(dp, f)).st_size; nf += 1
                except OSError: pass
        try: when = os.lstat(jd).st_mtime
        except OSError: when = 0
        names = [os.path.basename(it["orig"]) for it in m["items"]] if m else sorted(x for d in os.listdir(jd) if d.isdigit() for x in os.listdir(os.path.join(jd, d)))
        rows.append({"id": f"{os.path.basename(os.path.dirname(base))}/{n}", "when": (m or {}).get("when") or when, "label": (m or {}).get("label", ""),
                     "files": nf, "size": sz, "names": names[:8], "n": len(names), "known": bool(m),
                     "orig": sorted({_cfg["display"](os.path.dirname(it["orig"])) for it in m["items"]})[:4] if m else [],
                     "expires": (when + CESTINO_GIORNI * 86400) if when else 0})
    return jsonify({"jobs": rows, "days": CESTINO_GIORNI})


def _trash_job(tid):
    for base, n, jd, m in _trash_jobs():
        if f"{os.path.basename(os.path.dirname(base))}/{n}" == tid: return base, jd, m
    abort(404)


@bp.post("/fm/api/trash/restore")
def api_trash_restore():
    if not request.is_json: abort(415)
    base, jd, m = _trash_job((request.get_json(silent=True) or {}).get("id", ""))
    if not _visible(m): abort(403)
    done, errs = 0, []
    if m:
        for it in m["items"]:
            src, dst = it["trash"], it["orig"]
            if not os.path.lexists(src): continue
            if os.path.lexists(dst): dst = os.path.join(os.path.dirname(dst), _free_name(os.path.dirname(dst), os.path.basename(dst), copy=True))
            try: _move(src, dst); done += 1
            except Exception as e: errs.append(f"{os.path.basename(dst)}: {str(e)[:200]}")
    else:   # cestino di prima della 3.25, senza l'origine: va in «Ripristinati dal cestino» nella stessa share (solo amministratore)
        dest = os.path.join(os.path.dirname(base), "Ripristinati dal cestino", os.path.basename(jd))
        for d in sorted(x for x in os.listdir(jd) if x.isdigit()):
            for x in os.listdir(os.path.join(jd, d)):
                try: _move(os.path.join(jd, d, x), os.path.join(dest, _free_name(dest, x) if os.path.isdir(dest) else x)); done += 1
                except Exception as e: errs.append(f"{x}: {str(e)[:200]}")
        _chown_tree(dest) if os.path.isdir(dest) else None
    if not errs: shutil.rmtree(jd, ignore_errors=True)
    return jsonify({"done": done, "errors": errs, "where": "" if m else "Ripristinati dal cestino"})


@bp.post("/fm/api/trash/empty")
def api_trash_empty():
    if not request.is_json: abort(415)
    base, jd, m = _trash_job((request.get_json(silent=True) or {}).get("id", ""))
    if not _visible(m): abort(403)
    shutil.rmtree(jd, ignore_errors=True); return jsonify({"ok": not os.path.exists(jd)})


@bp.get("/fm/api/job/<jid>")
def api_job(jid):
    j = _jobs.get(jid)
    if not j or j["user"] != _cfg["user"](): abort(404)
    return jsonify({k: v for k, v in j.items() if k not in ("created", "user") and not k.startswith("_")})


def _apply(moves, j):
    for a, b in moves:
        try:
            if not os.path.lexists(a): raise FileNotFoundError(os.path.basename(a))
            _move(a, b); j["done"] += 1
        except Exception as e: j["errors"].append(f"{os.path.basename(a)}: {str(e)[:200]}")


@bp.post("/fm/api/history/<what>")
def api_history(what):
    if not request.is_json: abort(415)
    if what not in ("undo", "redo"): abort(404)
    u = _cfg["user"]()
    with _lock:
        h = _hist.setdefault(u, {"undo": [], "redo": []})
        if not h[what]: return jsonify({"job": None})
        e = h[what].pop()
    j = _job(what, e["label"], len(e["moves"]))
    moves = [(b, a) for a, b in reversed(e["moves"])] if what == "undo" else e["moves"]
    def go():
        _apply(moves, j)
        with _lock: h["redo" if what == "undo" else "undo"].append(e)
    _run(j, go); return jsonify({"job": j["id"], "label": e["label"], "kind": e["kind"]})


@bp.get("/fm/api/history")
def api_hist_state():
    h = _hist.get(_cfg["user"](), {"undo": [], "redo": []})
    last = lambda l: {"label": l[-1]["label"], "kind": l[-1]["kind"]} if l else None
    return jsonify({"undo": last(h["undo"]), "redo": last(h["redo"])})


# ---- cestino -----------------------------------------------------------------------------------------------------
def _purge_loop():
    while True:
        time.sleep(3600)
        try:
            for base in {_trash_base(r) for r in _roots_all()} | set(_cfg["trash_bases"]()):
                if not os.path.isdir(base): continue
                for n in os.listdir(base):
                    p = os.path.join(base, n)
                    if time.time() - os.lstat(p).st_mtime > CESTINO_GIORNI * 86400:
                        shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
        except Exception as e: print(f"cestino file manager: {e}", flush=True)


def _roots_all():
    try: return list(_cfg["roots"](all_persons=True).values())
    except TypeError: return list(_cfg["roots"]().values())
