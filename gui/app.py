#!/usr/bin/env python3
"""iCloud Dashboard (ex icloud-dispatcher) GUI: utenti = container iCloud (foto: boredazfcuk/icloudpd; iCloud Drive: mandarons/icloud-drive), chat Telegram, cosa scaricare, rinnovo 2FA."""
import json, os, re, secrets, subprocess, shutil, datetime, html, time, threading
from werkzeug.security import check_password_hash, generate_password_hash
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, redirect, url_for, render_template, flash, abort, session

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
    if request.endpoint in ("login", "login_2fa", "recover", "prefs", "healthz", "static") or not accounts.any_password() or me(): return None
    return redirect(url_for("login", next=request.full_path if request.method == "GET" else "/"))
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
    with ThreadPoolExecutor(max_workers=8) as ex: users = list(ex.map(lambda nu: user_view(*nu), c["users"].items()))
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
    appdata = host_to_local(host_appdata); dest = host_to_local(dest_host)
    if dest == dest_host: flash(_("La destinazione deve stare sotto /mnt/user/documents (montato in iCloud Dashboard come /documents)")); return redirect(url_for("new"))
    photos_local = host_to_local(photos_host) if photos else None
    if photos and photos_local == photos_host: flash(_("La cartella foto deve stare sotto /mnt/user/media/photos (montato in iCloud Dashboard come /photos)")); return redirect(url_for("new"))
    for path in [appdata, dest] + ([photos_local] if photos else []):
        os.makedirs(path, exist_ok=True); shutil.chown(path, int(d["user_id"]), int(d["group_id"]))
    open(os.path.join(appdata, "config.yaml"), "w").write(f"""# {cont} — iCloud Drive di {label} → {dest_host}{photos_note} (solo download, {d.get('drive_image', 'mandarons/icloud-drive')})
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
  <Repository>{d.get('drive_image', 'mandarons/icloud-drive')}</Repository>
  <Registry>https://hub.docker.com/r/mandarons/icloud-drive</Registry>
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
""" + (f'''  <Config Name="Foto personali" Target="/icloud/photos/all" Default="{html.escape(photos_host)}" Mode="rw" Description="Libreria PrimarySync, cartelle %Y/%m" Type="Path" Display="always" Required="false" Mask="false">{html.escape(photos_host)}</Config>
''' if photos else "") + """</Container>
""")
    rc, out, err = sh(f"docker run -d --name {cont} --restart unless-stopped --label net.unraid.docker.managed=dockerman -e PUID={d['user_id']} -e PGID={d['group_id']} -e TZ={d['tz']} -e ENV_CONFIG_FILE_PATH=/config/config.yaml -v {host_appdata}:/config -v {json.dumps(dest_host)}:/icloud/drive{photos_mount} --label net.unraid.docker.icon={icon} {d.get('drive_image', 'mandarons/icloud-drive')}", 180)
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
    return render_template("admin.html", rows=rows, n=nset(), default_chat=cfg().get("default_chat_id", ""))
@app.post("/admin/profile/<name>/<action>")
def admin_profile(name, action):
    _admin_only(); f = request.form
    if not accounts.get(name): abort(404)
    if action == "password":
        if len(f.get("new", "")) < 8: flash(_("La password deve avere almeno 8 caratteri"))
        else: accounts.set_password(name, f["new"]); flash(_("Password di {a0} impostata", a0=name))
    elif action == "totp_off": accounts.update(name, totp="", totp_on=False, recovery=[]); flash(_("Verifica in due passaggi di {a0} disattivata", a0=name))
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

@app.route("/healthz")
def healthz(): return "ok"
if __name__ == "__main__":
    threading.Thread(target=ah_loop, daemon=True).start()   # controlli (se AUTOHEAL) e invio degli avvisi accodati di notte
    app.run(host="0.0.0.0", port=PORT, debug=False)
