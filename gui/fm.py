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


def init(app, roots, icloud_sub, thumb, user, cloud=None, scan=None):
    _cfg.update(roots=roots, icloud=icloud_sub, thumb=thumb, user=user, cloud=cloud, scan=scan)
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


def _trash_slot(p, j, name):
    """Posto nel cestino per un elemento del lavoro j: <cestino>/<data>-<lavoro>/<n>/<nome> (n evita i nomi uguali)."""
    j["slots"] = j.get("slots", 0) + 1
    return os.path.join(_trash_base(p), time.strftime("%Y%m%d-%H%M%S", time.localtime(j["started"])) + "-" + j["id"], str(j["slots"]), name)


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
                    t = _trash_slot(s, j, os.path.basename(s)); _move(s, t); moves.append((s, t)); j["done"] += 1
                except Exception as e: j["errors"].append(f"{os.path.basename(s)}: {str(e)[:200]}")
            _record(j, moves)
        _run(j, rm); return jsonify({"job": j["id"]})
    abort(400)


# ---- pulizia (3.24): doppioni e file solo sul NAS --------------------------------------------------------------------
# A = nella copia di iCloud ma non più su iCloud, con una copia identica altrove sul NAS (spostati): eliminabili.
# B = non più su iCloud e senza altre copie: vivono solo sul NAS. C = fuori dalla copia di iCloud, identici a un file che è su iCloud.
# Stesso contenuto = stessa dimensione e stesso SHA-1; si calcola solo per i file con la stessa dimensione di un altro.
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


def _walk(base, skip_dir=None):
    for dp, dns, fns in os.walk(base):
        rel = dp[len(base):].strip("/"); parts = rel.split("/") if rel else []
        dns[:] = [d for d in dns if d not in SKIP and not d.startswith(".") and os.path.join(dp, d) != skip_dir
                  and not (len(parts) == 1 and d in PIM)]
        for n in fns:
            if n.startswith("."): continue
            f = os.path.join(dp, n)
            try: st = os.lstat(f)
            except OSError: continue
            if stat.S_ISREG(st.st_mode) and st.st_size > 0: yield f, st.st_size


@bp.post("/fm/api/cleanup")
def api_cleanup():
    if not request.is_json: abort(415)
    person = (request.get_json(silent=True) or {}).get("root", ""); base = resolve(person, "")
    ctx = _cfg["scan"](person) if _cfg.get("scan") else None
    if not ctx or not ctx.get("drive"): return jsonify({"error": "elenco"}), 409
    drive = os.path.realpath(ctx["drive"]); present = ctx["present"]
    j = _job("scan", person, 0)
    base = os.path.realpath(base); rel = lambda f: "" if f == base else f[len(base) + 1:]   # il thread non può leggere la sessione
    def scan():
        inside = list(_walk(drive)) if os.path.isdir(drive) else []
        outside = list(_walk(base, skip_dir=drive))
        j["total"] = len(inside) + len(outside)
        by_size = {}
        for f, sz in inside + outside: by_size.setdefault(sz, []).append(f)
        gone = [(f, sz) for f, sz in inside if f not in present]
        gone_set = {f for f, _sz in gone}
        A, B, C = [], [], []
        for f, sz in gone:
            j["done"] += 1
            # una copia vale solo se resta: su iCloud o fuori dalla copia di iCloud Drive (mai un altro file «non più su iCloud»,
            # altrimenti eliminando il gruppo sparirebbero tutte le copie)
            same = [o for o in by_size.get(sz, []) if o != f and o not in gone_set]
            h = _sha1(f) if same else None
            copies = [o for o in same if h and _sha1(o) == h]
            st_ = ctx["state"](f) if ctx.get("state") else "gone"   # «gone» o «extra» (copia in più con un altro nome)
            row = {"path": rel(f), "size": sz, "state": st_}
            if copies: row["copies"] = [rel(o) for o in copies[:3]]; A.append(row)
            else: B.append(row)
        for f, sz in outside:
            j["done"] += 1
            on_cloud = [o for o in by_size.get(sz, []) if o in present]
            if not on_cloud: continue
            h = _sha1(f); copies = [o for o in on_cloud if h and _sha1(o) == h]
            if copies: C.append({"path": rel(f), "size": sz, "copies": [rel(o) for o in copies[:3]]})
        j["done"] += len(inside) - len(gone)
        j["result"] = {k: sorted(v, key=lambda r: -r["size"])[:5000] for k, v in (("A", A), ("B", B), ("C", C))}
        j["result"].update(when=ctx.get("when"), files=len(inside) + len(outside))
    _run(j, scan); return jsonify({"job": j["id"]})


@bp.get("/fm/api/job/<jid>")
def api_job(jid):
    j = _jobs.get(jid)
    if not j or j["user"] != _cfg["user"](): abort(404)
    return jsonify({k: v for k, v in j.items() if k not in ("created", "user")})


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
            for base in {_trash_base(r) for r in _roots_all()}:
                if not os.path.isdir(base): continue
                for n in os.listdir(base):
                    p = os.path.join(base, n)
                    if time.time() - os.lstat(p).st_mtime > CESTINO_GIORNI * 86400:
                        shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
        except Exception as e: print(f"cestino file manager: {e}", flush=True)


def _roots_all():
    try: return list(_cfg["roots"](all_persons=True).values())
    except TypeError: return list(_cfg["roots"]().values())
