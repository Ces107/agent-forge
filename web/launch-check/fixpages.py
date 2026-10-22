"""HTML for the Fix Pack pages: building, locked and the unlocked pack (/fix/<id>)."""

from __future__ import annotations

import html
import time
from urllib import parse as urlparse

import fixpack
import scan as scanner

esc = lambda s: html.escape(str(s), quote=True)  # noqa: E731

PAGE_CSS = """
:root{--ink:#0f172a;--mut:#64748b;--line:#e2e8f0;--bg:#f8fafc}*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Inter","Segoe UI",system-ui,-apple-system,sans-serif}
.w{max-width:1080px;margin:0 auto;padding:24px 28px 48px}.top{display:flex;justify-content:space-between;gap:12px;
align-items:center;margin-bottom:18px}.brand{font-weight:800;font-size:17px}.brand i{font-style:normal;color:#16a34a}
.top a{color:#0f172a;font-weight:700;text-decoration:none;border:1px solid var(--line);background:#fff;border-radius:10px;
padding:8px 14px;font-size:14px}
.card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:20px 22px;margin:0 0 14px}
h1{font-size:28px;margin:0 0 6px;letter-spacing:-.02em}h2{font-size:19px;margin:0 0 12px}
.badge{display:inline-block;background:#fef3c7;color:#92400e;border-radius:99px;padding:3px 10px;font-size:13px;
font-weight:700;margin-bottom:8px}
.acts{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}
.btn{display:inline-block;background:#16a34a;color:#fff;border:0;border-radius:12px;padding:12px 18px;font:inherit;
font-weight:800;text-decoration:none;cursor:pointer}.btn.s{background:#fff;color:#0f172a;border:2px solid #cbd5e1}
.shots{display:grid;grid-template-columns:200px 300px 1fr;gap:14px;align-items:start}
.shots figure{margin:0}.shots img{width:100%;border:6px solid #0f172a;border-radius:14px;display:block;background:#fff}
.shots figcaption{font-size:13px;color:var(--mut);margin-top:6px;font-weight:600}
ol.marks{margin:12px 0 0;padding-left:0;list-style:none;columns:2;column-gap:24px;font-size:13.5px}
ol.marks li{break-inside:avoid;margin:0 0 6px}ol.marks b{background:#dc2626;color:#fff;border-radius:5px;padding:0 5px;
font-size:12px;margin-right:4px}ol.marks span{color:var(--mut)}
.file{border:1px solid var(--line);border-radius:12px;margin:0 0 10px;overflow:hidden}
.file header{display:flex;justify-content:space-between;align-items:center;gap:8px;background:#f1f5f9;padding:8px 12px;
font-weight:800;font-size:14px}.file header span{font-weight:500;color:var(--mut);font-size:12.5px;margin-left:6px}
.file pre,.prompt pre{margin:0;padding:10px 12px;font:12px/1.5 Consolas,"Cascadia Mono",monospace;white-space:pre-wrap;
overflow-wrap:anywhere;max-height:320px;overflow:auto}
.cp{background:#0f172a;color:#fff;border:0;border-radius:8px;padding:5px 10px;font-weight:700;cursor:pointer;font-size:12px}
table{width:100%;border-collapse:collapse}td{border-top:1px solid var(--line);padding:8px 6px;vertical-align:top;font-size:14px}
.sev{font-size:11px;font-weight:800;border-radius:6px;padding:2px 7px;background:#f1f5f9}.sev.HIGH{background:#fee2e2;
color:#b91c1c}.sev.MEDIUM{background:#fef3c7;color:#b45309}
.readme{white-space:pre-wrap;font:12.5px/1.55 Consolas,monospace;background:#f8fafc;border-radius:10px;padding:12px}
.wait{text-align:center;padding:40px 10px}.bar{height:6px;border-radius:6px;background:#e2e8f0;overflow:hidden;margin:16px auto;
max-width:420px}.bar i{display:block;height:100%;width:94%;background:#22c55e;animation:f 60s cubic-bezier(.2,.6,.4,1)}
@keyframes f{from{width:3%}to{width:94%}}
@media (max-width:800px){.shots{grid-template-columns:1fr 1fr}.shots figure:last-child{grid-column:1/3}ol.marks{columns:1}
.w{padding:16px}}
"""

COPY_JS = """(function(){document.querySelectorAll('button.cp').forEach(function(b){b.addEventListener('click',function(){
var t=document.getElementById(b.getAttribute('data-copy')).innerText;function ok(){b.textContent='Copied';}
if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(ok);}else{
var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();
try{document.execCommand('copy');ok();}catch(e){}document.body.removeChild(a);}});});})();"""


def _doc(title, body, nonce, script=""):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,'
            f'initial-scale=1"><title>{esc(title)}</title><meta name="robots" content="noindex"><link rel="icon" '
            f'href="/favicon.svg" type="image/svg+xml"><style>{PAGE_CSS}</style></head><body><div class="w">'
            f'<div class="top"><div class="brand">Launch<i>Check</i></div><a href="/">← Check another site</a></div>'
            f'{body}</div><script nonce="{nonce}">{COPY_JS}{script}</script></body></html>')


def building_page(scan_id, token, nonce, what):
    q = f"?token={token}" if token else ""
    body = (f'<div class="card wait"><h1>{esc(what)}</h1><p>Rendering phone, tablet and desktop and writing your files. '
            'Usually 30–90 s on this server. This page updates by itself.</p><div class="bar"><i></i></div></div>')
    script = (f"(function(){{var u='/fix/{scan_id}/status{q}';function p(){{fetch(u,{{cache:'no-store'}})"
              ".then(function(r){return r.json();}).then(function(d){if(d.state==='done'||d.state==='failed'||"
              "d.state==='gone'){location.reload();}else{setTimeout(p,2500);}}).catch(function(){setTimeout(p,4000);});}"
              "setTimeout(p,2500);})();")
    return _doc("Building your Fix Pack | Launch Check", body, nonce, script)


def locked_page(nonce, message="This Fix Pack is locked"):
    body = (f'<div class="card"><h1>{esc(message)}</h1><p>It opens after checkout. Run a free scan to see your own '
            'result, or look at the sample pack.</p><div class="acts"><a class="btn" href="/fix/demo">See a sample Fix '
            'Pack</a><a class="btn s" href="/">Scan my site</a></div></div>')
    return _doc("Fix Pack | Launch Check", body, nonce)


def pack_page(report, pack, scan_id, token, nonce, demo, dfy=False):
    host = "FlowPilot (demo page)" if demo else (urlparse.urlsplit(report["final_url"]).hostname or report["final_url"])
    q = f"?token={token}" if token else ""
    head = ('<span class="badge">Sample pack for the seeded FlowPilot demo page, a typical AI-built landing page</span>'
            if demo else "")
    rescan = (f'<form method="post" action="/rescan/{esc(scan_id)}{q}" style="display:inline"><button class="btn s" '
              f'type="submit">Re-scan{"" if demo else " (free for 7 days)"}</button></form>')
    if dfy:
        head += ('<p><b>Done-for-you booked.</b> The founder emails you within 24 hours at your checkout email to fix '
                 'these on your site (or open a pull request). Everything below is yours already.</p>')
    top = (f'<div class="card">{head}<h1>Your Fix Pack for {esc(host)}</h1><div>Score at the scan: '
           f'<b>{report["score"]}/100</b> · a {len(pack["steps"])}-step fix prompt · {len(pack["files"])} files · '
           f'{len(pack["shots"])} annotated renders</div><div class="acts"><a class="btn" href="/fix/{esc(scan_id)}/'
           f'{esc(pack["zip_name"])}{q}">Download {esc(pack["zip_name"])}</a>{rescan}</div></div>')
    labels = {375: "Phone · 375 px", 768: "Tablet · 768 px", 1440: "Desktop · 1440 px"}
    figs = [f'<figure><img src="/fix/{esc(scan_id)}/render-{w}.jpg{q}" alt="{labels[w]} render with every problem boxed">'
            f'<figcaption>{labels[w]}</figcaption></figure>' for w in fixpack.RENDER_WIDTHS if w in pack["shots"]]
    marks = ""
    if pack.get("render"):
        items = [f'<li><b>#{f.get("rank")}</b>{esc(scanner.lc.short(f.get("detail", ""), 120))} <span>→ '
                 f'{esc(f.get("fix", ""))}</span></li>' for f in pack["render"].get("findings", [])[:40] if f.get("rank")]
        marks = f'<ol class="marks">{"".join(items)}</ol>'
    shots = (f'<div class="card"><h2>Every problem, boxed</h2><div class="shots">{"".join(figs)}</div>{marks}</div>'
             if figs else '<div class="card"><h2>Renders</h2><p>The renders could not be made this time; open the '
                          'pack again in a minute.</p></div>')
    files = "".join(
        f'<div class="file"><header><div>{esc(name)}<span>{esc(fixpack.FILE_ABOUT.get(name, ""))}</span></div>'
        f'<button class="cp" type="button" data-copy="f{i}">Copy</button></header><pre id="f{i}">{esc(text)}</pre></div>'
        for i, (name, text) in enumerate(pack["files"].items()))
    prompt = fixpack.prompt_text(report, pack["steps"])
    prompt_card = (f'<div class="card prompt"><h2>The full fix prompt <span style="font-weight:500;color:#64748b">for '
                   f'Lovable, Bolt, Cursor or Claude Code ({len(pack["steps"])} steps)</span></h2><button class="cp" '
                   f'type="button" data-copy="pp">Copy prompt</button><pre id="pp">{esc(prompt)}</pre></div>')
    report = pack.get("report") or report
    style = pack.get("style") or []
    style_card = (f'<div class="card"><details><summary><b>Style notes ({len(style)})</b> <span style="color:#64748b">'
                  f'spacing grid, font sizes, colours, line length: polish, not problems</span></summary><ul>'
                  + "".join(f"<li>{esc(x)}</li>" for x in style) + '</ul></details></div>') if style else ""
    rows = "".join(f'<tr><td><span class="sev {f["severity"]}">{f["severity"]}</span></td><td><b>{esc(f["title"])}</b>'
                   + (f'<br><small>{esc(f["where"])}</small>' if f.get("where") else "") + f'<br>{esc(f["fix"])}</td></tr>'
                   for f in report["findings"] if f["severity"] != "INFO")
    listing = "\n".join(f"  {n:<15}{fixpack.FILE_ABOUT.get(n, '')}" for n in pack["files"])
    readme = fixpack.README.format(url=(report.get("pack_url") or report["final_url"]), date=time.strftime("%Y-%m-%d", time.gmtime(pack["built"])),
                                   score=report["score"], listing=listing, rescan="the Re-scan button on this page")
    body = (top + shots + f'<div class="card"><h2>Your files</h2>{files}</div>' + prompt_card
            + f'<div class="card"><h2>All findings with fixes</h2><table>{rows}</table></div>'
            + style_card + f'<div class="card"><h2>Where each file goes</h2><div class="readme">{esc(readme)}</div></div>')
    return _doc(f"Fix Pack for {host} | Launch Check", body, nonce)
