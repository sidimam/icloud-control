#!/usr/bin/env python3
"""icloud-dispatcher GUI: utenti = container iCloud (foto: boredazfcuk/icloudpd; iCloud Drive: mandarons/icloud-drive), chat Telegram, cosa scaricare, rinnovo 2FA."""
import json, os, re, subprocess, shutil, datetime, html, time
from flask import Flask, request, redirect, url_for, render_template, flash, abort, session
from werkzeug.security import check_password_hash, generate_password_hash

APP_DIR = os.environ.get("APP_DIR", "/app"); CFG = os.path.join(APP_DIR, "config.json")
TOKEN = os.environ.get("TELEGRAM_TOKEN", ""); PORT = int(os.environ.get("GUI_PORT", "8787"))
app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), "templates"))
# ---- accesso: GUI_USER + GUI_PASSWORD_HASH (hash werkzeug, mai la password in chiaro). Senza hash la GUI resta aperta come prima.
GUI_USER = os.environ.get("GUI_USER", "admin"); GUI_HASH = os.environ.get("GUI_PASSWORD_HASH", "")
def _secret():   # chiave di sessione persistente in /app/state: un riavvio non disconnette
    f = os.path.join(APP_DIR, "state", "gui_secret")
    try: return open(f, "rb").read()
    except FileNotFoundError:
        os.makedirs(os.path.dirname(f), exist_ok=True); k = os.urandom(32); open(f, "wb").write(k); os.chmod(f, 0o600); return k
app.secret_key = _secret()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=30))
FAILS = {}   # ip -> [orari dei tentativi falliti]: oltre 10 in 10 minuti l'ip aspetta
@app.before_request
def require_login():
    if not GUI_HASH or request.endpoint in ("login", "healthz", "static") or session.get("user") == GUI_USER: return None
    return redirect(url_for("login", next=request.full_path if request.method == "GET" else "/"))
@app.route("/login", methods=["GET", "POST"])
def login():
    nxt = request.values.get("next", "/"); nxt = nxt if nxt.startswith("/") and not nxt.startswith("//") else "/"
    if request.method == "POST":
        ip = request.headers.get("CF-Connecting-IP") or request.remote_addr; now = time.time()
        recent = FAILS[ip] = [t for t in FAILS.get(ip, []) if now - t < 600]
        if len(recent) >= 10: flash("Troppi tentativi: riprova fra qualche minuto"); return render_template("login.html", next=nxt), 429
        if request.form.get("user", "") == GUI_USER and check_password_hash(GUI_HASH, request.form.get("password", "")):
            session.clear(); session["user"] = GUI_USER; session.permanent = request.form.get("remember") == "on"; FAILS.pop(ip, None); return redirect(nxt)
        recent.append(now); time.sleep(1); flash("Utente o password errati")
    return render_template("login.html", next=nxt)
@app.post("/logout")
def logout(): session.clear(); return redirect(url_for("login"))

def cfg(): return json.load(open(CFG))
def save(c): json.dump(c, open(CFG, "w"), indent=2, ensure_ascii=False)
def sh(cmd, timeout=60):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout); return r.returncode, r.stdout.strip(), r.stderr.strip()
def dexec(c, cmd, timeout=60): return sh(f"docker exec {c} sh -c {json.dumps(cmd)}", timeout)
def inspect(c):
    rc, out, _ = sh(f"docker inspect {c}");
    return json.loads(out)[0] if rc == 0 and out else None
def env_of(info): return dict(e.split("=", 1) for e in info["Config"]["Env"] if "=" in e)
def mounts(info): return {m["Destination"]: m["Source"] for m in info.get("Mounts", [])}
def conf_path(info):
    src = mounts(info).get("/config");  # percorso host → nel nostro container è sotto /appdata
    if not src: return None
    p = cfg()["paths"]
    return src.replace("/mnt/user/appdata", p["appdata"], 1) + "/icloudpd.conf"
def read_conf(path):
    d = {}
    try:
        for line in open(path):
            if "=" in line and not line.startswith("#"): k, v = line.rstrip("\n").split("=", 1); d[k] = v
    except FileNotFoundError: pass
    return d
def write_conf(path, updates):
    lines = open(path).read().splitlines() if os.path.exists(path) else []; seen = set()
    for i, l in enumerate(lines):
        k = l.split("=", 1)[0]
        if k in updates: lines[i] = f"{k}={updates[k]}"; seen.add(k)
    for k, v in updates.items():
        if k not in seen: lines.append(f"{k}={v}")
    open(path, "w").write("\n".join(lines) + "\n")
def tpl_path(c): return os.path.join(cfg()["paths"]["templates"], f"my-{c}.xml")
def tpl_set(c, key, value):
    p = tpl_path(c)
    if not os.path.exists(p): return False
    s = open(p).read()
    pat = re.compile(r'(<Config [^>]*Target="%s"[^>]*?)(/>|>[^<]*</Config>)' % re.escape(key))
    if pat.search(s): s = pat.sub(lambda m: m.group(1) + f">{html.escape(str(value))}</Config>", s, count=1)
    else: s = s.replace("</Container>", f'  <Config Name="{key}" Target="{key}" Default="" Mode="" Description="" Type="Variable" Display="advanced" Required="false" Mask="false">{html.escape(str(value))}</Config>\n</Container>')
    open(p, "w").write(s); return True
def telegram(chat, text):
    if TOKEN and chat: sh(f"curl -s -X POST https://api.telegram.org/bot{TOKEN}/sendMessage -d chat_id={chat} --data-urlencode text={json.dumps(text)} >/dev/null", 20)
def cookie_name(c): return dexec(c, 'ls /config | grep -E "^[a-z0-9]+(com|it)$" | head -1')[1]
def auth_running(c): return dexec(c, "pgrep -f authenticate.exp >/dev/null")[0] == 0
def start_auth(c):
    ck = cookie_name(c)
    dexec(c, f"rm -f /config/{ck} /config/{ck}.session; mkdir -p /tmp/icloudpd; : > /tmp/icloudpd/expect_input.txt; chmod 666 /tmp/icloudpd/expect_input.txt; nohup /usr/bin/expect /opt/authenticate.exp >/tmp/icloudpd/reauth.log 2>&1 &")
def send_code(c, code): dexec(c, f"printf '%s\\n' '{code}' >> /tmp/icloudpd/expect_input.txt")

def utype(u): return u.get("type", "icloudpd")
def host_to_local(path):
    """Percorso host (/mnt/user/...) → percorso dentro questo container (mount /appdata, /photos, /documents)."""
    p = cfg()["paths"]
    for host, local in (("/mnt/user/appdata", p["appdata"]), ("/mnt/user/media/photos", p["photos"]), ("/mnt/user/documents", p.get("documents", "/documents"))):
        if path.startswith(host): return path.replace(host, local, 1)
    return path
def yaml_get(path, key, default=""):
    try:
        for line in open(path):
            m = re.match(r"\s*%s:\s*(.*?)\s*$" % re.escape(key), line)
            if m: return m.group(1).strip().strip('"')
    except FileNotFoundError: pass
    return default
def yaml_set(path, key, value):
    lines = open(path).read().splitlines(); done = False
    for i, l in enumerate(lines):
        m = re.match(r"(\s*)%s:\s*" % re.escape(key), l)
        if m: lines[i] = f"{m.group(1)}{key}: {value}"; done = True; break
    if not done: lines.append(f"  {key}: {value}")
    open(path, "w").write("\n".join(lines) + "\n")
def drive_days(local_config):
    """Giorni alla scadenza del cookie di fiducia Apple (X-APPLE-WEBAUTH-HSA-TRUST, ~90 giorni, rinnovato dall'app finche' la sessione vive)."""
    d = os.path.join(local_config, "session_data")
    try: files = [os.path.join(d, f) for f in os.listdir(d) if not f.endswith(".session")]
    except FileNotFoundError: return ""
    for f in files:
        for line in open(f, errors="ignore"):
            m = re.match(r'Set-Cookie3: X-APPLE-WEBAUTH-HSA-TRUST=.*?expires="([^"]+)Z"', line)
            if m:
                exp = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                return str(int((exp - datetime.datetime.utcnow()).total_seconds() // 86400))
    return ""
def d_auth_running(c): return dexec(c, 'pgrep -f "icloud --username" >/dev/null')[0] == 0
def d_start_auth(c, apple):
    dexec(c, f"rm -f /tmp/2fa.fifo /tmp/2fa.log; mkfifo -m 666 /tmp/2fa.fifo; nohup sh -c 'exec 3<>/tmp/2fa.fifo; su-exec abc icloud --username={apple} --session-directory=/config/session_data <&3 >/tmp/2fa.log 2>&1; echo EXIT=$? >>/tmp/2fa.log; rm -f /tmp/2fa.fifo' >/dev/null 2>&1 &")
def d_send_code(c, code): dexec(c, f"printf '%s\\n' '{code}' > /tmp/2fa.fifo")

# ---- log e giri di sincronizzazione
ANSI = re.compile(r"\x1b\[[0-9;]*m"); TS = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")
NOISE = re.compile(r"ObjectNotFoundException|Could not find document|File not found in iCloud Drive|not found on server\. Skipping")   # errori innocui ripetuti a ogni giro
CYCLES = (("Drive", "Syncing drive...", "Drive synced"), ("Foto", "Syncing photos...", "Photos synced"))   # righe di inizio/fine giro di mandarons/icloud-drive
def log_ts(line):
    m = TS.match(line)
    return datetime.datetime.strptime(m.group(1).replace("T", " "), "%Y-%m-%d %H:%M:%S") if m else None
def ago(t):
    if not t: return ""
    m = int((datetime.datetime.now() - t).total_seconds() // 60)
    return "adesso" if m < 1 else f"{m} min fa" if m < 120 else f"{m // 60} h {m % 60:02d} min fa" if m < 2880 else f"{m // 1440} giorni fa"
def container_logs(c, n=300):
    rc, out, err = sh(f"docker logs --tail {int(n)} {c} 2>&1", 60)
    return [ANSI.sub("", l) for l in (out or err).splitlines()]
def last_activity(c):
    for l in reversed(container_logs(c, 20)):
        t = log_ts(l)
        if t: return t
    return None
def sync_cycles(c, interval):
    """Per ogni giro (Drive, Foto): in corso da quando, fine e durata dell'ultimo completo, e se sembra bloccato
    (in corso da più del doppio dell'ultimo giro completo, minimo un'ora)."""
    pat = "|".join(re.escape(x) for _, a, b in CYCLES for x in (a, b))
    rc, out, _ = sh(f"docker logs --since 48h {c} 2>&1 | grep -E {json.dumps(pat)}", 60); out = ANSI.sub("", out)
    res = []
    for kind, start, end in CYCLES:
        since = last = None
        for l in out.splitlines():
            t = log_ts(l)
            if not t: continue
            if start in l: since = t
            elif end in l and since: last = (since, t); since = None
        if not (since or last): continue
        dur = int((last[1] - last[0]).total_seconds() // 60) if last else None
        running = int((datetime.datetime.now() - since).total_seconds() // 60) if since else None
        stalled = running is not None and running > max(60, 2 * (dur or 0))
        res.append({"kind": kind, "running_min": running, "since": since.strftime("%H:%M") if since else "", "last_end": ago(last[1]) if last else "",
                    "last_min": dur, "stalled": stalled})
    return res

def user_view(name, u):
    if utype(u) == "iclouddrive": return drive_view(name, u)
    return photos_view(name, u)
def drive_view(name, u):
    c = u["container"]; info = inspect(c); v = {"name": name, "type": "iclouddrive", "label": u.get("label", name), "container": c, "chat_id": u.get("chat_id", ""), "exists": info is not None, "apple_id": u.get("apple_id", "")}
    if not info: return v
    m = mounts(info); conf_host = m.get("/config", ""); local = host_to_local(conf_host); y = os.path.join(local, "config.yaml")
    ports = info.get("HostConfig", {}).get("PortBindings", {}).get("8080/tcp") or []
    v.update(apple_id=yaml_get(y, "username", u.get("apple_id", "")), sync_interval=yaml_get(y, "sync_interval", ""), remove_obsolete=yaml_get(y, "remove_obsolete", "false"),
             drive_host=m.get("/icloud/drive", ""), config_host=conf_host, running=info["State"]["Running"], health=info["State"].get("Health", {}).get("Status", ""), image=info["Config"]["Image"],
             webui_port=(ports[0].get("HostPort") if ports else ""), days=drive_days(local))
    if v["running"]:
        v["auth"] = d_auth_running(c)
        rc, last, _ = sh(f"docker logs --since 72h {c} 2>&1 | grep -E 'Downloading|completed|2FA|Retrying login|Syncing drive' | tail -1", 30); v["last"] = re.sub(r"^.*?:: (INFO|WARNING|ERROR) :: \S+ :: \S+ :: \d+ :: ", "", ANSI.sub("", last))[:140]
        v["cycles"] = sync_cycles(c, v["sync_interval"]); t = last_activity(c); v["activity"] = ago(t)
        # nessuna riga di log da più di due intervalli di sincronizzazione (+10 min) e nessun giro in corso: anche questo è un blocco
        quiet = t and (datetime.datetime.now() - t).total_seconds() > 2 * int(v["sync_interval"] or 1800) + 600
        v["stalled"] = any(x["stalled"] for x in v["cycles"]) or bool(quiet and not any(x["running_min"] is not None for x in v["cycles"]))
    return v
def photos_view(name, u):
    c = u["container"]; info = inspect(c); v = {"name": name, "type": "icloudpd", "label": u.get("label", name), "container": c, "chat_id": u.get("chat_id", ""), "exists": info is not None}
    if not info: return v
    e = env_of(info); conf = read_conf(conf_path(info) or "") ; g = lambda k, d="": conf.get(k) or e.get(k) or d
    v.update(apple_id=g("apple_id"), photo_library=g("photo_library", "PrimarySync"), photo_album=g("photo_album"), folder_structure=g("folder_structure"),
             tg_chat=g("telegram_chat_id"), notification_days=g("notification_days"), running=info["State"]["Running"], health=info["State"].get("Health", {}).get("Status", ""),
             photos_host=mounts(info).get("/home/user/iCloud", ""), config_host=mounts(info).get("/config", ""), image=info["Config"]["Image"])
    if v["running"]:
        v["days"] = dexec(c, "cat /config/DAYS_REMAINING 2>/dev/null")[1]; v["auth"] = auth_running(c)
        rc, last, _ = sh(f"docker logs --since 72h {c} 2>&1 | grep -E 'Downloaded /|Check successful|New files detected|Waiting for' | tail -1", 30); v["last"] = re.sub(r"^\S+ \S+ +\w+ +", "", ANSI.sub("", last))[:120]
        v["activity"] = ago(last_activity(c))
    return v

@app.route("/")
def index():
    c = cfg(); users = [user_view(n, u) for n, u in c["users"].items()]
    # Due sezioni per tipo: prima le foto (icloudpd), poi iCloud Drive; dentro ogni sezione l'ordine di config.json.
    sections = [("📷 Foto iCloud (icloudpd)", [v for v in users if v["type"] == "icloudpd"]), ("📁 iCloud Drive (mandarons/icloud-drive)", [v for v in users if v["type"] == "iclouddrive"])]
    sections = [sct for sct in sections if sct[1]]
    return render_template("index.html", sections=sections, cfg=c)
@app.post("/u/<name>/auth")
def u_auth(name):
    c = cfg(); u = c["users"].get(name) or abort(404)
    if utype(u) == "iclouddrive":
        if not u.get("apple_id"): flash(f"{name}: manca l'Apple ID in config.json"); return redirect(url_for("index"))
        d_start_auth(u["container"], u["apple_id"])
    else: start_auth(u["container"])
    telegram(u.get("chat_id") or c.get("default_chat_id"), f"🔐 {name}: riautenticazione avviata dalla GUI. Apple invia il codice a 6 cifre; rispondi con: #{name} 123456")
    flash(f"Riautenticazione avviata su {u['container']}: inserisci il codice Apple qui sotto o in Telegram con #{name} 123456"); return redirect(url_for("index"))
@app.post("/u/<name>/code")
def u_code(name):
    c = cfg(); u = c["users"].get(name) or abort(404); code = re.sub(r"\D", "", request.form.get("code", ""))
    if len(code) != 6: flash("Codice non valido: servono 6 cifre"); return redirect(url_for("index"))
    drive = utype(u) == "iclouddrive"
    if not (d_auth_running(u["container"]) if drive else auth_running(u["container"])): flash(f"{name}: nessuna autenticazione in corso, prima «Riautentica»"); return redirect(url_for("index"))
    (d_send_code if drive else send_code)(u["container"], code)
    if drive:
        time.sleep(20); rc, out, _ = dexec(u["container"], "cat /tmp/2fa.log 2>/dev/null | tail -3")
        if "EXIT=0" in out and not re.search(r"failed|invalid|error", out, re.I): sh(f"docker restart {u['container']}", 120); flash(f"{name}: sessione Apple rinnovata, container riavviato")
        else: flash(f"{name}: esito non ancora confermato ({out[-120:]}) — ricontrolla tra poco o riprova «Riautentica»")
        return redirect(url_for("index"))
    flash(f"Codice consegnato a {u['container']}: verifica tra ~30 s nei giorni rimanenti"); return redirect(url_for("index"))
@app.post("/u/<name>/<action>")
def u_power(name, action):
    """Azioni rapide sul container: avvia, ferma, riavvia (il riavvio fa partire subito un giro di sincronizzazione)."""
    if action not in ("start", "stop", "restart"): abort(404)
    u = cfg()["users"].get(name) or abort(404); rc, out, err = sh(f"docker {action} {u['container']}", 120)
    done = {"start": "avviato", "stop": "fermato", "restart": "riavviato: il giro di sincronizzazione riparte subito"}[action]
    flash(f"{u['container']} {done}" if rc == 0 else f"{u['container']}: docker {action} non riuscito ({err[-200:]})")
    back = request.form.get("back", ""); return redirect(back if back.startswith("/") and not back.startswith("//") else url_for("index"))
@app.route("/u/<name>/logs")
def u_logs(name):
    u = cfg()["users"].get(name) or abort(404); c = u["container"]; info = inspect(c) or abort(404)
    n = min(max(int(request.args.get("n", 300) or 300), 50), 5000); show_noise = request.args.get("all") == "1"; only_err = request.args.get("err") == "1"; q = request.args.get("q", "").strip()
    lines = container_logs(c, n); hidden = 0; rows = []
    for l in lines:
        if not show_noise and NOISE.search(l): hidden += 1; continue
        lvl = "bad" if re.search(r"\bERROR\b|Traceback|Exception", l) else "warn" if re.search(r"\bWARNING\b", l) else ""
        if only_err and lvl != "bad": continue
        if q and q.lower() not in l.lower(): continue
        rows.append((lvl, l))
    v = user_view(name, u)
    return render_template("logs.html", u=v, rows=rows, n=n, hidden=hidden, show_noise=show_noise, only_err=only_err, q=q, refresh=request.args.get("refresh", ""), now=datetime.datetime.now().strftime("%H:%M:%S"))
@app.route("/u/<name>/logs.txt")
def u_logs_txt(name):
    u = cfg()["users"].get(name) or abort(404)
    return "\n".join(container_logs(u["container"], 5000)) + "\n", 200, {"Content-Type": "text/plain; charset=utf-8", "Content-Disposition": f'attachment; filename="{u["container"]}.log"'}
@app.post("/u/<name>/settings")
def u_settings(name):
    c = cfg(); u = c["users"].get(name) or abort(404); f = request.form; info = inspect(u["container"]) or abort(404)
    if utype(u) == "iclouddrive":
        y = os.path.join(host_to_local(mounts(info).get("/config", "")), "config.yaml")
        if f.get("sync_interval", "").strip().isdigit(): yaml_set(y, "sync_interval", f["sync_interval"].strip())
        yaml_set(y, "remove_obsolete", "true" if f.get("remove_obsolete") == "on" else "false")
        u["label"] = f.get("label", u.get("label", name)).strip() or name; u["chat_id"] = f.get("telegram_chat_id", "").strip(); u["apple_id"] = f.get("apple_id", u.get("apple_id", "")).strip() or u.get("apple_id", ""); save(c)
        sh(f"docker restart {u['container']}", 120); flash(f"{name}: config.yaml aggiornato, container riavviato"); return redirect(url_for("index"))
    upd = {k: f.get(k, "").strip() for k in ("photo_library", "photo_album", "folder_structure", "telegram_chat_id", "notification_days")}
    upd = {k: v for k, v in upd.items() if v != "" or k in ("photo_album",)}
    write_conf(conf_path(info), upd)
    for k, v in upd.items(): tpl_set(u["container"], k, v)
    u["label"] = f.get("label", u.get("label", name)).strip() or name; u["chat_id"] = f.get("telegram_chat_id", "").strip(); save(c)
    sh(f"docker restart {u['container']}", 120); flash(f"{name}: impostazioni salvate (conf + template Unraid), container riavviato"); return redirect(url_for("index"))
@app.post("/u/<name>/delete")
def u_delete(name):
    c = cfg(); u = c["users"].pop(name, None) or abort(404); c["aliases"] = {a: n for a, n in c["aliases"].items() if n != name}; save(c)
    flash(f"{name}: tolto dalla GUI e dal bot (il container {u['container']} e le sue cartelle restano: gestiscili da Unraid)"); return redirect(url_for("index"))
@app.route("/new", methods=["GET", "POST"])
def new():
    c = cfg()
    if request.method == "GET": return render_template("new.html", cfg=c, bases=[u["container"] for u in c["users"].values() if inspect(u["container"])])
    f = request.form; name = re.sub(r"[^a-z0-9-]", "", f["name"].lower()); short = re.sub(r"[^A-Za-z0-9]", "", f["short"]) or name
    if f.get("type") == "iclouddrive": return new_drive(c, f, name, short)
    cont = f"iCloudpd-{short}"; base = f["base"]; apple = f["apple_id"].strip(); chat = f.get("chat_id", "").strip(); lib = f.get("photo_library", "PrimarySync").strip() or "PrimarySync"
    if not name or not apple or name in c["users"]: flash("Dati mancanti o nome già usato"); return redirect(url_for("new"))
    p = c["paths"]; d = c["defaults"]; appdata = f"{p['appdata']}/icloudpd-{short.lower()}"; photos = f"{p['photos']}/{short}"
    os.makedirs(appdata, exist_ok=True); os.makedirs(photos, exist_ok=True); shutil.chown(appdata, int(d["user_id"]), int(d["group_id"])); shutil.chown(photos, int(d["user_id"]), int(d["group_id"]))
    binfo = inspect(base); benv = env_of(binfo); bm = mounts(binfo)
    env = {**{k: v for k, v in benv.items() if k not in ("PATH", "HOME", "HOSTNAME")}, "apple_id": apple, "photo_library": lib, "photo_album": f.get("photo_album", "").strip(), "TZ": d["tz"],
           "folder_structure": f.get("folder_structure", d["folder_structure"]).strip() or d["folder_structure"], "user": d["user"], "user_id": d["user_id"], "group": d["group"], "group_id": d["group_id"],
           "notification_type": "telegram", "telegram_token": TOKEN, "telegram_chat_id": chat or benv.get("telegram_chat_id", ""), "telegram_polling": "false", "notification_days": d["notification_days"], "download_path": "/home/user/iCloud"}
    host_appdata = appdata.replace(p["appdata"], "/mnt/user/appdata", 1); host_photos = photos.replace(p["photos"], "/mnt/user/media/photos", 1)
    # template Unraid dal container base
    bt = tpl_path(base)
    if os.path.exists(bt):
        s = open(bt).read(); s = re.sub(r"<Name>[^<]*</Name>", f"<Name>{cont}</Name>", s)
        s = s.replace(bm["/config"], host_appdata).replace(bm["/home/user/iCloud"].rstrip("/"), host_photos)
        open(tpl_path(cont), "w").write(s)
        for k in ("apple_id", "photo_library", "photo_album", "folder_structure", "telegram_chat_id", "notification_days", "user", "user_id", "group", "group_id", "TZ"): tpl_set(cont, k, env[k])
    envs = " ".join(f"-e {json.dumps(k + '=' + v)}" for k, v in env.items())
    labels = f'-l net.unraid.docker.managed=dockerman -l net.unraid.docker.icon={json.dumps(binfo["Config"]["Labels"].get("net.unraid.docker.icon", ""))}'
    rc, out, err = sh(f"docker run -d --name {cont} --restart=no --net=bridge {labels} {envs} -v {host_appdata}:/config -v {host_photos}:/home/user/iCloud {binfo['Config']['Image']}", 120)
    if rc != 0: flash(f"docker run fallito: {err[-300:]}"); return redirect(url_for("new"))
    c["users"][name] = {"container": cont, "label": f.get("label", name).strip() or name, "chat_id": chat}; c["aliases"][short.lower()] = name; save(c)
    flash(f"{cont} creato e avviato: ora il primo login Apple. Da Unraid: docker exec -it {cont} sync-icloud.sh --Initialise (password + codice 2FA). Autostart: attivalo dalla pagina Docker di Unraid.")
    return redirect(url_for("index"))
def new_drive(c, f, name, short):
    """Nuovo container iCloud Drive (mandarons/icloud-drive): cartelle, config.yaml, template Unraid, container. Primo login manuale."""
    apple = f["apple_id"].strip(); chat = f.get("chat_id", "").strip(); label = f.get("label", name).strip() or name
    dest_host = (f.get("drive_dest", "") or f"/mnt/user/documents/{label}/iCloud Drive").strip().rstrip("/")
    if not name or not apple or name in c["users"]: flash("Dati mancanti o nome già usato"); return redirect(url_for("new"))
    p = c["paths"]; d = c["defaults"]; cont = f"iclouddr-{short.lower()}"; host_appdata = f"/mnt/user/appdata/{cont}"
    appdata = host_to_local(host_appdata); dest = host_to_local(dest_host)
    if dest == dest_host: flash("La destinazione deve stare sotto /mnt/user/documents (montato nel dispatcher come /documents)"); return redirect(url_for("new"))
    for path in (appdata, dest):
        os.makedirs(path, exist_ok=True); shutil.chown(path, int(d["user_id"]), int(d["group_id"]))
    open(os.path.join(appdata, "config.yaml"), "w").write(f"""# {cont} — iCloud Drive di {label} → {dest_host} (solo download, {d.get('drive_image', 'mandarons/icloud-drive')})
app:
  logger:
    level: "info"
    filename: "/config/icloud.log"
  credentials:
    username: "{apple}"
    retry_login_interval: 600
  root: "/icloud"
  region: global
drive:
  destination: "drive"
  remove_obsolete: false
  sync_interval: {d.get('drive_sync_interval', 1800)}
"""); shutil.chown(os.path.join(appdata, "config.yaml"), int(d["user_id"]), int(d["group_id"]))
    icon = "https://raw.githubusercontent.com/sidimam/icloud-dispatcher/main/assets/icloud-drive.png"   # quadrata 256x256 (l'icona dashboard-icons è 797x512 e Unraid la deforma)
    open(tpl_path(cont), "w").write(f"""<?xml version="1.0"?>
<Container version="2">
  <Name>{cont}</Name>
  <Repository>{d.get('drive_image', 'mandarons/icloud-drive')}</Repository>
  <Registry>https://hub.docker.com/r/mandarons/icloud-drive</Registry>
  <Network>bridge</Network>
  <Shell>sh</Shell>
  <Privileged>false</Privileged>
  <Support>https://github.com/mandarons/icloud-docker</Support>
  <Project>https://github.com/mandarons/icloud-docker</Project>
  <Overview>Scarica iCloud Drive di {html.escape(label)} in {html.escape(dest_host)} (solo download). Primo login: docker exec -it {cont} /bin/sh -c "su-exec abc icloud --username={apple} --session-directory=/config/session_data"</Overview>
  <Category>Backup: Cloud:</Category>
  <WebUI/>
  <Icon>{icon}</Icon>
  <ExtraParams>--label net.unraid.docker.managed=dockerman</ExtraParams>
  <Config Name="Config" Target="/config" Default="{host_appdata}" Mode="rw" Description="config.yaml, sessione 2FA, log" Type="Path" Display="always" Required="true" Mask="false">{host_appdata}</Config>
  <Config Name="iCloud Drive" Target="/icloud/drive" Default="{html.escape(dest_host)}" Mode="rw" Description="Destinazione dei file" Type="Path" Display="always" Required="true" Mask="false">{html.escape(dest_host)}</Config>
  <Config Name="Config file" Target="ENV_CONFIG_FILE_PATH" Default="/config/config.yaml" Mode="" Description="Senza questa variabile l'app legge /app/config.yaml" Type="Variable" Display="advanced" Required="true" Mask="false">/config/config.yaml</Config>
  <Config Name="PUID" Target="PUID" Default="99" Mode="" Description="" Type="Variable" Display="advanced" Required="false" Mask="false">{d["user_id"]}</Config>
  <Config Name="PGID" Target="PGID" Default="100" Mode="" Description="" Type="Variable" Display="advanced" Required="false" Mask="false">{d["group_id"]}</Config>
  <Config Name="TZ" Target="TZ" Default="Europe/Rome" Mode="" Description="" Type="Variable" Display="advanced" Required="false" Mask="false">{d["tz"]}</Config>
</Container>
""")
    rc, out, err = sh(f"docker run -d --name {cont} --restart unless-stopped --label net.unraid.docker.managed=dockerman -e PUID={d['user_id']} -e PGID={d['group_id']} -e TZ={d['tz']} -e ENV_CONFIG_FILE_PATH=/config/config.yaml -v {host_appdata}:/config -v {json.dumps(dest_host)}:/icloud/drive {d.get('drive_image', 'mandarons/icloud-drive')}", 180)
    if rc != 0: flash(f"docker run fallito: {err[-300:]}"); return redirect(url_for("new"))
    c["users"][name] = {"type": "iclouddrive", "container": cont, "label": label, "chat_id": chat, "apple_id": apple}; c["aliases"][short.lower() + "-drive"] = name; save(c)
    flash(f"{cont} creato. Primo login Apple dal terminale di Unraid: docker exec -it {cont} /bin/sh -c \"su-exec abc icloud --username={apple} --session-directory=/config/session_data\" — password, codice, y al keyring. Poi #{name} auth / #{name} 123456 funzionano dal bot.")
    return redirect(url_for("index"))
@app.route("/healthz")
def healthz(): return "ok"
if __name__ == "__main__": app.run(host="0.0.0.0", port=PORT, debug=False)
