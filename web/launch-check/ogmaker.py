"""OG image + favicon maker: a second micro-product inside Launch Check.

Routes (hooked from app.py): GET /og (the page), POST /og/render (1200x630 PNG preview with a corner mark),
GET /og/pack?<params>&exp=..&sig=.. (the paid zip, behind an HMAC signature).
Nothing is fetched: every pixel is drawn here from the form fields. Nothing is written to disk.

Payment: a plain payment link (LC_OG_URL, e.g. a Stripe Payment Link) carries client_reference_id=<ref>. When the
provider confirms, the main app calls pack_link_for_ref(ref) (or pack_url(params)) and hands the buyer that link.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import io
import json
import os
import re
import secrets
import threading
import time
import unicodedata
import zipfile
from collections import OrderedDict, defaultdict, deque
from functools import lru_cache
from urllib import parse as urlparse

W, H = 1200, 630
PAY_SECRET = os.environ.get("PAY_SECRET") or secrets.token_hex(32)  # links die with the process if unset
OG_URL = os.environ.get("LC_OG_URL", "")
OG_PRICE = os.environ.get("LC_OG_PRICE", "$2")
PACK_TTL = 7 * 86400
MAX_BODY = 2048
TEMPLATES = ("bold", "bar", "gradient")
PRESETS = (  # (background, accent)
    ("#0b1220", "#22c55e"), ("#ffffff", "#2563eb"), ("#1e1b4b", "#f472b6"),
    ("#fef3c7", "#d97706"), ("#0f766e", "#5eead4"), ("#111111", "#f97316"),
)
HEX = re.compile(r"#[0-9a-fA-F]{6}")
LETTERS = re.compile(r"[^\W_]{1,2}")
DOMAIN = re.compile(r"(?:https?://)?((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})(?:/.*)?", re.I)
EMOJI_CATS = {"So", "Sk", "Mn", "Me", "Cf"}
FIELDS = ("title", "subtitle", "site", "icon", "bg", "accent", "template")
SHORT = {"title": "t", "subtitle": "s", "site": "d", "icon": "i", "bg": "bg", "accent": "ac", "template": "tpl"}

_refs: OrderedDict[str, dict] = OrderedDict()  # ref -> params of a recent render (so a paid ref can become a link)
_refs_lock = threading.Lock()
_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()
RATE_MAX, RATE_WINDOW = 60, 60  # renders per IP per minute


# ---------------------------------------------------------------- validation

def _text(raw, limit, name, required=False):
    s = "".join(ch for ch in str(raw or "") if unicodedata.category(ch)[0] != "C" or ch == " ")
    s = re.sub(r"\s+", " ", s).strip()
    if required and not s:
        raise ValueError(f"{name} is required")
    if len(s) > limit:
        raise ValueError(f"{name}: {limit} characters max")
    return s


def _icon(raw):
    s = str(raw or "").strip()
    if not s:
        return ""
    if LETTERS.fullmatch(s):
        return s.upper() if s.isascii() else s
    if len(s) > 12 or s.isascii():
        raise ValueError("icon: one emoji or 1-2 letters")
    for ch in s:
        if unicodedata.category(ch) not in EMOJI_CATS and ch != "\u200d":
            raise ValueError("icon: one emoji or 1-2 letters")
    if not any(unicodedata.category(ch) == "So" for ch in s):
        raise ValueError("icon: one emoji or 1-2 letters")
    return s


def validate(form) -> dict:
    """Form fields (str or [str] values, long or short names) -> clean params. Raises ValueError with a short reason."""
    def get(k):
        v = form.get(k, form.get(SHORT[k], ""))
        return (v[0] if v else "") if isinstance(v, list) else v

    p = {
        "title": _text(get("title"), 70, "title", required=True),
        "subtitle": _text(get("subtitle"), 120, "subtitle"),
        "site": _text(get("site"), 60, "site"),
        "icon": _icon(get("icon")),
        "bg": str(get("bg") or "#0b1220").strip(),
        "accent": str(get("accent") or "#22c55e").strip(),
        "template": str(get("template") or "bold").strip(),
    }
    for k in ("bg", "accent"):
        if not HEX.fullmatch(p[k]):
            raise ValueError(f"{k}: a #rrggbb colour")
        p[k] = p[k].lower()
    if p["template"] not in TEMPLATES:
        raise ValueError("template: bold, bar or gradient")
    return p


def ref_for(p) -> str:
    return hashlib.sha256(json.dumps([p[k] for k in FIELDS]).encode()).hexdigest()[:24]


def _remember(p):
    ref = ref_for(p)
    with _refs_lock:
        _refs[ref] = p
        _refs.move_to_end(ref)
        while len(_refs) > 5000:
            _refs.popitem(last=False)
    return ref


# ---------------------------------------------------------------- drawing

@lru_cache(maxsize=64)
def _font(size, bold=True):
    from PIL import ImageFont
    paths = (("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf")
             if bold else
             ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "C:/Windows/Fonts/arial.ttf"))
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


@lru_cache(maxsize=4)
def _emoji_font():
    from PIL import ImageFont
    for path, size in (("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", 109),  # bitmap: only size 109
                       ("C:/Windows/Fonts/seguiemj.ttf", 160)):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return None


def _rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _lum(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = c
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _ink(c):
    """Readable text colour on background c."""
    return (11, 18, 32) if _lum(c) > 0.42 else (255, 255, 255)


def _mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _glyph(icon, px, color):
    """The icon (emoji or letters) as an RGBA tile about px tall."""
    from PIL import Image, ImageDraw
    if not LETTERS.fullmatch(icon):
        f = _emoji_font()
        if f is not None:
            try:
                tile = Image.new("RGBA", (f.size * 3, f.size * 2), (0, 0, 0, 0))
                ImageDraw.Draw(tile).text((f.size // 2, f.size // 3), icon, font=f, embedded_color=True)
                box = tile.getbbox()
                if box and (box[2] - box[0]) > 1.45 * (box[3] - box[1]) and len(icon) > 2:
                    return _glyph(icon[0] + "️", px, color)  # a ZWJ sequence this Pillow can't shape: first part
                if box:
                    tile = tile.crop(box)
                    s = px / max(tile.size)
                    return tile.resize((max(1, round(tile.width * s)), max(1, round(tile.height * s))),
                                       Image.LANCZOS)
            except (OSError, ValueError):
                pass
    f = _font(round(px * (0.9 if len(icon) == 1 else 0.68)))
    tile = Image.new("RGBA", (px * 2, px * 2), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((px, px), icon, font=f, fill=color, anchor="mm")
    box = tile.getbbox()
    return tile.crop(box) if box else tile


def _wrap(d, text, font, width):
    lines, cur = [], ""
    for word in text.split(" "):
        test = f"{cur} {word}".strip()
        if d.textlength(test, font=font) <= width or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _fit(d, text, width, sizes, max_lines, bold=True):
    for size in sizes:
        f = _font(size, bold)
        lines = _wrap(d, text, f, width)
        if len(lines) <= max_lines and all(d.textlength(ln, font=f) <= width for ln in lines):
            return f, lines
    f = _font(sizes[-1], bold)
    lines = []
    for ln in _wrap(d, text, f, width)[:max_lines]:  # last resort: hard-cut very long words
        while d.textlength(ln, font=f) > width and len(ln) > 1:
            ln = ln[:-2] + "…"
        lines.append(ln)
    return f, lines


def _badge(img, x, y, size, p, center=False):
    """A rounded accent square with the icon in it; returns its right edge."""
    from PIL import Image, ImageDraw
    if not p["icon"]:
        return x
    acc = _rgb(p["accent"])
    if center:
        x -= size // 2
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 4, fill=acc + (255,))
    g = _glyph(p["icon"], round(size * 0.62), _ink(acc))
    tile.alpha_composite(g, ((size - g.width) // 2, (size - g.height) // 2))
    img.alpha_composite(tile, (x, y))
    return x + size


def _gradient(size, a, b):
    from PIL import Image
    mask = Image.linear_gradient("L").rotate(45, expand=True)
    c = mask.width // 2
    mask = mask.crop((c - 90, c - 90, c + 90, c + 90)).resize(size, Image.BILINEAR)
    return Image.composite(Image.new("RGB", size, b), Image.new("RGB", size, a), mask)


def render(p, mark=True, size=(W, H)):
    """The share card as an RGB PIL image."""
    from PIL import Image, ImageDraw
    w, h = size
    bg, acc = _rgb(p["bg"]), _rgb(p["accent"])
    tpl = p["template"]
    if tpl == "gradient":
        base = _gradient(size, bg, _mix(bg, acc, 0.85))
        ground = _mix(bg, acc, 0.45)
    else:
        base = Image.new("RGB", size, bg)
        ground = bg
    img = base.convert("RGBA")
    d = ImageDraw.Draw(img)
    ink = _ink(ground)
    soft = _mix(ink, ground, 0.3)
    sizes_t = (84, 76, 68, 60, 54, 48)
    sizes_s = (38, 34, 30, 28)
    if tpl == "bar":
        d.rectangle((0, 0, 22, h), fill=acc)
        x, width = 100, w - 200
        y = 92
        right = _badge(img, x, y, 72, p)
        if p["site"]:
            sf = _font(30)
            d.text((right + (22 if p["icon"] else 0), y + 36), p["site"], font=sf, fill=soft, anchor="lm")
        ft, lt = _fit(d, p["title"], width, sizes_t, 3)
        fs, ls = _fit(d, p["subtitle"], width, sizes_s, 2, bold=False) if p["subtitle"] else (None, [])
        lh_t, lh_s = round(ft.size * 1.14), round(fs.size * 1.3) if fs else 0
        block = len(lt) * lh_t + (24 + len(ls) * lh_s if ls else 0)
        y = max(200, (h + 120 - block) // 2)
        for ln in lt:
            d.text((x, y), ln, font=ft, fill=ink)
            y += lh_t
        d.rectangle((x, y + 20, x + 90, y + 27), fill=acc)
        y += 50
        for ln in ls:
            d.text((x, y), ln, font=fs, fill=soft)
            y += lh_s
    else:
        width = w - 220
        ft, lt = _fit(d, p["title"], width, sizes_t, 3)
        fs, ls = _fit(d, p["subtitle"], width, sizes_s, 2, bold=False) if p["subtitle"] else (None, [])
        lh_t, lh_s = round(ft.size * 1.14), round(fs.size * 1.3) if fs else 0
        badge = 96 if p["icon"] else 0
        block = (badge + 34 if badge else 0) + len(lt) * lh_t + (20 + len(ls) * lh_s if ls else 0)
        y = (h - block) // 2 - (14 if p["site"] else 0)
        if badge:
            _badge(img, w // 2, y, badge, p, center=True)
            y += badge + 34
        for ln in lt:
            d.text((w // 2, y), ln, font=ft, fill=ink, anchor="ma")
            y += lh_t
        y += 20
        for ln in ls:
            d.text((w // 2, y), ln, font=fs, fill=soft, anchor="ma")
            y += lh_s
        if p["site"]:
            site_c = acc if tpl == "bold" and abs(_lum(acc) - _lum(bg)) > 0.18 else soft
            d.text((w // 2, h - 58), p["site"], font=_font(28), fill=site_c, anchor="ms")
    if mark:
        _mark(img, ground)
    return img.convert("RGB")


def _mark(img, ground):
    from PIL import Image, ImageDraw
    f = _font(17, bold=False)
    text = "made with launch-check"
    tw = round(ImageDraw.Draw(img).textlength(text, font=f))
    pw, ph = tw + 28, 32
    ink = _ink(ground)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    x0, y0 = img.width - pw - 18, img.height - ph - 18
    ld.rounded_rectangle((x0, y0, x0 + pw, y0 + ph), radius=ph // 2,
                         fill=(_mix(ground, ink, 0.12) + (200,)))
    ld.text((x0 + pw // 2, y0 + ph // 2), text, font=f, fill=ink + (190,), anchor="mm")
    # the free preview is for checking the look; a faint diagonal PREVIEW keeps the clean card for the $2 pack
    big = _font(150, bold=True)
    tile = Image.new("RGBA", (900, 220), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((450, 110), "PREVIEW", font=big, fill=ink + (38,), anchor="mm")
    tile = tile.rotate(18, expand=True)
    layer.alpha_composite(tile, ((img.width - tile.width) // 2, (img.height - tile.height) // 2))
    img.alpha_composite(layer)


def icon_image(p, size, rounded=True):
    """Square app icon: accent background, icon (or the title's first letter) in a readable colour."""
    from PIL import Image, ImageDraw
    big = 512
    acc = _rgb(p["accent"])
    tile = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    td = ImageDraw.Draw(tile)
    if rounded:
        td.rounded_rectangle((0, 0, big - 1, big - 1), radius=big // 5, fill=acc + (255,))
    else:
        td.rectangle((0, 0, big, big), fill=acc + (255,))
    icon = p["icon"] or (p["title"][:1].upper() or "•")
    g = _glyph(icon, round(big * (0.6 if rounded else 0.55)), _ink(acc))
    tile.alpha_composite(g, ((big - g.width) // 2, (big - g.height) // 2))
    return tile if size == big else tile.resize((size, size), Image.LANCZOS)


def png(img, optimize=True) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=optimize)
    return buf.getvalue()


def _base_url(p):
    m = DOMAIN.fullmatch(p["site"].strip())
    return f"https://{m.group(1).lower()}" if m else "https://your-site.com"


def meta_tags(p) -> str:
    e = lambda s: html.escape(s, quote=True)  # noqa: E731
    base = _base_url(p)
    desc = p["subtitle"] or p["title"]
    lines = [
        f'<title>{e(p["title"])}</title>',
        f'<meta name="description" content="{e(desc)}">',
        f'<meta property="og:title" content="{e(p["title"])}">',
        f'<meta property="og:description" content="{e(desc)}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:url" content="{base}/">',
        f'<meta property="og:image" content="{base}/og-image.png">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
    ]
    if p["site"]:
        lines.append(f'<meta property="og:site_name" content="{e(p["site"])}">')
    lines += [
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{e(p["title"])}">',
        f'<meta name="twitter:description" content="{e(desc)}">',
        f'<meta name="twitter:image" content="{base}/twitter-image.png">',
        '<link rel="icon" href="/favicon.ico" sizes="48x48">',
        '<link rel="icon" href="/icon-192.png" type="image/png" sizes="192x192">',
        '<link rel="apple-touch-icon" href="/apple-touch-icon.png">',
        '<link rel="manifest" href="/site.webmanifest">',
        f'<meta name="theme-color" content="{p["bg"]}">',
    ]
    return "\n".join(lines) + "\n"


def make_pack(params) -> bytes:
    """The paid zip: clean og/twitter images, favicon set, web manifest and the meta tags."""
    p = validate(params)
    name = p["site"] or p["title"]
    manifest = {"name": name, "short_name": name[:12],
                "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                          {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}],
                "theme_color": p["bg"], "background_color": p["bg"], "display": "standalone"}
    ico = io.BytesIO()
    icon_image(p, 512).save(ico, "ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    files = {
        "og-image.png": png(render(p, mark=False)),
        "twitter-image.png": png(render(p, mark=False, size=(1200, 600))),
        "favicon.ico": ico.getvalue(),
        "apple-touch-icon.png": png(icon_image(p, 180, rounded=False)),
        "icon-192.png": png(icon_image(p, 192)),
        "icon-512.png": png(icon_image(p, 512)),
        "site.webmanifest": json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        "meta-tags.html": "<!-- paste inside <head>; put the image and icon files at your site root -->\n"
                          + meta_tags(p),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for fn, data in files.items():
            z.writestr(fn, data)
    return buf.getvalue()


# ---------------------------------------------------------------- signed download links

def _canon(p, exp):
    return urlparse.urlencode([(SHORT[k], p[k]) for k in FIELDS] + [("exp", str(exp))])


def _sig(canon):
    return hmac.new(PAY_SECRET.encode(), f"ogpack:{canon}".encode(), hashlib.sha256).hexdigest()[:32]


def pack_url(params, ttl=PACK_TTL, now=None) -> str:
    """The signed /og/pack link the main app hands a buyer once the payment is confirmed."""
    p = validate(params)
    canon = _canon(p, int((now or time.time()) + ttl))
    return f"/og/pack?{canon}&sig={_sig(canon)}"


def pack_link_for_ref(ref, ttl=PACK_TTL):
    """client_reference_id from the payment -> signed link, while the render is still remembered (else None)."""
    with _refs_lock:
        p = _refs.get(ref)
    return pack_url(p, ttl) if p else None


def check_pack_query(query, now=None):
    """Query string -> params when the signature and expiry hold, else None."""
    q = urlparse.parse_qs(query, keep_blank_values=True)
    try:
        exp = int((q.get("exp") or ["0"])[0])
        p = validate(q)
    except ValueError:
        return None
    sig = (q.get("sig") or [""])[0]
    if exp < (now or time.time()) or not hmac.compare_digest(_sig(_canon(p, exp)), sig):
        return None
    return p


# ---------------------------------------------------------------- page

CSS = """
:root{--ink:#0b1220;--mut:#5b6678;--line:#e3e8ef;--bg:#f6f8fb;--card:#fff;--acc:#16a34a}
*{box-sizing:border-box}html,body{margin:0}body{background:var(--bg);color:var(--ink);
font:16px/1.5 "Inter","Segoe UI",system-ui,-apple-system,sans-serif;-webkit-font-smoothing:antialiased}
.hero{min-height:100svh;padding:36px 16px 56px;
background:radial-gradient(1200px 600px at 50% -10%,#dcfce7 0,rgba(220,252,231,0) 60%),var(--bg)}
.in{width:100%;max-width:1040px;margin:0 auto}
.brand{font-weight:800;font-size:17px;margin-bottom:22px;display:flex;align-items:center;gap:8px;color:inherit;
text-decoration:none}.brand i{font-style:normal;color:var(--acc)}
h1{font-size:clamp(28px,5vw,44px);line-height:1.05;letter-spacing:-.03em;margin:0 0 20px;font-weight:850}
.grid{display:grid;grid-template-columns:minmax(0,340px) minmax(0,1fr);gap:22px;align-items:start}
@media (max-width:820px){.grid{grid-template-columns:1fr}.pv{order:-1}}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:16px;
box-shadow:0 1px 2px rgba(11,18,32,.05),0 18px 50px rgba(11,18,32,.08)}
label{display:block;font-size:12px;font-weight:750;letter-spacing:.05em;text-transform:uppercase;color:var(--mut);
margin:12px 0 5px}label:first-child{margin-top:0}
input[type=text]{width:100%;border:1px solid var(--line);border-radius:10px;font:inherit;padding:10px 12px;
background:#fff;color:var(--ink);outline:0}input[type=text]:focus{border-color:#94a3b8}
.row{display:flex;gap:10px}.row>div{flex:1;min-width:0}
input[type=color]{width:100%;height:40px;border:1px solid var(--line);border-radius:10px;padding:3px;background:#fff;
cursor:pointer}
.sw{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.sw button{width:34px;height:34px;border-radius:50%;border:2px solid #fff;box-shadow:0 0 0 1px var(--line);
cursor:pointer;padding:0}
.seg{display:flex;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.seg label{flex:1;margin:0;text-align:center;padding:9px 4px;cursor:pointer;font-size:13px;text-transform:none;
letter-spacing:0;color:var(--ink);font-weight:650;border-left:1px solid var(--line)}
.seg label:first-of-type{border-left:0}.seg input{position:absolute;opacity:0;pointer-events:none}
.seg label:has(input:checked){background:var(--ink);color:#fff}
.pv img{width:100%;height:auto;aspect-ratio:1200/630;display:block;border-radius:14px;border:1px solid var(--line);
background:#e2e8f0}
.pv .err{color:#991b1b;font-weight:650;font-size:14px;min-height:21px;margin:8px 2px 0}
.acts{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}
.btn{border:0;border-radius:12px;background:var(--ink);color:#fff;font:inherit;font-weight:750;font-size:15px;
padding:12px 18px;cursor:pointer;text-decoration:none;display:inline-block}
.btn.ghost{background:#fff;color:var(--ink);border:1px solid var(--line)}
.btn[aria-disabled=true]{opacity:.55;cursor:not-allowed}
textarea{width:100%;height:150px;margin-top:12px;border:1px solid var(--line);border-radius:12px;padding:10px;
font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#1e293b;background:#fff;resize:vertical}
details{margin-top:12px}summary{cursor:pointer;font-weight:700;font-size:14px;color:#334155}
"""

JS = r"""(function(){
var f=document.getElementById('og'),img=document.getElementById('pv'),er=document.getElementById('er'),
dl=document.getElementById('dl'),mt=document.getElementById('mt'),buy=document.getElementById('buy'),t=null,seq=0;
function e(s){return s.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function meta(){var ti=f.title.value.trim(),su=f.subtitle.value.trim()||ti,si=f.site.value.trim(),
m=si.match(/^(?:https?:\/\/)?((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})(?:\/.*)?$/i),
b=m?'https://'+m[1].toLowerCase():'https://your-site.com';
var l=['<title>'+e(ti)+'</title>','<meta name="description" content="'+e(su)+'">',
'<meta property="og:title" content="'+e(ti)+'">','<meta property="og:description" content="'+e(su)+'">',
'<meta property="og:type" content="website">','<meta property="og:url" content="'+b+'/">',
'<meta property="og:image" content="'+b+'/og-image.png">','<meta property="og:image:width" content="1200">',
'<meta property="og:image:height" content="630">','<meta name="twitter:card" content="summary_large_image">',
'<meta name="twitter:image" content="'+b+'/og-image.png">'];mt.value=l.join('\n');}
function go(){var n=++seq;meta();fetch('/og/render',{method:'POST',body:new URLSearchParams(new FormData(f))})
.then(function(r){if(n!==seq)return;if(!r.ok)return r.text().then(function(x){er.textContent=x;});
er.textContent='';var ref=r.headers.get('X-OG-Ref');
if(buy&&ref&&buy.dataset.base){var u=buy.dataset.base;buy.href=u+(u.indexOf('?')<0?'?':'&')+'client_reference_id='+ref;}
return r.blob().then(function(b){var rd=new FileReader();rd.onload=function(){if(n===seq){img.src=rd.result;dl.href=rd.result;}};
rd.readAsDataURL(b);});}).catch(function(){er.textContent='Preview failed. Retry.';});}
function later(){clearTimeout(t);t=setTimeout(go,350);}
f.addEventListener('input',later);f.addEventListener('change',later);
f.addEventListener('submit',function(ev){ev.preventDefault();go();});
document.querySelectorAll('.sw button').forEach(function(b){b.addEventListener('click',function(){
f.bg.value=b.dataset.bg;f.accent.value=b.dataset.ac;go();});});
document.getElementById('cp').addEventListener('click',function(){mt.select();
(navigator.clipboard?navigator.clipboard.writeText(mt.value):Promise.reject()).catch(function(){document.execCommand('copy');});
this.textContent='Copied';});
go();})();"""


def og_page(nonce) -> str:
    e = lambda s: html.escape(s, quote=True)  # noqa: E731
    sw = "".join(f'<button type="button" data-bg="{b}" data-ac="{a}" aria-label="{b} and {a}" '
                 f'style="background:linear-gradient(135deg,{b} 50%,{a} 50%)"></button>' for b, a in PRESETS)
    seg = "".join(f'<label><input type="radio" name="template" value="{k}"{" checked" if k == "bold" else ""}>'
                  f'{lab}</label>' for k, lab in (("bold", "Bold"), ("bar", "Bar"), ("gradient", "Gradient")))
    if OG_URL:
        buy = (f'<a class="btn" id="buy" data-base="{e(OG_URL)}" href="{e(OG_URL)}" rel="noopener">'
               f'Download pack · {e(OG_PRICE)}</a>')
    else:
        buy = (f'<span class="btn" id="buy" aria-disabled="true">Download (launch price {e(OG_PRICE)}) — opens soon'
               '</span>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OG image + favicon maker | Launch Check</title>
<meta name="description" content="Make the preview card your links are missing: a 1200x630 OG image, favicons and the meta tags.">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<meta property="og:title" content="OG image + favicon maker"><meta property="og:image" content="/og.png">
<meta name="twitter:card" content="summary_large_image"><style>{CSS}</style></head><body>
<main class="hero"><div class="in">
<a class="brand" href="/"><img src="/favicon.svg" alt="" width="26" height="26"><span>Launch<i>Check</i></span></a>
<h1>Your link preview card.</h1>
<div class="grid">
<form class="card" id="og" autocomplete="off">
<label for="t">Title</label><input type="text" id="t" name="title" maxlength="70" required value="Ship it faster">
<label for="s">Subtitle</label><input type="text" id="s" name="subtitle" maxlength="120" value="The launch checklist for makers">
<div class="row"><div><label for="d">Site</label><input type="text" id="d" name="site" maxlength="60"
 placeholder="yoursite.com" spellcheck="false" autocapitalize="off"></div>
<div style="flex:0 0 92px"><label for="i">Icon</label><input type="text" id="i" name="icon" maxlength="12" value="🚀"
 placeholder="🚀 / AB"></div></div>
<div class="row"><div><label for="bg">Background</label><input type="color" id="bg" name="bg" value="#0b1220"></div>
<div><label for="ac">Accent</label><input type="color" id="ac" name="accent" value="#22c55e"></div></div>
<div class="sw">{sw}</div>
<label>Style</label><div class="seg">{seg}</div>
</form>
<div class="pv"><img id="pv" alt="Preview, 1200 x 630" width="1200" height="630">
<div class="err" id="er" role="status"></div>
<div class="acts">{buy}<a class="btn ghost" id="dl" download="og-preview.png" href="#">Free preview</a></div>
<details><summary>Meta tags</summary><textarea id="mt" readonly spellcheck="false"></textarea>
<button type="button" class="btn ghost" id="cp" style="margin-top:8px">Copy</button></details>
</div></div></div></main><script nonce="{nonce}">{JS}</script></body></html>"""


# ---------------------------------------------------------------- routes (called from app.py)

def _ip(handler):
    for name in ("CF-Connecting-IP", "True-Client-IP"):
        v = (handler.headers.get(name) or "").strip()
        if v:
            return v[:64]
    xff = (handler.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return (xff or handler.client_address[0])[:64]


def _rate_ok(ip, now=None):
    now = now or time.time()
    with _hits_lock:
        q = _hits[ip]
        while q and now - q[0] > RATE_WINDOW:
            q.popleft()
        if len(q) >= RATE_MAX:
            return False
        q.append(now)
        if len(_hits) > 20_000:
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > RATE_WINDOW][:10_000]:
                _hits.pop(k, None)
        return True


def handle_get(handler, path, query):
    if path == "/og":
        n = secrets.token_urlsafe(16)
        return handler.send(200, og_page(n), nonce=n, cache="no-cache")
    if path == "/og/pack":
        p = check_pack_query(query)
        if not p:
            return handler.send(403, "This download link is invalid or has expired.", "text/plain; charset=utf-8")
        return handler.send(200, make_pack(p), "application/zip",
                            extra={"Content-Disposition": 'attachment; filename="og-pack.zip"'})
    return handler.send(404, "not found", "text/plain; charset=utf-8")


def handle_post(handler, path):
    if path != "/og/render":
        return handler.send(404, "not found", "text/plain; charset=utf-8")
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        length = 0
    if length <= 0 or length > MAX_BODY:
        return handler.send(413, "Too long.", "text/plain; charset=utf-8")
    if not _rate_ok(_ip(handler)):
        return handler.send(429, "Slow down a little.", "text/plain; charset=utf-8")
    form = urlparse.parse_qs(handler.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True)
    try:
        p = validate(form)
    except ValueError as exc:
        return handler.send(400, str(exc).capitalize() + ".", "text/plain; charset=utf-8")
    return handler.send(200, png(render(p, mark=True), optimize=False), "image/png",
                        extra={"X-OG-Ref": _remember(p)})
