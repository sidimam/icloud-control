"""Lingua, tema e icone della GUI (dalla 3.2).

Lingua e tema: nel profilo di chi è entrato (campi "lang", "theme"), altrimenti nel cookie, altrimenti italiano e tema di sistema.
I testi sono scritti in italiano nel codice e nei template (`_("…")`); EN traduce in inglese, con gli stessi segnaposto {a0}, {a1}…
Le icone sono disegni SVG propri nello stile dei simboli Apple (tratto arrotondato, colore del testo).
"""
from flask import request, session
from markupsafe import Markup
import accounts
from i18n_en import EN

LANGS = {"it": "Italiano", "en": "English"}
THEMES = ("system", "light", "dark")


def _profile():
    n = session.get("user")
    return accounts.get(n) if n else None


def current_lang():
    try:
        l = (_profile() or {}).get("lang") or request.cookies.get("lang") or "it"
    except RuntimeError:
        l = "it"
    return l if l in LANGS else "it"


def current_theme():
    try:
        t = (_profile() or {}).get("theme") or request.cookies.get("theme") or "system"
    except RuntimeError:
        t = "system"
    return t if t in THEMES else "system"


def _(s, **kw):
    t = EN.get(s, s) if current_lang() == "en" else s
    return t.format(**kw) if kw else t


_P = {
    "users": '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c.8-3.2 3-5 5.5-5s4.7 1.8 5.5 5"/><circle cx="17" cy="9" r="2.5"/><path d="M15.6 14.2c2.3.1 4.1 1.7 4.9 4.8"/>',
    "plus": '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>',
    "person": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="10" r="3"/><path d="M6.6 18.2c1.2-2.2 3.1-3.3 5.4-3.3s4.2 1.1 5.4 3.3"/>',
    "gear": '<circle cx="12" cy="12" r="7" stroke-width="3" stroke-dasharray="2.4 2.1"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.8"/>',
    "logout": '<path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4"/><path d="M10 16l-4-4 4-4M6 12h10"/>',
    "cloud": '<path d="M7 18h10a4 4 0 0 0 .7-7.94A6 6 0 0 0 6.3 9.3 4.4 4.4 0 0 0 7 18z"/>',
    "folder": '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v7a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 16.5z"/>',
    "photo": '<rect x="3" y="5" width="18" height="14" rx="2.5"/><circle cx="8.5" cy="10" r="1.6"/><path d="M21 16l-5-5-8 8"/>',
    "restart": '<path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4.5V9h-4.5"/>',
    "stop": '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>',
    "play": '<path d="M8 5.8v12.4c0 .8.9 1.3 1.6.8l9.4-6.2a1 1 0 0 0 0-1.6L9.6 5c-.7-.5-1.6 0-1.6.8z"/>',
    "doc": '<path d="M7 3h7l4 4v12.5A1.5 1.5 0 0 1 16.5 21h-9A1.5 1.5 0 0 1 6 19.5V4.5A1.5 1.5 0 0 1 7.5 3z"/><path d="M14 3v4h4M9.5 12h5M9.5 15.5h5"/>',
    "bell": '<path d="M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
    "key": '<circle cx="8" cy="15" r="3.5"/><path d="M10.5 12.5L19 4M16 7l2.5 2.5M13.5 9.5l2 2"/>',
    "lock": '<rect x="5" y="11" width="14" height="9.5" rx="2.2"/><path d="M8.5 11V8a3.5 3.5 0 0 1 7 0v3"/>',
    "shield": '<path d="M12 3l7 2.8v5.4c0 4.4-2.9 7.9-7 9.8-4.1-1.9-7-5.4-7-9.8V5.8z"/><path d="M9 12.2l2.1 2.1L15.3 10"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="3.8" ry="9"/><path d="M3 12h18"/>',
    "theme": '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 0 0 18z" fill="currentColor"/>',
    "chevron": '<path d="M9 5.5l6.5 6.5L9 18.5"/>',
    "send": '<path d="M20.5 3.5L3.5 10.6l6.2 2.2 2.2 6.2z"/><path d="M9.7 12.8l4.3-4.3"/>',
    "warn": '<path d="M10.3 4.3a2 2 0 0 1 3.4 0l7.6 13.2a2 2 0 0 1-1.7 3H4.4a2 2 0 0 1-1.7-3z"/><path d="M12 9.5v4.5M12 17.2v.1"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7"/>',
    "trash": '<path d="M4.5 7h15M10 4h4M6.5 7l.8 12a2 2 0 0 0 2 1.9h5.4a2 2 0 0 0 2-1.9l.8-12"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
    "chart": '<path d="M12 3.5a8.5 8.5 0 1 0 8.5 8.5H12z"/><path d="M15 3.8A8.5 8.5 0 0 1 20.2 9H15z"/>',
    "filter": '<path d="M4 6h16M7 12h10M10 18h4"/>',
    "grid": '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    "table": '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M3.5 10h17M3.5 14.5h17M9 10v9"/>',
    "cards": '<rect x="3.5" y="4.5" width="7.5" height="15" rx="1.5"/><rect x="13" y="4.5" width="7.5" height="15" rx="1.5"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r=".8"/><circle cx="4.5" cy="12" r=".8"/><circle cx="4.5" cy="18" r=".8"/>',
}


def info(text, label=None):
    """Pulsante «i» (3.17): le descrizioni stanno dietro una i e si aprono in un popup (vedi base.html)."""
    from markupsafe import escape
    lab = label or _("Informazioni")
    return Markup(f'<button type="button" class="ibtn" aria-label="{escape(lab)}" title="{escape(lab)}" aria-expanded="false" data-info="{escape(text)}">i</button>')


def icon(name, size=18, cls="ic"):
    return Markup(f'<svg class="{cls}" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                  f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_P.get(name, "")}</svg>')
