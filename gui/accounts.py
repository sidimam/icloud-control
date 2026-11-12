"""Profili della GUI (dalla 3.0): admin + un profilo per persona, password e 2FA TOTP facoltativo.

Stato in /app/state/profiles.json:
  {"seeded": true, "profiles": {"<nome>": {"role": "admin", "pw": "<hash werkzeug>", "totp": "<segreto base32>",
                                           "totp_on": false, "recovery": ["<hash>", …], "telegram": "<chat id privata>",
                                           "created": ts, "last_login": ts}}}
Recupero (3.0): codici monouso generati all'attivazione del 2FA e codice a 6 cifre mandato dal bot alla chat Telegram privata
del profilo (vedi app.py, /recover).
Al primo avvio della 3.0 il profilo admin eredita la password della 2.x (/app/state/gui_password o GUI_PASSWORD_HASH) e vengono
creati i profili di DEFAULT_PROFILES senza password: entrano solo dopo che un amministratore gliela imposta.
GUI_RESET_PASSWORD reimposta la password dell'admin e gli spegne il 2FA (password o telefono persi).
"""
import io, json, os, secrets, time
import pyotp, qrcode, qrcode.image.svg
from werkzeug.security import check_password_hash, generate_password_hash

APP_DIR = os.environ.get("APP_DIR", "/app")
FILE = os.path.join(APP_DIR, "state", "profiles.json")
OLD_PW_FILE = os.path.join(APP_DIR, "state", "gui_password")
ADMIN = os.environ.get("GUI_USER", "admin")
DEFAULT_PROFILES = [p.strip() for p in os.environ.get("GUI_PROFILES", "sdimambro,dmanieri,sofdimambro,mdimambro").split(",") if p.strip()]
ROLES = {"admin": "Amministratore", "user": "Utente"}   # utente: vede e gestisce solo il proprio container
ISSUER = "iCloud Control"


def _blank(role="admin"):
    return {"role": role, "pw": "", "totp": "", "totp_on": False, "recovery": [], "telegram": "", "created": time.time(), "last_login": 0}


def load():
    try:
        d = json.load(open(FILE))
    except (FileNotFoundError, ValueError):
        d = {}
    d.setdefault("profiles", {})
    return d


def save(d):
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    tmp = FILE + ".tmp"
    json.dump(d, open(tmp, "w"), indent=1, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, FILE)


def bootstrap(reset_password=""):
    """Crea admin e profili predefiniti una sola volta; applica GUI_RESET_PASSWORD all'admin."""
    d = load()
    if not d.get("seeded"):
        admin = d["profiles"].setdefault(ADMIN, _blank())
        if not admin["pw"]:
            try:
                admin["pw"] = open(OLD_PW_FILE).read().strip()
            except FileNotFoundError:
                admin["pw"] = os.environ.get("GUI_PASSWORD_HASH", "")
        for name in DEFAULT_PROFILES:
            d["profiles"].setdefault(name, _blank())
        d["seeded"] = True
    d["profiles"].setdefault(ADMIN, _blank())
    if reset_password:
        a = d["profiles"][ADMIN]
        a.update(pw=generate_password_hash(reset_password), totp="", totp_on=False)
        print(f"GUI: password di {ADMIN} reimpostata e 2FA spento da GUI_RESET_PASSWORD (svuota la variabile e riavvia)", flush=True)
    save(d)


def profiles():
    return load()["profiles"]


def get(name):
    return profiles().get(name)


def any_password():
    return any(p.get("pw") for p in profiles().values())


def check_password(name, pw):
    p = get(name)
    return bool(p and p.get("pw") and check_password_hash(p["pw"], pw))


def update(name, **fields):
    d = load()
    if name not in d["profiles"]:
        return False
    d["profiles"][name].update(fields)
    save(d)
    return True


def set_password(name, pw):
    return update(name, pw=generate_password_hash(pw))


def add(name, role="admin", pw=""):
    d = load()
    if name in d["profiles"]:
        return False
    p = _blank(role)
    if pw:
        p["pw"] = generate_password_hash(pw)
    d["profiles"][name] = p
    save(d)
    return True


def remove(name):
    d = load()
    if name not in d["profiles"] or name == ADMIN:
        return False
    del d["profiles"][name]
    save(d)
    return True


def admins():
    return [n for n, p in profiles().items() if p.get("role") == "admin"]


def is_admin(name):
    p = get(name)
    return bool(p and p.get("role") == "admin")


# ---- 2FA TOTP (app Password di Apple, Google Authenticator, 1Password, Bitwarden…)
def new_secret():
    return pyotp.random_base32()


def totp_uri(name, secret):
    return pyotp.TOTP(secret).provisioning_uri(name=name, issuer_name=ISSUER)


def totp_qr_svg(uri):
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def verify_totp(secret, code):
    code = "".join(ch for ch in str(code) if ch.isdigit())
    return bool(secret and len(code) == 6 and pyotp.TOTP(secret).verify(code, valid_window=1))


# ---- codici di recupero: 8 codici monouso tipo «k7m2-x9qp», salvati solo come hash
_ALPHA = "abcdefghjkmnpqrstuvwxyz23456789"   # senza 0/o, 1/l/i


def new_recovery_codes(name, n=8):
    codes = ["".join(secrets.choice(_ALPHA) for _ in range(4)) + "-" + "".join(secrets.choice(_ALPHA) for _ in range(4)) for _ in range(n)]
    update(name, recovery=[generate_password_hash(c) for c in codes])
    return codes


def _norm(code):
    c = "".join(ch for ch in str(code).lower() if ch.isalnum())
    return c[:4] + "-" + c[4:] if len(c) == 8 else ""


def use_recovery_code(name, code):
    c = _norm(code)
    p = get(name)
    if not c or not p:
        return False
    left = p.get("recovery", [])
    for h in left:
        if check_password_hash(h, c):
            update(name, recovery=[x for x in left if x != h])
            return True
    return False
