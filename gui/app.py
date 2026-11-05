#!/usr/bin/env python3
"""iCloud Dashboard (ex icloud-dispatcher) GUI: utenti = container iCloud (foto: boredazfcuk/icloudpd; iCloud Drive: mandarons/icloud-drive), chat Telegram, cosa scaricare, rinnovo 2FA."""
import json, os, re, secrets, subprocess, shutil, datetime, html, time, threading, sqlite3, email, email.policy
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, redirect, url_for, render_template, flash, abort, session, copy_current_request_context

APP_DIR = os.environ.get("APP_DIR", "/app"); CFG = os.path.join(APP_DIR, "config.json")
TOKEN = os.environ.get("TELEGRAM_TOKEN", ""); PORT = int(os.environ.get("GUI_PORT", "8787"))
app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), "templates"))
# ---- accesso (3.0): profili in /app/state/profiles.json (vedi accounts.py), password + 2FA TOTP facoltativo per profilo.
# GUI_RESET_PASSWORD, se compilata, all'avvio reimposta la password dell'admin e gli spegne il 2FA: poi va svuotata.
import accounts, ui
from ui import _
app.jinja_env.globals.update(_=_, icon=ui.icon, cur_lang=ui.current_lang, cur_theme=ui.current_theme, LANGS=ui.LANGS)
GUI_USER = accounts.ADMIN; GUI_RESET = os.environ.get("GUI_RESET_PASSWORD", "")
accounts.bootstrap(GUI_RESET)
def _secret():   # chiave di sessione persistente in /app/state: un riavvio non disconnette
    f = os.path.join(APP_DIR, "state", "gui_secret")
    try: return open(f, "rb").read()
    except FileNotFoundError:
        os.makedirs(os.path.dirname(f), exist_ok=True); k = os.urandom(32); open(f, "wb").write(k); os.chmod(f, 0o600); return k
app.secret_key = _secret()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=30))
FAILS = {}   # ip -> [orari dei tentativi falliti]: oltre 10 in 10 minuti l'ip aspetta
def me(): return session.get("user") if session.get("user") and accounts.get(session["user"]) else None
def _ip(): return request.headers.get("CF-Connecting-IP") or request.remote_addr
def _throttled():
    now = time.time(); r = FAILS[_ip()] = [t for t in FAILS.get(_ip(), []) if now - t < 600]; return len(r) >= 10
def _fail(): FAILS.setdefault(_ip(), []).append(time.time()); time.sleep(1)
def _safe_next(n): return n if n and n.startswith("/") and not n.startswith("//") else "/"
@app.before_request
def require_login():
    if request.endpoint in ("login", "login_2fa", "recover", "prefs", "healthz", "static") or not accounts.any_password(): return None
    if not me(): return redirect(url_for("login", next=request.full_path if request.method == "GET" else "/"))
    # Ruoli (3.4): l'amministratore vede tutto; l'utente solo il proprio container iCloud e il proprio profilo.
    if accounts.is_admin(me()): return None
    if request.endpoint in ("new", "admin", "admin_profile", "admin_profile_new", "admin_notify", "admin_notify_test", "admin_reindex", "admin_geocode"): abort(403)
    name = (request.view_args or {}).get("name")
    if request.endpoint and request.endpoint.startswith("u_") and name != _own_icloud(): abort(403)
    return None
def _own_icloud():
    """Utente iCloud del profilo: quello il cui container è iclouddr-<profilo>."""
    return _icloud_of(me()) if me() else ""
def _complete_login(name, remember):
    session.clear(); session["user"] = name; session.permanent = remember; FAILS.pop(_ip(), None); accounts.update(name, last_login=time.time())
@app.route("/login", methods=["GET", "POST"])
def login():
    nxt = _safe_next(request.values.get("next", "/"))
    if request.method == "POST":
        if _throttled(): flash(_("Troppi tentativi: riprova fra qualche minuto")); return render_template("login.html", next=nxt), 429
        name = request.form.get("user", "").strip().lower(); remember = request.form.get("remember") == "on"
        if accounts.check_password(name, request.form.get("password", "")):
            p = accounts.get(name)
            if p.get("totp_on"):
                session.clear(); session["pending"] = name; session["pending_remember"] = remember; session["pending_ts"] = time.time()
                return redirect(url_for("login_2fa", next=nxt))
            _complete_login(name, remember); return redirect(nxt)
        _fail(); flash(_("Utente o password errati"))
    return render_template("login.html", next=nxt)
@app.route("/login/2fa", methods=["GET", "POST"])
def login_2fa():
    nxt = _safe_next(request.values.get("next", "/")); name = session.get("pending")
    if not name or time.time() - session.get("pending_ts", 0) > 300: session.clear(); flash(_("Accedi di nuovo")); return redirect(url_for("login", next=nxt))
    if request.method == "POST":
        if _throttled(): flash(_("Troppi tentativi: riprova fra qualche minuto")); return render_template("login_2fa.html", next=nxt), 429
        code = request.form.get("code", "")
        if accounts.verify_totp(accounts.get(name).get("totp", ""), code):
            _complete_login(name, session.get("pending_remember", False)); return redirect(nxt)
        if accounts.use_recovery_code(name, code):
            left = len(accounts.get(name).get("recovery", []))
            _security_alert(f"🔑 iCloud Dashboard: {name} è entrato con un codice di recupero (ne restano {left}).")
            _complete_login(name, session.get("pending_remember", False))
            flash(_("Sei entrato con un codice di recupero: ne restano {a0}. Se hai perso il telefono, genera codici nuovi o disattiva il 2FA da «Il mio profilo».", a0=left)); return redirect(nxt)
        _fail(); flash(_("Codice non valido"))
    return render_template("login_2fa.html", next=nxt, user=name)
@app.post("/logout")
def logout(): session.clear(); return redirect(url_for("login"))
@app.post("/prefs")
def prefs():
    """Tema (sistema/chiaro/scuro) e lingua: nel profilo se si è entrati, sempre anche in un cookie (vale per la pagina di accesso)."""
    th = request.form.get("theme"); lg = request.form.get("lang"); resp = redirect(_safe_next(request.form.get("back", "/")))
    if th in ui.THEMES:
        resp.set_cookie("theme", th, max_age=3 * 365 * 86400, samesite="Lax", httponly=True)
        if me(): accounts.update(me(), theme=th)
    if lg in ui.LANGS:
        resp.set_cookie("lang", lg, max_age=3 * 365 * 86400, samesite="Lax", httponly=True)
        if me(): accounts.update(me(), lang=lg)
    return resp
@app.route("/password")
def password(): return redirect(url_for("account"))
@app.route("/account", methods=["GET", "POST"])
def account():
    name = me() or accounts.ADMIN; p = accounts.get(name) or {}
    if request.method == "POST":
        f = request.form; act = f.get("action")
        if act == "password":
            if p.get("pw") and not accounts.check_password(name, f.get("current", "")): _fail(); flash(_("La password attuale non è corretta"))
            elif len(f.get("new", "")) < 8: flash(_("La nuova password deve avere almeno 8 caratteri"))
            elif f.get("new") != f.get("confirm"): flash(_("Le due password nuove non coincidono"))
            else: accounts.set_password(name, f["new"]); flash(_("Password cambiata"))
        elif act == "totp_start":
            session["totp_new"] = accounts.new_secret()
        elif act == "totp_confirm":
            sec = session.get("totp_new", "")
            if accounts.verify_totp(sec, f.get("code", "")):
                accounts.update(name, totp=sec, totp_on=True); session.pop("totp_new", None); flash(_("Verifica in due passaggi attivata"))
                return render_template("codes.html", codes=accounts.new_recovery_codes(name), name=name)
            else: flash(_("Codice non valido: controlla l'ora del telefono e riprova"))
        elif act == "totp_off":
            if accounts.check_password(name, f.get("current", "")): accounts.update(name, totp="", totp_on=False); flash(_("Verifica in due passaggi disattivata"))
            else: _fail(); flash(_("Password non corretta"))
        elif act == "totp_cancel": session.pop("totp_new", None)
        elif act == "recovery_new":
            if accounts.check_password(name, f.get("current", "")): return render_template("codes.html", codes=accounts.new_recovery_codes(name), name=name)
            _fail(); flash(_("Password non corretta"))
        elif act == "tg_send":
            chat = f.get("chat", "").strip()
            if not re.fullmatch(r"\d{4,15}", chat): flash(_("La chat privata è un numero (es. 141880239): i gruppi non vanno bene per il recupero"))
            elif not TOKEN: flash(_("Manca il token del bot"))
            else:
                code = f"{secrets.randbelow(10**6):06d}"; session["tg_new"] = {"chat": chat, "code": code, "exp": time.time() + 600}
                telegram(chat, f"🔐 iCloud Dashboard: codice per collegare questa chat al profilo {name}: {code}"); flash(_("Ti ho mandato un codice su Telegram: scrivilo qui sotto"))
        elif act == "tg_confirm":
            t = session.get("tg_new") or {}
            if t and time.time() < t["exp"] and f.get("code", "").strip() == t["code"]: accounts.update(name, telegram=t["chat"]); session.pop("tg_new", None); flash(_("Chat Telegram collegata: potrai usarla per recuperare l'accesso"))
            else: flash(_("Codice non valido o scaduto"))
        elif act == "tg_remove": accounts.update(name, telegram=""); flash(_("Chat Telegram scollegata"))
        return redirect(url_for("account"))
    sec = session.get("totp_new"); qr = accounts.totp_qr_svg(accounts.totp_uri(name, sec)) if sec else ""
    return render_template("account.html", name=name, p=accounts.get(name) or {}, totp_new=sec, qr=qr, icloud=_icloud_of(name), tg_pending=bool(session.get("tg_new")))
# ---- recupero accesso (3.0): codice a 6 cifre alla chat Telegram privata del profilo, valido 10 minuti, 5 tentativi.
# La risposta al primo passo è sempre la stessa, così dalla pagina non si capisce quali profili esistono.
RECOVER = {}   # profilo -> {"code": hash, "exp": ts, "tries": n}
def _security_alert(text):
    try:
        chat = notify_chat(cfg())
        if chat: telegram(chat, text)
    except Exception: pass
@app.route("/recover", methods=["GET", "POST"])
def recover():
    f = request.form; step = f.get("step", "start")
    if request.method == "POST" and _throttled(): flash(_("Troppi tentativi: riprova fra qualche minuto")); return render_template("recover.html", step="start"), 429
    if request.method == "POST" and step == "start":
        name = f.get("user", "").strip().lower(); p = accounts.get(name)
        if p and p.get("telegram") and TOKEN:
            code = f"{secrets.randbelow(10**6):06d}"; RECOVER[name] = {"code": generate_password_hash(code), "exp": time.time() + 600, "tries": 0}
            telegram(p["telegram"], f"🔐 iCloud Dashboard: codice di recupero per {name}: {code} (valido 10 minuti). Se non l'hai chiesto tu, ignoralo e avvisa Simone.")
        else: _fail()
        flash(_("Se il profilo ha una chat Telegram collegata, il bot ti ha appena mandato un codice.")); return render_template("recover.html", step="finish", user=name)
    if request.method == "POST" and step == "finish":
        name = f.get("user", "").strip().lower(); r = RECOVER.get(name)
        if not r or time.time() > r["exp"] or r["tries"] >= 5: RECOVER.pop(name, None); flash(_("Codice scaduto: ricomincia")); return render_template("recover.html", step="start")
        r["tries"] += 1
        if not check_password_hash(r["code"], f.get("code", "").strip()): _fail(); flash(_("Codice non valido")); return render_template("recover.html", step="finish", user=name)
        if f.get("new") and (len(f["new"]) < 8 or f["new"] != f.get("confirm")): flash(_("Password nuova: almeno 8 caratteri e uguale nei due campi")); return render_template("recover.html", step="finish", user=name)
        done = []
        if f.get("new"): accounts.set_password(name, f["new"]); done.append("password cambiata")
        if f.get("totp_off") == "on": accounts.update(name, totp="", totp_on=False, recovery=[]); done.append("verifica in due passaggi disattivata")
        shown = [_(x) for x in done]
        RECOVER.pop(name, None)
        if not done: flash(_("Codice giusto, ma non hai chiesto niente: scegli una password nuova o spegni il 2FA")); return render_template("recover.html", step="start")
        _security_alert(f"🔐 iCloud Dashboard: recupero dell'accesso per {name} ({', '.join(done)}).")
        flash(_("Fatto: {a0}. Ora puoi entrare.", a0=", ".join(shown))); return redirect(url_for("login"))
    return render_template("recover.html", step="start")
def _icloud_of(profile):
    try: return next((n for n, u in cfg()["users"].items() if u.get("container") == f"iclouddr-{profile}"), "")
    except Exception: return ""
@app.context_processor
def page_ctx():
    n = me(); return {"reset_set": bool(GUI_RESET), "me": n, "me_admin": bool(n and accounts.is_admin(n)), "fmt_ts": lambda t: datetime.datetime.fromtimestamp(t).strftime("%d/%m/%Y %H:%M") if t else "mai"}

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
# Ogni messaggio del bot e della GUI inizia con il mittente (5/11/2026): in casa scrivono anche HA, Unraid, Proxmox, Uptime Kuma.
TG_PREFIX = os.environ.get("TELEGRAM_PREFIX", "ICLOUD:").strip()
def telegram(chat, text):
    if TG_PREFIX and not text.startswith(TG_PREFIX): text = f"{TG_PREFIX} {text}"
    if TOKEN and chat: sh(f"curl -s -X POST https://api.telegram.org/bot{TOKEN}/sendMessage -d chat_id={chat} --data-urlencode text={json.dumps(text)} >/dev/null", 20)
# ---- notifiche automatiche (2.6): chat, tipi e ore silenziose dalle variabili del template. Le risposte ai comandi del bot e della GUI
# non passano di qui e arrivano sempre subito. Nelle ore silenziose gli avvisi si accodano in /app/state/notify_queue.json e
# partono tutti insieme in un solo messaggio appena finiscono.
NOTIFY_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
NOTIFY_RESTART = os.environ.get("NOTIFY_AUTOHEAL_RESTART", "1") != "0"; NOTIFY_GAVE_UP = os.environ.get("NOTIFY_AUTOHEAL_GAVE_UP", "1") != "0"
QUIET = os.environ.get("NOTIFY_QUIET_HOURS", "23:00-07:30").strip(); NQ_FILE = os.path.join(APP_DIR, "state", "notify_queue.json")
# Dalla 3.0 le stesse impostazioni si cambiano anche dalla scheda Amministrazione (/app/state/settings.json): quelle salvate lì
# hanno la precedenza, le variabili del template restano i valori iniziali.
SETTINGS_FILE = os.path.join(APP_DIR, "state", "settings.json")
def nset():
    try: s = json.load(open(SETTINGS_FILE)).get("notify", {})
    except (FileNotFoundError, ValueError): s = {}
    return {"chat": s.get("chat", NOTIFY_CHAT), "restart": s.get("restart", NOTIFY_RESTART), "gave_up": s.get("gave_up", NOTIFY_GAVE_UP), "quiet": s.get("quiet", QUIET)}
def save_nset(v):
    try: d = json.load(open(SETTINGS_FILE))
    except (FileNotFoundError, ValueError): d = {}
    d["notify"] = v; os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True); json.dump(d, open(SETTINGS_FILE, "w"), indent=1, ensure_ascii=False)
def notify_chat(c): return nset()["chat"] or c.get("default_chat_id", "")
def in_quiet(now=None):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", nset()["quiet"].strip())
    if not m: return False
    a, b = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4]); now = now or datetime.datetime.now(); t = now.hour * 60 + now.minute
    return a <= t < b if a <= b else (t >= a or t < b)
def _nq():
    try: return json.load(open(NQ_FILE))
    except (FileNotFoundError, ValueError): return []
def notify(c, text):
    chat = notify_chat(c)
    if not chat: return
    if in_quiet():
        q = (_nq() + [{"ts": time.time(), "text": text}])[-50:]; os.makedirs(os.path.dirname(NQ_FILE), exist_ok=True); json.dump(q, open(NQ_FILE, "w"), ensure_ascii=False)
    else: telegram(chat, text)
def notify_flush(c):
    q = _nq()
    if not q or in_quiet() or not notify_chat(c): return
    body = "\n\n".join(f"{datetime.datetime.fromtimestamp(x['ts']).strftime('%H:%M')} {x['text']}" for x in q)
    telegram(notify_chat(c), ("🌙 Avvisi della notte (iCloud Dashboard):\n\n" + body)[:4000]); os.remove(NQ_FILE)
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
def yaml_section_get(path, section, key, default=""):
    """Valore di `key` dentro la sezione di primo livello `section` di config.yaml (es. photos → sync_interval)."""
    cur = None
    try:
        for line in open(path):
            top = re.match(r"^([A-Za-z_]+):\s*$", line)
            if top: cur = top.group(1); continue
            m = re.match(r"\s+%s:\s*(.*?)\s*$" % re.escape(key), line)
            if m and cur == section: return m.group(1).strip().strip('"')
    except FileNotFoundError: pass
    return default
def every(sec):
    try: s = int(sec)
    except (TypeError, ValueError): return ""
    return _("ogni {a0} min", a0=s // 60) if s < 3600 else _("ogni {a0} h", a0=round(s / 3600, 1)).replace(".0 ", " ")
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
    return _("adesso") if m < 1 else _("{a0} min fa", a0=m) if m < 120 else _("{a0} h {a1} min fa", a0=m // 60, a1=f"{m % 60:02d}") if m < 2880 else _("{a0} giorni fa", a0=m // 1440)
def container_logs(c, n=300):
    rc, out, err = sh(f"docker logs --tail {int(n)} {c} 2>&1", 60)
    return [ANSI.sub("", l) for l in (out or err).splitlines()]
def last_activity(c):
    for l in reversed(container_logs(c, 20)):
        t = log_ts(l)
        if t: return t
    return None
# I log dei container iclouddr arrivano a 50 MB (decine di migliaia di avvisi «file già scaricato» al giorno): docker logs li rilegge
# tutti a ogni chiamata. Una sola lettura filtrata per container, tenuta 15 s (le azioni rapide la invalidano).
DRIVE_PAT = "|".join(re.escape(x) for _, a, b in CYCLES for x in (a, b)) + "|Downloading|completed|2FA|Retrying login"
_log_cache = {}
def drive_log_lines(c):
    hit = _log_cache.get(c)
    if hit and time.time() - hit[0] < 15: return hit[1]
    rc, out, _ = sh(f"docker logs --since 72h {c} 2>&1 | grep -E {json.dumps(DRIVE_PAT)}", 60)
    lines = ANSI.sub("", out).splitlines(); _log_cache[c] = (time.time(), lines); return lines
def sync_cycles(c, interval):
    """Per ogni giro (Drive, Foto): in corso da quando, fine e durata dell'ultimo completo, e se sembra bloccato
    (in corso da più del doppio dell'ultimo giro completo, minimo un'ora)."""
    out = "\n".join(drive_log_lines(c))
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
        last = next((l for l in reversed(drive_log_lines(c)) if re.search("Downloading|completed|2FA|Retrying login|Syncing drive", l)), "")
        v["last"] = re.sub(r"^.*?:: (INFO|WARNING|ERROR) :: \S+ :: \S+ :: \d+ :: ", "", last)[:140]
        v["cycles"] = sync_cycles(c, v["sync_interval"]); t = last_activity(c); v["activity"] = ago(t)
        # nessuna riga di log da più di due intervalli di sincronizzazione (+10 min) e nessun giro in corso: anche questo è un blocco
        quiet = t and (datetime.datetime.now() - t).total_seconds() > 2 * int(v["sync_interval"] or 1800) + 600
        v["stalled"] = any(x["stalled"] for x in v["cycles"]) or bool(quiet and not any(x["running_min"] is not None for x in v["cycles"]))
    v["autoheal"] = ah_view(c); v["autoheal_on"] = AH_ON
    try:   # modulo PIM dell'immagine sidimam/iclouddr: stato dell'ultimo giro (note, promemoria)
        ps = json.load(open(os.path.join(local, "pim-status.json")))
        v["pim"] = {"ok": ps.get("ok"), "when": ago(datetime.datetime.fromtimestamp(ps.get("finished") or ps.get("started"))),
                    "seconds": ps.get("seconds"), "note": (ps.get("note") or {}).get("note"), "protette": (ps.get("note") or {}).get("protette"),
                    "promemoria": (ps.get("promemoria") or {}).get("promemoria"), "liste": (ps.get("promemoria") or {}).get("liste"),
                    "error": ps.get("error"), "dest": m.get("/icloud/pim", "")}
    except (FileNotFoundError, ValueError, TypeError): v["pim"] = None
    # «Cosa scarica»: una riga per servizio iCloud, attivo o no, con cartella, frequenza e ultimo giro.
    cyc = {x["kind"]: x for x in v.get("cycles") or []}
    def cyc_txt(kind):
        g = cyc.get(kind)
        if not g: return ""
        if g["running_min"] is not None: return _("in corso da {a0} min", a0=g["running_min"])
        return _("ultimo giro {a0}", a0=g["last_end"])
    photo_paths = [src for dst, src in sorted(m.items()) if dst.startswith("/icloud/photos")]
    pim = v["pim"]; pim_on = "/icloud/pim" in m
    svc = [{"name": "iCloud Drive", "icon": "folder", "badge": "b-blue", "on": True, "paths": [m.get("/icloud/drive", "")],
            "freq": every(v.get("sync_interval")), "last": cyc_txt("Drive"),
            "info": _("tutto il Drive: Scrivania, Documenti, Download e cartelle delle app")},
           {"name": _("Foto"), "icon": "photo", "badge": "b-orange", "on": bool(photo_paths), "paths": photo_paths,
            "freq": every(yaml_section_get(y, "photos", "sync_interval")) if photo_paths else "", "last": cyc_txt("Foto"),
            "info": "" if photo_paths else _("non attivo")}]
    note_dir = (m.get("/icloud/pim", "") + "/Note") if pim_on else ""
    svc += [{"name": _("Note"), "icon": "doc", "badge": "b-orange", "on": bool(pim_on and pim), "paths": [note_dir] if pim_on else [],
             "freq": every(env_of(info).get("PIM_INTERVAL", "21600")) if pim_on else "", "last": _("ultimo giro {a0}", a0=pim["when"]) if pim else "",
             "info": (_("{a0} note", a0=pim["note"]) if pim and pim.get("ok") else (pim or {}).get("error") or "") if pim_on else _("non attivo: serve l'immagine sidimam/iclouddr"),
             "err": bool(pim and not pim.get("ok"))},
            {"name": _("Promemoria"), "icon": "check", "badge": "b-red", "on": bool(pim_on and pim), "paths": [m.get("/icloud/pim", "") + "/Promemoria"] if pim_on else [],
             "freq": every(env_of(info).get("PIM_INTERVAL", "21600")) if pim_on else "", "last": _("ultimo giro {a0}", a0=pim["when"]) if pim else "",
             "info": (_("{a0} promemoria in {a1} liste", a0=pim["promemoria"], a1=pim["liste"]) if pim and pim.get("ok") else "") if pim_on else _("non attivo: serve l'immagine sidimam/iclouddr"),
             "err": bool(pim and not pim.get("ok"))}]
    # account collegati e loro stato
    accs = acc_load(u) if pim_on or "/icloud/persona" in m else []
    st_acc = {}
    try: st_acc = json.load(open(os.path.join(local, "pim-status.json"))).get("accounts", {})
    except (FileNotFoundError, ValueError): pass
    person = m.get("/icloud/persona", "")
    v["can_accounts"] = bool(env_of(info) and info["Config"]["Image"].startswith("ghcr.io/sidimam/iclouddr") and (person or pim_on))
    v["person_dir"] = person
    v["accounts"] = []
    for a in accs:
        stt = st_acc.get(a["id"], {})
        v["accounts"].append({**{k: a.get(k) for k in ("id", "type", "label", "email", "mail", "calendar", "contacts", "ics", "exclude", "imap_host", "imap_port")},
                              "has_pw": bool(a.get("password")), "type_name": ACC_TYPES.get(a.get("type"), a.get("type")),
                              "dir": (person + "/" + a.get("label", "")) if person else stt.get("dir", ""), "st": stt, "error": stt.get("error")})
    def agg(key, flag, unit_key, unit_fmt):
        rows = [a for a in v["accounts"] if a.get(flag) or (key == "calendar" and a.get("ics"))]
        if not rows: return False, [], "", _("non ancora attivo: aggiungi un account qui sotto")
        parts = []; last = 0
        for a in rows:
            r = a["st"].get(key) or {}
            if a["error"] and not r: parts.append(f'{a["label"]}: ' + _("errore"))
            elif r: parts.append(f'{a["label"]}: ' + unit_fmt(r)); last = max(last, r.get("when") or 0)
            else: parts.append(f'{a["label"]}: ' + _("in attesa del primo giro"))
        return True, [a["dir"] + "/" + unit_key for a in rows if a["dir"]], (_("ultimo giro {a0}", a0=ago(datetime.datetime.fromtimestamp(last))) if last else ""), " · ".join(parts)
    for key, flag, folder, name, icon_, badge, freq, fmt in (
            ("mail", "mail", "Posta", _("Posta"), "send", "b-blue", env_of(info).get("PIM_MAIL_INTERVAL", "900"), lambda r: _("{a0} messaggi", a0=r.get("messaggi", 0))),
            ("calendar", "calendar", "Calendario", _("Calendario"), "doc", "b-red", env_of(info).get("PIM_DAV_INTERVAL", "3600"), lambda r: _("{a0} eventi", a0=r.get("eventi", 0)) + (f' + {r["link_ics"]} ICS' if r.get("link_ics") else "")),
            ("contacts", "contacts", "Contatti", _("Contatti"), "users", "b-gray", env_of(info).get("PIM_DAV_INTERVAL", "3600"), lambda r: _("{a0} contatti", a0=r.get("contatti", 0)))):
        on, paths, last, txt = agg(key, flag, folder, fmt)
        svc.append({"name": name, "icon": icon_, "badge": badge, "on": on, "paths": paths, "freq": every(freq) if on else "", "last": last, "info": txt,
                    "err": any(a["error"] for a in v["accounts"] if a.get(flag))})
    v["services"] = svc
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
    c = cfg()
    mine = c["users"].items() if (not accounts.any_password() or accounts.is_admin(me())) else [(n, u) for n, u in c["users"].items() if n == _own_icloud()]
    # i thread ricevono una copia della richiesta, così lingua e profilo valgono anche per i testi calcolati lì
    jobs = [copy_current_request_context(lambda nu=nu: user_view(*nu)) for nu in mine]   # una copia per utente
    with ThreadPoolExecutor(max_workers=8) as ex: users = list(ex.map(lambda f: f(), jobs))
    # Due sezioni per tipo: prima le foto (icloudpd), poi iCloud Drive; dentro ogni sezione l'ordine di config.json.
    sections = [("Foto iCloud (icloudpd)", [v for v in users if v["type"] == "icloudpd"]), ("iCloud Drive (mandarons/icloud-drive)", [v for v in users if v["type"] == "iclouddrive"])]
    sections = [sct for sct in sections if sct[1]]
    return render_template("index.html", sections=sections, cfg=c)
@app.post("/u/<name>/auth")
def u_auth(name):
    c = cfg(); u = c["users"].get(name) or abort(404)
    if utype(u) == "iclouddrive":
        if not u.get("apple_id"): flash(_("{a0}: manca l'Apple ID in config.json", a0=name)); return redirect(url_for("index"))
        d_start_auth(u["container"], u["apple_id"])
    else: start_auth(u["container"])
    telegram(u.get("chat_id") or c.get("default_chat_id"), f"🔐 {name}: riautenticazione avviata dalla GUI. Apple invia il codice a 6 cifre; rispondi con: #{name} 123456")
    flash(_("Riautenticazione avviata su {a0}: inserisci il codice Apple qui sotto o in Telegram con #{a1} 123456", a0=u['container'], a1=name)); return redirect(url_for("index"))
@app.post("/u/<name>/code")
def u_code(name):
    c = cfg(); u = c["users"].get(name) or abort(404); code = re.sub(r"\D", "", request.form.get("code", ""))
    if len(code) != 6: flash(_("Codice non valido: servono 6 cifre")); return redirect(url_for("index"))
    drive = utype(u) == "iclouddrive"
    if not (d_auth_running(u["container"]) if drive else auth_running(u["container"])): flash(_("{a0}: nessuna autenticazione in corso, prima «Riautentica»", a0=name)); return redirect(url_for("index"))
    (d_send_code if drive else send_code)(u["container"], code)
    if drive:
        time.sleep(20); rc, out, _ = dexec(u["container"], "cat /tmp/2fa.log 2>/dev/null | tail -3")
        if "EXIT=0" in out and not re.search(r"failed|invalid|error", out, re.I): sh(f"docker restart {u['container']}", 120); flash(_("{a0}: sessione Apple rinnovata, container riavviato", a0=name))
        else: flash(_("{a0}: esito non ancora confermato ({a1}) — ricontrolla tra poco o riprova «Riautentica»", a0=name, a1=out[-120:]))
        return redirect(url_for("index"))
    flash(_("Codice consegnato a {a0}: verifica tra ~30 s nei giorni rimanenti", a0=u['container'])); return redirect(url_for("index"))
@app.post("/u/<name>/<action>")
def u_power(name, action):
    """Azioni rapide sul container: avvia, ferma, riavvia (il riavvio fa partire subito un giro di sincronizzazione)."""
    if action not in ("start", "stop", "restart"): abort(404)
    u = cfg()["users"].get(name) or abort(404); rc, out, err = sh(f"docker {action} {u['container']}", 120); _log_cache.pop(u["container"], None)
    done = {"start": _("{a0} avviato"), "stop": _("{a0} fermato"), "restart": _("{a0} riavviato: il giro di sincronizzazione riparte subito")}[action]
    flash(done.format(a0=u['container']) if rc == 0 else _("{a0}: docker {a1} non riuscito ({a2})", a0=u['container'], a1=action, a2=err[-200:]))
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
# ---- account collegati (3.5): posta, calendario e contatti per persona, sincronizzati dal modulo PIM di sidimam/iclouddr.
# Il file /config/pim-accounts.json del container iclouddr (600, utente abc 99:100) contiene anche le password per app:
# la GUI non le mostra mai, le sostituisce solo se ne viene scritta una nuova.
ACC_TYPES = {"icloud": "iCloud", "gmail": "Gmail", "outlook": "Outlook", "other": "Altro (IMAP)"}
def _acc_path(u):
    info = inspect(u["container"]) or {}
    return os.path.join(host_to_local(mounts(info).get("/config", "")), "pim-accounts.json")
def acc_load(u):
    try: return json.load(open(_acc_path(u))).get("accounts", [])
    except (FileNotFoundError, ValueError): return []
def acc_save(u, accs):
    p = _acc_path(u); tmp = p + ".tmp"
    with open(tmp, "w") as f: json.dump({"accounts": accs}, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    try: os.chown(tmp, 99, 100)
    except OSError: pass
    os.replace(tmp, p)
def _acc_from_form(f, old=None):
    t = f.get("type", (old or {}).get("type", "icloud")); t = t if t in ACC_TYPES else "other"
    a = dict(old or {}); a.update(type=t, label=(f.get("label") or "").strip() or ACC_TYPES[t].split(" ")[0], email=f.get("email", "").strip(),
        mail=f.get("mail") == "on", calendar=f.get("calendar") == "on", contacts=f.get("contacts") == "on",
        ics=[x.strip() for x in f.get("ics", "").splitlines() if x.strip().startswith("http")],
        exclude=[x.strip() for x in f.get("exclude", "").split(",") if x.strip()],
        imap_host=f.get("imap_host", "").strip(), imap_port=f.get("imap_port", "").strip())
    if f.get("password"): a["password"] = f["password"]
    a["label"] = re.sub(r'[\\/:*?"<>|]', "_", a["label"])[:40]
    return a
@app.post("/u/<name>/accounts/add")
def u_acc_add(name):
    u = cfg()["users"].get(name) or abort(404); accs = acc_load(u); a = _acc_from_form(request.form)
    base = re.sub(r"[^a-z0-9]+", "-", a["label"].lower()).strip("-") or a["type"]; aid = base; i = 2
    while any(x["id"] == aid for x in accs): aid = f"{base}-{i}"; i += 1
    a["id"] = aid
    if any(x["label"].lower() == a["label"].lower() for x in accs): flash(_("Esiste già un account con l'etichetta {a0}: la cartella sarebbe la stessa", a0=a["label"])); return redirect(url_for("index"))
    accs.append(a); acc_save(u, accs); _pim_kick(u)
    flash(_("Account {a0} aggiunto: la prima sincronizzazione parte entro un minuto", a0=a["label"])); return redirect(url_for("index"))
@app.post("/u/<name>/accounts/<aid>/save")
def u_acc_save(name, aid):
    u = cfg()["users"].get(name) or abort(404); accs = acc_load(u)
    for i, x in enumerate(accs):
        if x["id"] == aid: accs[i] = _acc_from_form(request.form, x); break
    else: abort(404)
    acc_save(u, accs); _pim_kick(u); flash(_("Account {a0} salvato", a0=accs[i]["label"])); return redirect(url_for("index"))
@app.post("/u/<name>/accounts/<aid>/delete")
def u_acc_delete(name, aid):
    u = cfg()["users"].get(name) or abort(404); accs = acc_load(u)
    acc_save(u, [x for x in accs if x["id"] != aid]); flash(_("Account tolto: i file già scaricati restano nella cartella")); return redirect(url_for("index"))
def _pim_kick(u):
    """Chiede al modulo PIM di ripartire subito (file /config/pim-run-now, lo controlla ogni 30 s)."""
    p = os.path.join(os.path.dirname(_acc_path(u)), "pim-run-now")
    try:
        open(p, "w").close(); os.chown(p, 99, 100)
    except OSError: pass
@app.post("/u/<name>/sync")
def u_sync(name):
    u = cfg()["users"].get(name) or abort(404); _pim_kick(u); flash(_("Sincronizzazione chiesta: parte entro un minuto")); return redirect(url_for("index"))
@app.post("/u/<name>/settings")
def u_settings(name):
    c = cfg(); u = c["users"].get(name) or abort(404); f = request.form; info = inspect(u["container"]) or abort(404)
    if utype(u) == "iclouddrive":
        y = os.path.join(host_to_local(mounts(info).get("/config", "")), "config.yaml")
        if f.get("sync_interval", "").strip().isdigit(): yaml_set(y, "sync_interval", f["sync_interval"].strip())
        yaml_set(y, "remove_obsolete", "true" if f.get("remove_obsolete") == "on" else "false")
        u["label"] = f.get("label", u.get("label", name)).strip() or name; u["chat_id"] = f.get("telegram_chat_id", "").strip(); u["apple_id"] = f.get("apple_id", u.get("apple_id", "")).strip() or u.get("apple_id", ""); save(c)
        sh(f"docker restart {u['container']}", 120); flash(_("{a0}: config.yaml aggiornato, container riavviato", a0=name)); return redirect(url_for("index"))
    upd = {k: f.get(k, "").strip() for k in ("photo_library", "photo_album", "folder_structure", "telegram_chat_id", "notification_days")}
    upd = {k: v for k, v in upd.items() if v != "" or k in ("photo_album",)}
    write_conf(conf_path(info), upd)
    for k, v in upd.items(): tpl_set(u["container"], k, v)
    u["label"] = f.get("label", u.get("label", name)).strip() or name; u["chat_id"] = f.get("telegram_chat_id", "").strip(); save(c)
    sh(f"docker restart {u['container']}", 120); flash(_("{a0}: impostazioni salvate (conf + template Unraid), container riavviato", a0=name)); return redirect(url_for("index"))
@app.post("/u/<name>/delete")
def u_delete(name):
    c = cfg(); u = c["users"].pop(name, None) or abort(404); c["aliases"] = {a: n for a, n in c["aliases"].items() if n != name}; save(c)
    flash(_("{a0}: tolto dalla GUI e dal bot (il container {a1} e le sue cartelle restano: gestiscili da Unraid)", a0=name, a1=u['container'])); return redirect(url_for("index"))
@app.route("/new", methods=["GET", "POST"])
def new():
    """Nuovo utente = sempre un container iclouddr (mandarons/icloud-drive: iCloud Drive + foto). I container icloudpd esistenti restano gestibili, ma non se ne creano più."""
    c = cfg()
    if request.method == "GET": return render_template("new.html", cfg=c)
    f = request.form; name = re.sub(r"[^a-z0-9-]", "", f["name"].lower()); short = re.sub(r"[^A-Za-z0-9]", "", f["short"]) or name
    return new_drive(c, f, name, short)
def new_drive(c, f, name, short):
    """Nuovo container iCloud Drive (mandarons/icloud-drive): cartelle, config.yaml, template Unraid, container. Primo login manuale."""
    apple = f["apple_id"].strip(); chat = f.get("chat_id", "").strip(); label = f.get("label", name).strip() or name
    dest_host = (f.get("drive_dest", "") or f"/mnt/user/documents/{label}/iCloud Drive").strip().rstrip("/")
    photos = f.get("photos") == "on"; photos_host = (f.get("photos_dest", "") or f"/mnt/user/media/photos/{label}/Personale").strip().rstrip("/")
    ignore = [x.strip() for x in f.get("ignore", "").split(",") if x.strip()]
    photos_note = f" e foto → {photos_host}" if photos else ""; photos_mount = f" -v {json.dumps(photos_host)}:/icloud/photos/all" if photos else ""
    photos_overview = f" e le foto personali in {html.escape(photos_host)}" if photos else ""
    if not name or not apple or name in c["users"]: flash(_("Dati mancanti o nome già usato")); return redirect(url_for("new"))
    p = c["paths"]; d = c["defaults"]; cont = f"iclouddr-{short.lower()}"; host_appdata = f"/mnt/user/appdata/{cont}"
    # Dalla 3.8 i container nuovi usano l'immagine con il modulo PIM, come quelli di famiglia: note e promemoria in
    # <persona>/iCloud, account collegati nella cartella della persona (la cartella che contiene «iCloud Drive»).
    image = os.environ.get("ICLOUDDR_IMAGE", "ghcr.io/sidimam/iclouddr:0.2.2")
    person_host = os.path.dirname(dest_host); pim_host = person_host + "/iCloud"
    appdata = host_to_local(host_appdata); dest = host_to_local(dest_host)
    if dest == dest_host: flash(_("La destinazione deve stare sotto /mnt/user/documents (montato in iCloud Dashboard come /documents)")); return redirect(url_for("new"))
    photos_local = host_to_local(photos_host) if photos else None
    if photos and photos_local == photos_host: flash(_("La cartella foto deve stare sotto /mnt/user/media/photos (montato in iCloud Dashboard come /photos)")); return redirect(url_for("new"))
    for path in [appdata, dest, host_to_local(pim_host)] + ([photos_local] if photos else []):
        os.makedirs(path, exist_ok=True); shutil.chown(path, int(d["user_id"]), int(d["group_id"]))
    open(os.path.join(appdata, "config.yaml"), "w").write(f"""# {cont} — iCloud Drive di {label} → {dest_host}{photos_note} (solo download, {image})
app:
  logger:
    level: "info"
    filename: "/config/icloud.log"
  credentials:
    username: "{apple}"
    retry_login_interval: 600
  root: "/icloud"
  region: global
  max_threads: 4
drive:
  destination: "drive"
  remove_obsolete: false
  sync_interval: {d.get('drive_sync_interval', 1800)}
""" + ("  ignore:\n" + "".join(f'    - "{x}"\n' for x in ignore) if ignore else "") + ("""photos:
  destination: "photos"
  sync_interval: 3600
  remove_obsolete: false
  all_albums: false
  folder_format: "%Y/%m"
  filename_format: simple
  filters:
    libraries:
      - PrimarySync
    file_sizes:
      - "original"
      - "live_video_original"
      - "original_alt"
""" if photos else "")); shutil.chown(os.path.join(appdata, "config.yaml"), int(d["user_id"]), int(d["group_id"]))
    icon = "https://raw.githubusercontent.com/sidimam/icloud-dashboard/main/assets/icloud-drive.png"   # quadrata 256x256 (l'icona dashboard-icons è 797x512 e Unraid la deforma)
    open(tpl_path(cont), "w").write(f"""<?xml version="1.0"?>
<Container version="2">
  <Name>{cont}</Name>
  <Repository>{image}</Repository>
  <Registry>https://github.com/sidimam/iclouddr/pkgs/container/iclouddr</Registry>
  <Network>bridge</Network>
  <Shell>sh</Shell>
  <Privileged>false</Privileged>
  <Support>https://github.com/mandarons/icloud-docker</Support>
  <Project>https://github.com/mandarons/icloud-docker</Project>
  <Overview>Scarica iCloud Drive di {html.escape(label)} in {html.escape(dest_host)}{photos_overview} (solo download). Primo login: docker exec -it {cont} /bin/sh -c "su-exec abc icloud --username={apple} --session-directory=/config/session_data"</Overview>
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
  <Config Name="Note, promemoria (PIM)" Target="/icloud/pim" Default="{html.escape(pim_host)}" Mode="rw" Description="Modulo PIM: Note/ (HTML) e Promemoria/ (JSON e testo), sola lettura da iCloud" Type="Path" Display="always" Required="false" Mask="false">{html.escape(pim_host)}</Config>
  <Config Name="Cartella della persona (account collegati)" Target="/icloud/persona" Default="{html.escape(person_host)}" Mode="rw" Description="Posta, calendari e contatti degli account collegati: una sottocartella per account" Type="Path" Display="always" Required="false" Mask="false">{html.escape(person_host)}</Config>
  <Config Name="PIM attivo" Target="PIM_ENABLED" Default="1" Mode="" Description="0 = solo iclouddr originale" Type="Variable" Display="always" Required="false" Mask="false">1</Config>
  <Config Name="PIM ogni (secondi)" Target="PIM_INTERVAL" Default="21600" Mode="" Description="intervallo fra un giro e l altro di note e promemoria" Type="Variable" Display="advanced" Required="false" Mask="false">21600</Config>
""" + (f'''  <Config Name="Foto personali" Target="/icloud/photos/all" Default="{html.escape(photos_host)}" Mode="rw" Description="Libreria PrimarySync, cartelle %Y/%m" Type="Path" Display="always" Required="false" Mask="false">{html.escape(photos_host)}</Config>
''' if photos else "") + """</Container>
""")
    rc, out, err = sh(f"docker run -d --name {cont} --restart unless-stopped --label net.unraid.docker.managed=dockerman -e PUID={d['user_id']} -e PGID={d['group_id']} -e TZ={d['tz']} -e ENV_CONFIG_FILE_PATH=/config/config.yaml -v {host_appdata}:/config -v {json.dumps(dest_host)}:/icloud/drive{photos_mount} -v {json.dumps(pim_host)}:/icloud/pim -v {json.dumps(person_host)}:/icloud/persona -e PIM_ENABLED=1 --label net.unraid.docker.icon={icon} {image}", 180)
    if rc != 0: flash(_("docker run fallito: {a0}", a0=err[-300:])); return redirect(url_for("new"))
    c["users"][name] = {"type": "iclouddrive", "container": cont, "label": label, "chat_id": chat, "apple_id": apple}; c["aliases"][short.lower() + "-drive"] = name; save(c)
    flash(_("{a0} creato. Primo login Apple dal terminale di Unraid: docker exec -it {a1} /bin/sh -c \"su-exec abc icloud --username={a2} --session-directory=/config/session_data\" — password, codice, y al keyring. Poi #{a3} auth / #{a4} 123456 funzionano dal bot.", a0=cont, a1=cont, a2=apple, a3=name, a4=name))
    return redirect(url_for("index"))
# ---- riavvio automatico dei giri bloccati (2.5). mandarons/icloud-drive a volte resta appeso su una connessione morta (CLOSE_WAIT)
# a metà giro: nessuna riga di log, rete e CPU ferme, e non riparte più da solo (Sofia, 3/11/2026: 13:26→17:19 e 17:19→21:38).
# Ogni AUTOHEAL_CHECK_S secondi, per ogni container iclouddr acceso con un giro in corso: se negli ultimi AUTOHEAL_IDLE_MIN minuti
# ha ricevuto meno di 100 KB e usato meno di 2 s di CPU, oppure il giro dura da più di max(AUTOHEAL_MAX_CYCLE_H, 3 × l'ultimo),
# il container viene riavviato. Al massimo AUTOHEAL_MAX_RESTARTS riavvii in 6 ore per container, poi un avviso Telegram e basta.
AH_ON = os.environ.get("AUTOHEAL", "1") != "0"; AH_CHECK = int(os.environ.get("AUTOHEAL_CHECK_S", "120"))
AH_IDLE = int(os.environ.get("AUTOHEAL_IDLE_MIN", "10")); AH_MAXH = float(os.environ.get("AUTOHEAL_MAX_CYCLE_H", "4"))
AH_MAXR = int(os.environ.get("AUTOHEAL_MAX_RESTARTS", "3")); AH_FILE = os.path.join(APP_DIR, "state", "autoheal.json")
_ah_samples = {}   # container -> [(ts, byte ricevuti, µs di CPU)]
def ah_events():
    try: return json.load(open(AH_FILE))
    except (FileNotFoundError, ValueError): return []
def ah_log(ev):
    evs = (ah_events() + [ev])[-200:]; os.makedirs(os.path.dirname(AH_FILE), exist_ok=True); json.dump(evs, open(AH_FILE, "w"), ensure_ascii=False, indent=1)
def ah_counters(c):
    rc, out, _ = dexec(c, "cat /proc/net/dev /sys/fs/cgroup/cpu.stat")   # niente awk: il $ verrebbe espanso dalla shell esterna
    rx = next((int(l.split(":", 1)[1].split()[0]) for l in out.splitlines() if l.strip().startswith("eth0:")), None)
    cpu = next((int(l.split()[1]) for l in out.splitlines() if l.startswith("usage_usec")), None)
    return (rx, cpu) if rx is not None and cpu is not None else None
def ah_check(name, u, c):
    ct = u["container"]; info = inspect(ct)
    if not info or not info["State"]["Running"]: _ah_samples.pop(ct, None); return
    if d_auth_running(ct): return   # riautenticazione in corso: non toccare
    _log_cache.pop(ct, None); cyc = [x for x in sync_cycles(ct, 0) if x["running_min"] is not None]
    now = time.time(); k = ah_counters(ct)
    smp = _ah_samples.setdefault(ct, [])
    if k: smp.append((now, *k))
    smp[:] = [x for x in smp if now - x[0] <= AH_IDLE * 60 + AH_CHECK]
    if not cyc: return
    reason = None; run = max(x["running_min"] for x in cyc)
    long_ = max(AH_MAXH * 60, 3 * max((x["last_min"] or 0) for x in cyc))
    if run > long_: reason = f"giro {cyc[0]['kind']} in corso da {run} min (limite {int(long_)})"
    elif len(smp) >= 2 and smp[-1][0] - smp[0][0] >= AH_IDLE * 60 and run >= AH_IDLE:
        drx, dcpu = smp[-1][1] - smp[0][1], (smp[-1][2] - smp[0][2]) / 1e6
        if drx < 100_000 and dcpu < 2: reason = f"giro {cyc[0]['kind']} fermo: {drx // 1000} KB ricevuti e {dcpu:.1f} s di CPU in {AH_IDLE} min"
    if not reason: return
    recent = [e for e in ah_events() if e.get("container") == ct and e.get("action") == "restart" and now - e.get("ts", 0) < 6 * 3600]
    if len(recent) >= AH_MAXR:
        if not any(e.get("container") == ct and e.get("action") == "gave_up" and now - e.get("ts", 0) < 6 * 3600 for e in ah_events()):
            ah_log({"ts": now, "container": ct, "action": "gave_up", "reason": reason})
            if nset()["gave_up"]: notify(c, f"⚠️ {name}: {ct} si blocca di continuo ({len(recent)} riavvii automatici in 6 ore, ultimo motivo: {reason}). Per 6 ore non lo riavvio più in automatico: guarda iCloud Dashboard (📜 Log).")
        return
    rc, _, err = sh(f"docker restart {ct}", 120); _ah_samples.pop(ct, None); _log_cache.pop(ct, None)
    ah_log({"ts": now, "container": ct, "action": "restart", "reason": reason, "ok": rc == 0})
    print(f"autoheal: {ct} riavviato ({reason})" if rc == 0 else f"autoheal: {ct} riavvio fallito: {err[-200:]}", flush=True)
    if nset()["restart"]: notify(c, f"🔁 {name}: {ct} riavviato in automatico ({reason})." if rc == 0 else f"❌ {name}: riavvio automatico di {ct} non riuscito.")
def ah_loop():
    while True:
        try:
            c = cfg(); notify_flush(c)
            for name, u in c["users"].items():
                if AH_ON and utype(u) == "iclouddrive":
                    try: ah_check(name, u, c)
                    except Exception as e: print(f"autoheal {name}: {e}", flush=True)
        except Exception as e: print(f"autoheal: {e}", flush=True)
        time.sleep(AH_CHECK)
def ah_view(ct):
    evs = [e for e in ah_events() if e.get("container") == ct]
    for e in evs: e["when"] = datetime.datetime.fromtimestamp(e["ts"]).strftime("%d/%m %H:%M")
    return {"last": evs[-1] if evs else None, "n24": sum(1 for e in evs if e["action"] == "restart" and time.time() - e["ts"] < 86400)}

# ---- scheda Amministrazione (3.0): profili, password, 2FA, notifiche. Riservata ai profili con ruolo admin.
def _admin_only():
    if not (me() and accounts.is_admin(me())) and accounts.any_password(): abort(403)
@app.route("/admin")
def admin():
    _admin_only()
    ps = accounts.profiles()
    rows = [{"name": n, "p": p, "icloud": _icloud_of(n), "role": accounts.ROLES.get(p.get("role"), p.get("role"))} for n, p in sorted(ps.items(), key=lambda x: (x[0] != accounts.ADMIN, x[0]))]
    try: idx_by, idx_runs = SI.stats()
    except Exception: idx_by, idx_runs = {}, {}
    idx = sorted(((k[1], KIND_NAMES.get(k[0], k[0]), v) for k, v in idx_by.items()))
    last = max((r["finished"] for r in idx_runs.values()), default=0); errs = [f"{k}: {r['error']}" for k, r in idx_runs.items() if r.get("error")]
    return render_template("admin.html", rows=rows, n=nset(), default_chat=cfg().get("default_chat_id", ""), roles=accounts.ROLES, geocode=_settings().get("geocode", False),
                           idx=idx, idx_last=ago(datetime.datetime.fromtimestamp(last)) if last else "", idx_errs=errs, idx_db=os.path.getsize(SI.DB) if os.path.exists(SI.DB) else 0, fmt_size=_fmt_size)
@app.post("/admin/profile/<name>/<action>")
def admin_profile(name, action):
    _admin_only(); f = request.form
    if not accounts.get(name): abort(404)
    if action == "password":
        if len(f.get("new", "")) < 8: flash(_("La password deve avere almeno 8 caratteri"))
        else: accounts.set_password(name, f["new"]); flash(_("Password di {a0} impostata", a0=name))
    elif action == "totp_off": accounts.update(name, totp="", totp_on=False, recovery=[]); flash(_("Verifica in due passaggi di {a0} disattivata", a0=name))
    elif action == "role":
        role = f.get("role")
        if role not in accounts.ROLES: abort(400)
        if name == accounts.ADMIN and role != "admin": flash(_("Il profilo admin resta amministratore"))
        elif role != "admin" and accounts.admins() == [name]: flash(_("Serve almeno un amministratore"))
        else: accounts.update(name, role=role); flash(_("Ruolo di {a0}: {a1}", a0=name, a1=_(accounts.ROLES[role])))
    elif action == "telegram":
        chat = f.get("chat", "").strip()
        if chat and not re.fullmatch(r"\d{4,15}", chat): flash(_("La chat privata è un numero (es. 141880239)"))
        else: accounts.update(name, telegram=chat); flash(_("Chat Telegram di {a0} salvata", a0=name) if chat else _("Chat Telegram di {a0} rimossa", a0=name))
    elif action == "delete":
        if name == me() or name == accounts.ADMIN: flash(_("Non puoi eliminare questo profilo"))
        else: accounts.remove(name); flash(_("Profilo {a0} eliminato", a0=name))
    else: abort(404)
    return redirect(url_for("admin"))
@app.post("/admin/profile/new")
def admin_profile_new():
    _admin_only(); f = request.form; name = f.get("name", "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,31}", name): flash(_("Nome profilo non valido (lettere minuscole, numeri, . _ -)"))
    elif f.get("new") and len(f["new"]) < 8: flash(_("La password deve avere almeno 8 caratteri"))
    elif not accounts.add(name, "admin", f.get("new", "")): flash(_("Il profilo {a0} esiste già", a0=name))
    else: flash(_("Profilo {a0} creato", a0=name) if f.get("new") else _("Profilo {a0} creato: impostagli una password per farlo entrare", a0=name))
    return redirect(url_for("admin"))
@app.post("/admin/notify")
def admin_notify():
    _admin_only(); f = request.form; q = f.get("quiet", "").strip()
    if q and not re.fullmatch(r"\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}", q): flash(_("Ore silenziose: usa il formato 23:00-07:30, oppure lascia vuoto")); return redirect(url_for("admin"))
    save_nset({"chat": f.get("chat", "").strip(), "restart": f.get("restart") == "on", "gave_up": f.get("gave_up") == "on", "quiet": q}); flash(_("Notifiche salvate"))
    return redirect(url_for("admin"))
@app.post("/admin/notify/test")
def admin_notify_test():
    _admin_only(); chat = notify_chat(cfg())
    if not TOKEN or not chat: flash(_("Manca il token del bot o la chat")); return redirect(url_for("admin"))
    telegram(chat, f"🧪 Prova di iCloud Dashboard da {me() or 'admin'}: le notifiche arrivano qui."); flash(_("Messaggio di prova inviato"))
    return redirect(url_for("admin"))

# ---- ricerca e schede dei dati (3.6): database «iclouddb» (SQLite FTS5) in /app/state/iclouddb.db, vedi search_index.py
import search_index as SI
from flask import send_file, Response
IDX_INTERVAL = int(os.environ.get("INDEX_INTERVAL", "900"))
KIND_NAMES = {"file": "File", "note": "Note", "reminder": "Promemoria", "mail": "Posta", "event": "Calendario", "contact": "Contatti"}
KIND_ICONS = {"file": ("folder", "b-blue"), "note": ("doc", "b-orange"), "reminder": ("check", "b-red"), "mail": ("send", "b-blue"), "event": ("doc", "b-red"), "contact": ("users", "b-gray")}
def idx_sources():
    out = []
    for n, u in cfg()["users"].items():
        if utype(u) != "iclouddrive": continue
        info = inspect(u["container"])
        if not info: continue
        m = mounts(info); dh = m.get("/icloud/drive", "")
        ph = m.get("/icloud/persona") or (os.path.dirname(dh) if dh else "")
        out.append({"person": u.get("label", n), "user": n, "drive": (host_to_local(dh), dh) if dh else None,
                    "person_dir": (host_to_local(ph), ph) if ph else None, "accounts": [a.get("label") for a in acc_load(u) if a.get("label")]})
    return out
def idx_loop():
    time.sleep(60)
    while True:
        t = time.time()
        try: SI.run(idx_sources(), log=lambda x: print(x, flush=True)); print(f"indice: giro completato in {time.time() - t:.0f} s", flush=True)
        except Exception as e: print(f"indice: {e}", flush=True)
        time.sleep(IDX_INTERVAL)
def allowed_persons():
    """Persone visibili: tutte per l'amministratore (o senza password), altrimenti solo quella del proprio container."""
    c = cfg()["users"]
    if not accounts.any_password() or accounts.is_admin(me()): return [u.get("label", n) for n, u in c.items()]
    own = _own_icloud(); return [c[own].get("label", own)] if own in c else []
def _item(rid):
    con = SI.connect()
    try: r = con.execute("select * from items where id=?", (rid,)).fetchone()
    finally: con.close()
    if not r or r["person"] not in allowed_persons(): abort(404)
    d = dict(r); d["x"] = json.loads(d.get("extra") or "{}"); return d
def _local_ok(p):
    p = os.path.realpath(p)
    return any(p.startswith(os.path.realpath(r) + "/") for r in ("/documents",))
def _mark(snip):
    return Markup(html.escape(snip or "").replace("\x02", "<mark>").replace("\x03", "</mark>"))
@app.route("/search")
def search():
    a = request.args; persons = allowed_persons()
    kinds = [k for k in a.getlist("kind") if k in SI.KINDS] or list(SI.KINDS)
    person = a.get("person", ""); q = a.get("q", "").strip()
    where, args = [f"i.kind in ({','.join('?' * len(kinds))})"], list(kinds)
    sel_persons = [person] if person in persons else persons
    where.append(f"i.person in ({','.join('?' * len(sel_persons))})"); args += sel_persons
    if a.get("account"): where.append("i.account=?"); args.append(a["account"])
    if a.get("ext"): where.append("i.ext=?"); args.append(a["ext"].lower().lstrip("."))
    for fld, op in (("d1", ">="), ("d2", "<=")):
        if a.get(fld):
            try: where.append(f"i.date {op} ?"); args.append(datetime.datetime.strptime(a[fld], "%Y-%m-%d").timestamp() + (86399 if fld == "d2" else 0))
            except ValueError: pass
    for fld, op in (("min_kb", ">="), ("max_kb", "<=")):
        if a.get(fld, "").isdigit(): where.append(f"i.size {op} ?"); args.append(int(a[fld]) * 1024)
    if a.get("frm"): where.append("json_extract(i.extra,'$.from') like ?"); args.append(f"%{a['frm']}%")
    if a.get("to"): where.append("(json_extract(i.extra,'$.to') like ? or json_extract(i.extra,'$.cc') like ?)"); args += [f"%{a['to']}%"] * 2
    if a.get("att") == "1": where.append("json_array_length(json_extract(i.extra,'$.atts')) > 0")
    if a.get("att_name"): where.append("json_extract(i.extra,'$.atts') like ?"); args.append(f"%{a['att_name']}%")
    order = {"date": "i.date desc", "old": "i.date asc", "size": "i.size desc", "name": "i.title collate nocase"}.get(a.get("sort", ""), "")
    con = SI.connect(); rows = []; total = 0
    try:
        if q:
            fq = SI.fts_query(q)
            sql = f"select i.*, snippet(fts, -1, char(2), char(3), '…', 14) snip, bm25(fts) rank from fts join items i on i.id = fts.rowid where fts match ? and {' and '.join(where)} order by {order or 'rank'} limit 300"
            rows = con.execute(sql, [fq] + args).fetchall()
        else:
            rows = con.execute(f"select i.*, '' snip from items i where {' and '.join(where)} order by {order or 'i.date desc'} limit 300", args).fetchall()
        counts = {r["kind"]: r["n"] for r in con.execute(f"select kind, count(*) n from items where person in ({','.join('?' * len(sel_persons))}) group by kind", sel_persons)}
        exts = [r["ext"] for r in con.execute("select ext, count(*) n from items where kind='file' and ext!='' group by ext order by n desc limit 25")]
        accts = [r["account"] for r in con.execute("select distinct account from items where account is not null order by account")]
    except sqlite3.OperationalError as e:
        flash(_("Ricerca non valida: {a0}", a0=str(e))); counts, exts, accts = {}, [], []
    finally: con.close()
    res = [{**dict(r), "x": json.loads(r["extra"] or "{}"), "snip": _mark(r["snip"])} for r in rows]
    return render_template("search.html", res=res, q=q, kinds=kinds, all_kinds=SI.KINDS, kind_names=KIND_NAMES, kind_icons=KIND_ICONS, persons=persons, person=person,
                           counts=counts, exts=exts, accts=accts, a=a, fmt_date=_fmt_date, fmt_size=_fmt_size)
def _fmt_date(ts, with_time=True):
    if not ts: return ""
    d = datetime.datetime.fromtimestamp(ts); return d.strftime("%d/%m/%Y %H:%M" if with_time and (d.hour, d.minute) != (0, 0) else "%d/%m/%Y")
def _fmt_size(n):
    if n is None: return ""
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024: return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"
@app.route("/item/<int:rid>")
def item(rid):
    d = _item(rid); body = ""
    if d["kind"] == "mail" and _local_ok(d["x"].get("local", "")):
        try:
            msg = email.message_from_binary_file(open(d["x"]["local"], "rb"), policy=email.policy.default)
            part = msg.get_body(preferencelist=("plain", "html")); body = part.get_content() if part else ""
            if part and part.get_content_type() == "text/html": d["html_mail"] = True
        except Exception as e: body = f"({e})"
    if d["kind"] == "file":   # anteprima nella pagina
        ext = (d.get("ext") or "").lower()
        d["preview"] = ("image" if ext in ("jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp", "svg", "tif", "tiff") else
                        "pdf" if ext == "pdf" else
                        "video" if ext in ("mp4", "mov", "m4v", "webm") else
                        "audio" if ext in ("mp3", "m4a", "aac", "wav", "ogg", "flac") else
                        "text" if ext in ("txt", "md", "csv", "json", "log", "xml", "yaml", "yml", "ini", "conf", "py", "sh", "js", "css", "html", "swift", "go", "ics", "vcf") else "")
        if d["preview"] == "text" and _local_ok(d["x"].get("local", "")):
            try: body = open(d["x"]["local"], "rb").read(200 * 1024).decode("utf-8", "replace")
            except OSError: d["preview"] = ""
    return render_template("item.html", d=d, body=body, kind_names=KIND_NAMES, kind_icons=KIND_ICONS, fmt_date=_fmt_date, fmt_size=_fmt_size)
@app.route("/item/<int:rid>/raw")
def item_raw(rid):
    d = _item(rid); p = d["x"].get("local", "")
    if not _local_ok(p) or not os.path.isfile(p): abort(404)
    if d["kind"] == "note" or (d["kind"] == "mail" and request.args.get("html")):
        if d["kind"] == "mail":
            msg = email.message_from_binary_file(open(p, "rb"), policy=email.policy.default); part = msg.get_body(preferencelist=("html",)); content = part.get_content() if part else ""
        else:
            content = open(p, encoding="utf-8", errors="replace").read()
            # le immagini e gli allegati della nota stanno in _allegati/ accanto all'HTML: li serve /item/<id>/asset/
            content = re.sub(r'(src|href)="_allegati/', lambda m: f'{m.group(1)}="/item/{rid}/asset/_allegati/', content)
        r = Response(content, mimetype="text/html")
        # pagina isolata: niente script, niente immagini da Internet (solo quelle allegate alla nota)
        r.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"
        return r
    r = send_file(p, as_attachment=request.args.get("dl") == "1", download_name=os.path.basename(p) if d["kind"] != "mail" else (d["title"] or "messaggio")[:60] + ".eml", conditional=True)
    r.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'; object-src 'self'"
    r.headers["X-Content-Type-Options"] = "nosniff"
    return r
@app.route("/item/<int:rid>/asset/<path:sub>")
def item_asset(rid, sub):
    """Immagini e allegati delle note (cartella _allegati accanto all'HTML)."""
    d = _item(rid); base = os.path.dirname(d["x"].get("local", "")); p = os.path.realpath(os.path.join(base, sub))
    if d["kind"] != "note" or not p.startswith(os.path.realpath(base) + "/") or not os.path.isfile(p): abort(404)
    return send_file(p)
@app.route("/item/<int:rid>/att/<int:n>")
def item_att(rid, n):
    d = _item(rid); p = d["x"].get("local", "")
    if d["kind"] != "mail" or not _local_ok(p): abort(404)
    msg = email.message_from_binary_file(open(p, "rb"), policy=email.policy.default)
    parts = [x for x in msg.walk() if x.get_filename()]
    if n >= len(parts): abort(404)
    part = parts[n]; return Response(part.get_payload(decode=True) or b"", mimetype=part.get_content_type(), headers={"Content-Disposition": f'attachment; filename="{part.get_filename()}"'})
@app.route("/notes")
def notes():
    persons = allowed_persons(); person = request.args.get("person", ""); sel = [person] if person in persons else persons
    folder = request.args.get("folder", "")
    con = SI.connect()
    try:
        rows = [{**dict(r), "x": json.loads(r["extra"] or "{}")} for r in con.execute(f"select * from items where kind='note' and person in ({','.join('?' * len(sel))}) order by date desc", sel)]
    finally: con.close()
    folders = {}
    for r in rows: folders.setdefault((r["person"], r["x"].get("folder", "")), 0); folders[(r["person"], r["x"].get("folder", ""))] += 1
    if folder: rows = [r for r in rows if r["x"].get("folder") == folder]
    return render_template("notes.html", rows=rows, folders=sorted(folders.items()), persons=persons, person=person, folder=folder, fmt_date=_fmt_date)
@app.route("/reminders")
def reminders():
    persons = allowed_persons(); person = request.args.get("person", ""); sel = [person] if person in persons else persons
    lst = request.args.get("list", ""); show = request.args.get("show", "open")
    con = SI.connect()
    try:
        rows = [{**dict(r), "x": json.loads(r["extra"] or "{}")} for r in con.execute(f"select * from items where kind='reminder' and person in ({','.join('?' * len(sel))})", sel)]
    finally: con.close()
    lists = {}
    for r in rows:
        k = (r["person"], r["x"].get("list", "")); lists.setdefault(k, [0, 0]); lists[k][0 if not r["x"].get("completed") else 1] += 1
    if lst: rows = [r for r in rows if r["x"].get("list") == lst]
    if show == "open": rows = [r for r in rows if not r["x"].get("completed")]
    elif show == "done": rows = [r for r in rows if r["x"].get("completed")]
    now = time.time()
    rows.sort(key=lambda r: (r["x"].get("completed", False), r["date"] is None, r["date"] or 0, (r["title"] or "").lower()))
    return render_template("reminders.html", rows=rows, lists=sorted(lists.items()), persons=persons, person=person, lst=lst, show=show, now=now, fmt_date=_fmt_date)
# ---- Calendario, Posta, Contatti (3.7)
PALETTE = ["#007aff", "#34c759", "#ff9500", "#ff3b30", "#af52de", "#5ac8fa", "#ff2d55", "#a2845e", "#5856d6", "#30b0c7"]
def _settings():
    try: return json.load(open(SETTINGS_FILE))
    except (FileNotFoundError, ValueError): return {}
def _save_settings(d):
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True); json.dump(d, open(SETTINGS_FILE, "w"), indent=1, ensure_ascii=False)
def cal_list(persons):
    con = SI.connect()
    try:
        rows = con.execute(f"select json_extract(extra,'$.cal_id') cid, json_extract(extra,'$.calendar') name, person, account, count(*) n from items where kind='event' and person in ({','.join('?' * len(persons))}) group by cid order by person, name", persons).fetchall()
    finally: con.close()
    colors = _settings().get("calendar_colors", {})
    return [{"id": r["cid"], "name": r["name"], "person": r["person"], "account": r["account"], "n": r["n"], "color": colors.get(r["cid"]) or PALETTE[i % len(PALETTE)]} for i, r in enumerate(rows)]
@app.route("/calendar")
def calendar():
    persons = allowed_persons(); person = request.args.get("person", "")
    sel = [person] if person in persons else persons
    return render_template("calendar.html", cals=cal_list(sel), persons=persons, person=person, geocode=_settings().get("geocode", False))
@app.post("/calendar/color")
def calendar_color():
    cid = request.form.get("id", ""); col = request.form.get("color", "")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", col) or not any(c["id"] == cid for c in cal_list(allowed_persons())): abort(400)
    st = _settings(); st.setdefault("calendar_colors", {})[cid] = col; _save_settings(st); return ("", 204)
def _expand(r, start, end):
    """Occorrenze di un evento ricorrente fra start ed end (dal file .ics originale)."""
    import icalendar, recurring_ical_events
    x = json.loads(r["extra"] or "{}"); p = x.get("local", "")
    if not _local_ok(p): return []
    try:
        cal = icalendar.Calendar.from_ical(open(p, "rb").read())
        uid = r["path"].split("#")[1] if "#" in r["path"] else None
        out = []
        for ev in recurring_ical_events.of(cal).between(datetime.datetime.fromtimestamp(start), datetime.datetime.fromtimestamp(end)):
            if uid and str(ev.get("UID")) != uid: continue
            ds, de = ev.get("DTSTART").dt, (ev.get("DTEND").dt if ev.get("DTEND") else None)
            out.append((ds, de))
        return out
    except Exception: return []
def _iso(v):
    if isinstance(v, datetime.datetime): return v.isoformat()
    if isinstance(v, datetime.date): return v.isoformat()
    return None
@app.route("/api/events")
def api_events():
    persons = allowed_persons(); person = request.args.get("person", ""); sel = [person] if person in persons else persons
    def ts(v):
        try: return datetime.datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except Exception: return time.time()
    start, end = ts(request.args.get("start", "")), ts(request.args.get("end", ""))
    cals = {c["id"]: c for c in cal_list(sel)}
    con = SI.connect()
    try:
        rows = con.execute(f"""select * from items where kind='event' and person in ({','.join('?' * len(sel))}) and
            ((date < ? and coalesce(date_end, date) >= ?) or coalesce(json_extract(extra,'$.rrule'),'') != '')""", sel + [end, start]).fetchall()
    finally: con.close()
    out = []
    for r in rows:
        x = json.loads(r["extra"] or "{}"); c = cals.get(x.get("cal_id"), {}); color = c.get("color", PALETTE[0])
        base = {"id": r["id"], "title": r["title"] or "—", "color": color, "url": f"/item/{r['id']}",
                "extendedProps": {"person": r["person"], "calendar": x.get("calendar"), "location": x.get("location"), "geo": x.get("geo"), "cal_id": x.get("cal_id")}}
        if x.get("rrule"):
            for ds, de in _expand(r, start, end):
                out.append({**base, "start": _iso(ds), "end": _iso(de), "allDay": not isinstance(ds, datetime.datetime)})
        elif r["date"]:
            allday = bool(x.get("allday"))
            fmt = (lambda t: datetime.date.fromtimestamp(t).isoformat()) if allday else (lambda t: datetime.datetime.fromtimestamp(t).isoformat())
            out.append({**base, "start": fmt(r["date"]), "end": fmt(r["date_end"]) if r["date_end"] else None, "allDay": allday})
    return {"events": out[:5000]}
@app.route("/api/geocode")
def api_geocode():
    """Coordinate di un luogo via OpenStreetMap (Nominatim), solo se attivato in Amministrazione; risultati in cache nell'indice."""
    if not _settings().get("geocode"): return {"geo": None, "off": True}
    q = request.args.get("q", "").strip()[:200]
    if not q: return {"geo": None}
    con = SI.connect()
    try:
        con.execute("create table if not exists geocache(q text primary key, lat real, lon real, ts real)")
        hit = con.execute("select lat, lon from geocache where q=?", (q,)).fetchone()
        if hit: return {"geo": [hit["lat"], hit["lon"]] if hit["lat"] is not None else None}
        import urllib.request, urllib.parse
        time.sleep(1)   # regole d'uso di Nominatim: al massimo una richiesta al secondo
        req = urllib.request.Request("https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" + urllib.parse.quote(q), headers={"User-Agent": "iCloud-Dashboard/3.7 (uso domestico)"})
        try: res = json.load(urllib.request.urlopen(req, timeout=15))
        except Exception: res = []
        lat, lon = (float(res[0]["lat"]), float(res[0]["lon"])) if res else (None, None)
        con.execute("insert or replace into geocache values(?,?,?,?)", (q, lat, lon, time.time())); con.commit()
        return {"geo": [lat, lon] if lat is not None else None}
    finally: con.close()
@app.post("/admin/geocode")
def admin_geocode():
    _admin_only(); st = _settings(); st["geocode"] = request.form.get("geocode") == "on"; _save_settings(st)
    flash(_("Ricerca delle coordinate con OpenStreetMap: {a0}", a0=_("attiva") if st["geocode"] else _("spenta"))); return redirect(url_for("admin"))
@app.route("/mail")
def mail():
    persons = allowed_persons(); person = request.args.get("person", ""); sel = [person] if person in persons else persons
    acct = request.args.get("account", ""); folder = request.args.get("folder", "")
    con = SI.connect()
    try:
        tree = con.execute(f"select person, account, json_extract(extra,'$.folder') folder, count(*) n from items where kind='mail' and person in ({','.join('?' * len(sel))}) group by person, account, folder order by person, account, folder", sel).fetchall()
        where, args = [f"kind='mail' and person in ({','.join('?' * len(sel))})"], list(sel)
        if acct: where.append("account=?"); args.append(acct)
        if folder: where.append("json_extract(extra,'$.folder')=?"); args.append(folder)
        rows = [{**dict(r), "x": json.loads(r["extra"] or "{}")} for r in con.execute(f"select * from items where {' and '.join(where)} order by date desc limit 300", args)]
    finally: con.close()
    return render_template("mail.html", tree=tree, rows=rows, persons=persons, person=person, acct=acct, folder=folder, fmt_date=_fmt_date, fmt_size=_fmt_size)
@app.route("/contacts")
def contacts():
    persons = allowed_persons(); person = request.args.get("person", ""); sel = [person] if person in persons else persons; q = request.args.get("q", "").strip().lower()
    con = SI.connect()
    try: rows = [{**dict(r), "x": json.loads(r["extra"] or "{}")} for r in con.execute(f"select * from items where kind='contact' and person in ({','.join('?' * len(sel))})", sel)]
    finally: con.close()
    # stessa persona salvata da più di voi: unite per e-mail o telefono uguali (ultime 9 cifre)
    parent = list(range(len(rows))); key_of = {}
    def find(i):
        while parent[i] != i: parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i, r in enumerate(rows):
        for k in [("e", e) for e in r["x"].get("emails", [])] + [("t", t[-9:]) for t in r["x"].get("tels", []) if len(t) >= 6]:
            if k in key_of: parent[find(i)] = find(key_of[k])
            else: key_of[k] = i
    groups = {}
    for i, r in enumerate(rows): groups.setdefault(find(i), []).append(r)
    people = []
    for g in groups.values():
        names = sorted({r["title"] for r in g if r["title"]}, key=len, reverse=True)
        item = {"name": names[0] if names else "—", "ids": [r["id"] for r in g], "who": sorted({r["person"] for r in g}),
                "emails": sorted({e for r in g for e in r["x"].get("emails", [])}), "tels": sorted({t for r in g for t in r["x"].get("tels", [])}),
                "org": next((r["x"].get("org") for r in g if r["x"].get("org")), "")}
        if q and q not in (item["name"] + " " + " ".join(item["emails"] + item["tels"]) + " " + item["org"]).lower(): continue
        people.append(item)
    people.sort(key=lambda p: p["name"].lower())
    return render_template("contacts.html", people=people, persons=persons, person=person, q=q, total=len(rows))

@app.post("/admin/reindex")
def admin_reindex():
    _admin_only(); threading.Thread(target=lambda: SI.run(idx_sources(), log=lambda x: print(x, flush=True)), daemon=True).start()
    flash(_("Indicizzazione avviata: dura qualche minuto")); return redirect(url_for("admin"))

@app.route("/healthz")
def healthz(): return "ok"
if __name__ == "__main__":
    threading.Thread(target=ah_loop, daemon=True).start()   # controlli (se AUTOHEAL) e invio degli avvisi accodati di notte
    threading.Thread(target=idx_loop, daemon=True).start()  # indice di ricerca
    app.run(host="0.0.0.0", port=PORT, debug=False)
