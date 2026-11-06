"""Grafici della pagina Riepilogo (3.12): anelli SVG disegnati sul server, senza librerie.

Colori: la tavolozza categoriale di riferimento (8 tinte in ordine fisso, validata per daltonismo) in --c1…--c8 di base.html,
con le varianti scure. Ogni anello ha la legenda con valore e percentuale (l'identità non dipende solo dal colore), le fette
sono separate da 2 px e hanno un tooltip; passando sulla legenda si evidenzia la fetta e viceversa.
"""
import math
from markupsafe import Markup, escape


def donut(items, fmt, center, sub="", uid="d"):
    """items: [(etichetta, valore, slot 1-8, link o "")]. fmt: valore → testo. center: testo al centro."""
    items = [(l, v, s, h) for l, v, s, h in items if v and v > 0]
    total = sum(v for _, v, _, _ in items) or 1
    r, w, cx = 54, 18, 70
    circ = 2 * math.pi * r
    gap = 2 if len(items) > 1 else 0
    segs, legend, off = [], [], 0.0
    for i, (lbl, val, slot, href) in enumerate(items):
        ln = circ * val / total
        dash = max(ln - gap, 1.2)   # anche le fette minuscole restano visibili
        pct = 100 * val / total
        pct_t = "<0.1%" if pct < 0.1 else (f"{pct:.1f}%" if pct < 10 else f"{pct:.0f}%")
        tip = f"{lbl}: {fmt(val)} ({pct_t})"
        circle = (f'<circle class="seg" data-i="{uid}-{i}" cx="{cx}" cy="{cx}" r="{r}" fill="none" stroke="var(--c{slot})" '
                  f'stroke-width="{w}" stroke-dasharray="{dash:.2f} {circ - dash:.2f}" stroke-dashoffset="{-off:.2f}" '
                  f'transform="rotate(-90 {cx} {cx})"><title>{escape(tip)}</title></circle>')
        segs.append(f'<a href="{escape(href)}">{circle}</a>' if href else circle)
        row = (f'<span class="sw" style="background:var(--c{slot})"></span><span class="lbl">{escape(lbl)}</span>'
               f'<span class="val">{escape(fmt(val))} <span class="mut">· {pct_t}</span></span>')
        legend.append(f'<li data-i="{uid}-{i}">' + (f'<a href="{escape(href)}">{row}</a>' if href else row) + '</li>')
        off += ln
    if not items:
        segs.append(f'<circle cx="{cx}" cy="{cx}" r="{r}" fill="none" stroke="var(--fill)" stroke-width="{w}"/>')
    svg = (f'<svg class="donut" viewBox="0 0 140 140" role="img" aria-label="{escape(center)} {escape(sub)}">{"".join(segs)}'
           f'<text x="{cx}" y="{cx - 2}" text-anchor="middle" class="dc">{escape(center)}</text>'
           f'<text x="{cx}" y="{cx + 15}" text-anchor="middle" class="ds">{escape(sub)}</text></svg>')
    return Markup(f'<div class="viz">{svg}<ul class="lg">{"".join(legend)}</ul></div>')
