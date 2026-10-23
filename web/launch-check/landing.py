"""The home page: hero with the scan form and a marked-up phone, how it works, what it checks, pricing, FAQ.

Pure rendering: app.page() passes the copy for the variant and the live prices, this returns the HTML.
Every claim on the page matches what the scanner does (scan.py, fixpack.py); the example phone is the FlowPilot demo
(static/demo-flowpilot.html) rendered by the real checker, and its fix numbers are that page's real colours.
"""

from __future__ import annotations

import html
import re


def esc(s):
    return html.escape(str(s), quote=True)


CSS = """
@font-face{font-family:"InterV";src:url(/inter-var.woff2) format("woff2");font-weight:100 900;font-display:swap}
:root{--bg:#070b14;--bg2:#0b1222;--card:#0e1628;--card2:#111b30;--line:rgba(148,163,184,.14);--line2:rgba(148,163,184,.24);
--ink:#f1f5f9;--ink2:#cbd5e1;--mut:#94a3b8;--acc:#22c55e;--acc2:#4ade80;--red:#f43f5e;--amber:#fbbf24;--blue:#60a5fa;
--r:22px;--sans:"InterV","Inter","Segoe UI",system-ui,-apple-system,sans-serif}
*{box-sizing:border-box}html{scroll-behavior:smooth;-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 var(--sans);-webkit-font-smoothing:antialiased;overflow-x:hidden}
a{color:inherit}img{max-width:100%}
.w{width:100%;max-width:1160px;margin:0 auto;padding:0 24px}
/* nav */
.nav{position:sticky;top:0;z-index:20;backdrop-filter:saturate(1.4) blur(14px);-webkit-backdrop-filter:saturate(1.4) blur(14px);
background:rgba(7,11,20,.72);border-bottom:1px solid var(--line)}
.nav .w{display:flex;align-items:center;gap:28px;height:62px}
.logo{display:flex;align-items:center;gap:9px;font-weight:800;font-size:17px;letter-spacing:-.01em;text-decoration:none}
.logo i{font-style:normal;color:var(--acc2)}.logo img{width:26px;height:26px}
.nav nav{margin-left:auto}.nav ul{display:flex;gap:26px;list-style:none;margin:0;padding:0}
.nav ul a{text-decoration:none;color:var(--ink2);font-size:14.5px;font-weight:550}.nav ul a:hover{color:#fff}
.pill{display:inline-flex;align-items:center;gap:8px;border-radius:999px;font-weight:700;font-size:14px;text-decoration:none;
padding:9px 16px;border:1px solid var(--line2);color:var(--ink);background:rgba(255,255,255,.03)}
.pill:hover{border-color:var(--mut)}.pill svg{width:16px;height:16px}
/* hero */
.hero{position:relative;padding:72px 0 40px;overflow:hidden}
.hero:before{content:"";position:absolute;inset:-30% -10% auto;height:900px;pointer-events:none;
background:radial-gradient(600px 380px at 72% 30%,rgba(34,197,94,.20),transparent 70%),
radial-gradient(520px 340px at 18% 8%,rgba(96,165,250,.13),transparent 70%)}
.hero:after{content:"";position:absolute;inset:0;pointer-events:none;opacity:.5;
background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);
background-size:56px 56px;mask-image:radial-gradient(ellipse 70% 60% at 50% 20%,#000 30%,transparent 75%);
-webkit-mask-image:radial-gradient(ellipse 70% 60% at 50% 20%,#000 30%,transparent 75%)}
.hg{position:relative;z-index:1;display:grid;grid-template-columns:1.08fr .92fr;gap:40px;align-items:center}
.eyebrow{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line2);background:rgba(255,255,255,.04);
border-radius:999px;padding:6px 14px 6px 8px;font-size:13.5px;font-weight:600;color:var(--ink2)}
.eyebrow b{background:var(--acc);color:#052e16;border-radius:999px;padding:2px 9px;font-size:12px;font-weight:800}
h1{font-size:clamp(40px,6.2vw,72px);line-height:1.02;letter-spacing:-.045em;margin:22px 0 20px;font-weight:820}
h1 em{font-style:normal;background:linear-gradient(92deg,#4ade80,#22d3ee 60%,#60a5fa);-webkit-background-clip:text;
background-clip:text;color:transparent}
.lead{font-size:clamp(17px,1.6vw,19.5px);color:var(--ink2);max-width:560px;margin:0 0 28px}
form.lcf{display:flex;gap:8px;background:rgba(15,23,42,.85);border:1px solid var(--line2);border-radius:18px;padding:8px;
max-width:600px;box-shadow:0 0 0 6px rgba(34,197,94,.06),0 24px 60px rgba(0,0,0,.45);transition:box-shadow .2s,border-color .2s}
form.lcf:focus-within{border-color:rgba(74,222,128,.6);box-shadow:0 0 0 6px rgba(34,197,94,.14),0 24px 60px rgba(0,0,0,.45)}
form.lcf input[type=text]{flex:1 1 auto;min-width:0;border:0;outline:0;background:transparent;color:#fff;font:inherit;
font-size:17.5px;padding:14px 14px}
form.lcf input::placeholder{color:#7c8aa0}
form.lcf button{flex:none;border:0;border-radius:12px;background:linear-gradient(180deg,#4ade80,#16a34a);color:#03200f;
font:inherit;font-weight:800;font-size:16.5px;padding:14px 22px;cursor:pointer;box-shadow:inset 0 1px 0 rgba(255,255,255,.35)}
form.lcf button:hover{filter:brightness(1.07)}form.lcf button[disabled]{opacity:.8;cursor:wait}
.meta{display:flex;flex-wrap:wrap;gap:8px 20px;margin-top:18px;color:var(--mut);font-size:14px;font-weight:550}
.meta span{display:inline-flex;align-items:center;gap:7px}.meta svg{width:16px;height:16px;color:var(--acc2)}
.err{background:rgba(244,63,94,.12);border:1px solid rgba(244,63,94,.4);color:#fecdd3;border-radius:14px;padding:12px 16px;
margin:0 0 14px;font-weight:600;max-width:600px}
.src{color:var(--mut);font-size:13px;margin-top:10px}
.bar{height:5px;border-radius:5px;background:var(--line);overflow:hidden;margin-top:12px;display:none;max-width:600px}
.bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,#22c55e,#22d3ee);transition:width 20s cubic-bezier(.2,.6,.4,1)}
.busy .bar{display:block}.busy .bar i{width:94%}
/* phone */
.stage{position:relative;display:flex;justify-content:center;padding:10px 0}
.phone{position:relative;width:258px;border-radius:48px;padding:11px;background:linear-gradient(145deg,#2a3346,#0c111c 40%,#1d2433);
box-shadow:0 0 0 1px rgba(255,255,255,.08),0 50px 100px -20px rgba(0,0,0,.7),0 30px 60px -30px rgba(34,197,94,.35);
animation:float 7s ease-in-out infinite}
.phone:before{content:"";position:absolute;top:22px;left:50%;width:84px;height:24px;margin-left:-42px;border-radius:20px;background:#05070c;z-index:2}
.screen{position:relative;border-radius:38px;overflow:hidden;background:#fff}
.sb{display:flex;justify-content:space-between;align-items:center;height:30px;padding:2px 22px 0 26px;background:#fff;color:#111;font:700 12px/1 var(--sans)}
.sb i{font-style:normal;letter-spacing:1px;font-size:10px}
.screen img{display:block;width:100%;height:auto}
.scan{position:absolute;left:0;right:0;height:90px;top:-90px;background:linear-gradient(180deg,transparent,rgba(34,197,94,.28),transparent);
animation:scan 4.5s cubic-bezier(.5,0,.5,1) infinite 1s}
.call{position:absolute;display:flex;align-items:center;gap:9px;background:rgba(14,22,40,.92);border:1px solid var(--line2);
border-radius:14px;padding:9px 13px;font-size:13.5px;font-weight:650;color:#fff;white-space:nowrap;
box-shadow:0 14px 34px rgba(0,0,0,.45);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px)}
.call b{background:var(--red);color:#fff;border-radius:7px;padding:2px 7px;font-size:12px}
.call.c1{top:15%;right:calc(50% + 146px)}.call.c2{top:41%;left:calc(50% + 146px)}.call.c3{top:86%;right:calc(50% + 146px)}
@media (max-width:1360px){.call.c1,.call.c2,.call.c3{right:calc(50% + 100px);left:auto}.call:after{display:none}.fixc{display:none}}
.call:after{content:"";position:absolute;top:50%;width:22px;height:1px;background:rgba(244,63,94,.7)}
.c1:after,.c3:after{left:100%}.c2:after{right:100%}
.fixc{position:absolute;left:calc(50% + 104px);top:58%;width:236px;background:rgba(14,22,40,.95);border:1px solid rgba(74,222,128,.35);
border-radius:16px;padding:13px 15px;font-size:13px;box-shadow:0 18px 40px rgba(0,0,0,.5)}
.fixc small{display:block;color:var(--acc2);font-weight:800;letter-spacing:.06em;text-transform:uppercase;font-size:11px;margin-bottom:6px}
.fixc code{display:block;font:12.5px/1.5 ui-monospace,Consolas,monospace;color:#e2e8f0}
.fixc code s{color:#fda4af;text-decoration-color:rgba(253,164,175,.6)}.fixc code ins{color:#86efac;text-decoration:none}
.fixc .cr{display:flex;gap:8px;align-items:center;margin-top:8px;color:var(--ink2);font-weight:650}
.fixc .cr i{font-style:normal;border-radius:6px;padding:1px 7px;font-size:12px;font-weight:800}
.fixc .bad{background:rgba(244,63,94,.18);color:#fda4af}.fixc .ok{background:rgba(34,197,94,.18);color:#86efac}
@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-10px)}}
@keyframes scan{0%{top:-90px}60%,100%{top:100%}}
/* strip */
.strip{border-top:1px solid var(--line);border-bottom:1px solid var(--line);background:rgba(255,255,255,.015)}
.strip .w{display:flex;flex-wrap:wrap;align-items:center;justify-content:center;gap:10px 34px;padding:20px 24px;color:var(--mut);
font-size:14px;font-weight:600}
.strip b{color:var(--ink2);font-size:16px;font-weight:750;letter-spacing:-.01em}
/* sections */
section.s{padding:96px 0 0}
.kick{color:var(--acc2);font-weight:800;font-size:13px;letter-spacing:.12em;text-transform:uppercase;margin:0 0 12px}
h2{font-size:clamp(30px,4vw,46px);line-height:1.08;letter-spacing:-.035em;margin:0 0 14px;font-weight:800}
.sl{color:var(--ink2);font-size:18px;max-width:640px;margin:0 0 40px}
.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;counter-reset:st}
.step{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--line);border-radius:var(--r);padding:26px;position:relative}
.step:before{counter-increment:st;content:counter(st);display:grid;place-items:center;width:38px;height:38px;border-radius:12px;
background:rgba(34,197,94,.14);color:var(--acc2);font-weight:850;margin-bottom:18px;border:1px solid rgba(74,222,128,.3)}
.step h3{margin:0 0 6px;font-size:20px;letter-spacing:-.015em}.step p{margin:0;color:var(--mut)}
.vis{height:120px;border-radius:14px;background:#0a1120;border:1px solid var(--line);margin-top:20px;position:relative;overflow:hidden}
.v-url{display:flex;align-items:center;gap:8px;position:absolute;left:16px;right:16px;top:40px;background:#111b30;border:1px solid var(--line2);
border-radius:10px;padding:10px 12px;font:13px ui-monospace,Consolas,monospace;color:var(--ink2)}
.v-url:after{content:"";width:2px;height:16px;background:var(--acc2);animation:blink 1s steps(1) infinite}
.v-ph{position:absolute;left:50%;top:16px;width:64px;height:130px;margin-left:-32px;border-radius:14px;border:3px solid #334155;background:#f8fafc}
.v-ph i{position:absolute;left:8px;right:8px;height:6px;border-radius:3px;background:#cbd5e1}
.v-ph i:nth-child(1){top:14px;background:#8b5cf6}.v-ph i:nth-child(2){top:26px;right:20px}.v-ph i:nth-child(3){top:44px;outline:2px solid var(--red);outline-offset:2px}
.v-ph i:nth-child(4){top:60px;right:14px}
.v-fix{position:absolute;left:16px;right:16px;top:26px;font:12.5px/1.75 ui-monospace,Consolas,monospace;color:var(--ink2)}
.v-fix s{color:#fda4af}.v-fix ins{color:#86efac;text-decoration:none}
@keyframes blink{50%{opacity:0}}
.bento{display:grid;grid-template-columns:repeat(6,1fr);gap:16px}
.tile{grid-column:span 2;background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--line);border-radius:var(--r);
padding:24px;overflow:hidden;position:relative}
.tile.wide{grid-column:span 3}
.tile h3{margin:0 0 6px;font-size:19px;letter-spacing:-.015em}.tile p{margin:0;color:var(--mut);font-size:15px}
.tile .tv{height:110px;margin:0 0 18px;border-radius:14px;background:#0a1120;border:1px solid var(--line);position:relative;overflow:hidden;
display:flex;align-items:center;justify-content:center;gap:14px}
.sw{display:flex;flex-direction:column;align-items:center;gap:6px;font-size:12px;font-weight:700}
.sw span{display:grid;place-items:center;width:84px;height:46px;border-radius:10px;background:#fff;font-weight:700;font-size:14px}
.tap{position:relative;display:grid;place-items:center;width:44px;height:22px;border-radius:6px;background:#8b5cf6;color:#fff;font-size:11px;font-weight:800}
.tap.ok{width:96px;height:44px;border-radius:10px;font-size:13px;background:#16a34a}
.finger{position:absolute;width:48px;height:48px;border-radius:50%;border:2px dashed var(--amber);left:50%;top:50%;margin:-24px 0 0 -24px}
.cut{font-weight:800;font-size:20px;color:#fff;background:#7c3aed;padding:8px 14px;border-radius:10px;width:150px;overflow:hidden;white-space:nowrap;
outline:2px solid var(--red);outline-offset:3px}
.wider{position:relative;width:70px;height:96px;border:3px solid #334155;border-radius:12px;background:#f8fafc}
.wider i{position:absolute;left:6px;top:30px;width:110px;height:16px;border-radius:4px;background:#f59e0b;opacity:.9}
.wider:after{content:"↔";position:absolute;right:-46px;top:28px;color:var(--amber);font-size:20px;font-weight:800}
.serp{width:84%;font-size:12.5px;line-height:1.45}.serp b{color:#93c5fd;font-weight:650;display:block;font-size:14px}
.serp u{text-decoration:none;color:#86efac;font-size:11.5px}.serp em{font-style:normal;color:#fda4af}
.key{font:12.5px ui-monospace,Consolas,monospace;color:var(--ink2);background:#111b30;border:1px solid rgba(244,63,94,.45);border-radius:10px;padding:10px 12px;max-width:86%}
.key em{font-style:normal;color:#fda4af}
/* example */
.ex{display:grid;grid-template-columns:auto 1fr;gap:48px;align-items:center;background:linear-gradient(180deg,var(--card2),var(--card));
border:1px solid var(--line);border-radius:28px;padding:40px}
.ex .phone{width:236px;animation:none}
.exl{list-style:none;margin:0;padding:0;display:grid;gap:14px}
.exl li{display:grid;grid-template-columns:auto 1fr;gap:14px;align-items:start;background:#0a1120;border:1px solid var(--line);border-radius:16px;padding:16px 18px}
.exl li>b{background:var(--red);color:#fff;border-radius:8px;padding:3px 9px;font-size:13px}
.exl strong{display:block;font-size:16.5px}.exl span{color:var(--mut);font-size:14.5px}
.exl code{font:13px ui-monospace,Consolas,monospace;color:#86efac;background:rgba(34,197,94,.1);border-radius:6px;padding:1px 6px}
/* pricing */
.plans{display:grid;grid-template-columns:repeat(var(--n,2),1fr);gap:16px;max-width:var(--pw,820px)}
.plan{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--line);border-radius:var(--r);padding:28px;display:flex;flex-direction:column}
.plan.hot{border-color:rgba(74,222,128,.55);box-shadow:0 0 0 1px rgba(74,222,128,.25),0 30px 70px -30px rgba(34,197,94,.45)}
.plan h3{margin:0;font-size:17px;color:var(--ink2)}.price{font-size:46px;font-weight:850;letter-spacing:-.04em;margin:8px 0 2px}
.price small{font-size:16px;color:var(--mut);font-weight:600;letter-spacing:0}
.plan ul{list-style:none;padding:0;margin:18px 0 24px;display:grid;gap:10px;color:var(--ink2);font-size:15px}
.plan li{display:flex;gap:10px}.plan li:before{content:"";flex:none;width:18px;height:18px;margin-top:2px;border-radius:50%;
background:rgba(34,197,94,.18) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20'%3E%3Cpath d='M5 10.5l3 3 7-7' fill='none' stroke='%234ade80' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") center/14px no-repeat}
.plan .btn{margin-top:auto;display:block;text-align:center;border-radius:12px;padding:13px 18px;font-weight:800;text-decoration:none;
border:1px solid var(--line2);color:#fff;background:rgba(255,255,255,.04)}
.plan.hot .btn{background:linear-gradient(180deg,#4ade80,#16a34a);color:#03200f;border:0}
.tagx{display:inline-block;margin-left:8px;font-size:11.5px;font-weight:800;color:#03200f;background:var(--acc2);border-radius:999px;padding:2px 9px;vertical-align:middle}
.note{color:var(--mut);font-size:13.5px;margin-top:14px}
/* faq */
.faq{max-width:820px;display:grid;gap:10px}
.faq details{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:0 20px}
.faq summary{cursor:pointer;list-style:none;padding:18px 0;font-weight:700;font-size:16.5px;display:flex;justify-content:space-between;gap:16px}
.faq summary::-webkit-details-marker{display:none}.faq summary:after{content:"+";color:var(--acc2);font-size:22px;line-height:1;font-weight:500}
.faq details[open] summary:after{content:"–"}.faq details p{margin:0 0 18px;color:var(--ink2)}
.faq a{color:var(--acc2)}
/* final */
.final{margin:110px 0 0;padding:72px 0;text-align:center;border-top:1px solid var(--line);position:relative;overflow:hidden;
background:radial-gradient(700px 300px at 50% 0,rgba(34,197,94,.16),transparent 70%)}
.final h2{margin-bottom:12px}.final p{color:var(--ink2);margin:0 0 28px;font-size:18px}.final form.lcf{margin:0 auto}
footer{border-top:1px solid var(--line);padding:28px 0 40px;color:var(--mut);font-size:14px}
footer .w{display:flex;flex-wrap:wrap;gap:12px 28px;align-items:center}footer a{color:var(--ink2);text-decoration:none}
footer a:hover{color:#fff}footer .sp{margin-left:auto}
/* responsive */
@media (max-width:920px){.hg{grid-template-columns:1fr;gap:28px}.stage{margin-top:6px}.nav ul{display:none}.nav .pill{margin-left:auto}
.steps{grid-template-columns:1fr}.bento{grid-template-columns:1fr 1fr}.tile,.tile.wide{grid-column:span 1}
.ex{grid-template-columns:1fr;padding:28px;gap:28px}.ex .phone{margin:0 auto}.plans{grid-template-columns:1fr;max-width:520px}}
@media (max-width:600px){.w{padding:0 16px}.hero{padding:40px 0 28px}h1{margin-top:18px}
form.lcf{flex-direction:column;padding:8px}form.lcf button{width:100%}form.lcf input[type=text]{font-size:17px;padding:13px 12px}
.phone{width:236px;border-radius:42px}.screen{border-radius:33px}.call{font-size:12.5px;padding:7px 10px}
.call.c1{top:10%;right:auto;left:0}.call.c2{top:37%;left:auto;right:0}.call.c3{top:82%;right:auto;left:0}.call:after{display:none}
.bento{grid-template-columns:1fr}section.s{padding-top:72px}.ex{padding:20px}.final{margin-top:80px;padding:56px 0}
.strip .w{gap:8px 22px}footer .sp{margin-left:0}}
/* progress page */
.prog-w{max-width:620px;padding-top:56px;padding-bottom:80px}
.prog{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--line2);border-radius:26px;padding:34px 30px;
text-align:center;box-shadow:0 30px 80px -30px rgba(34,197,94,.35)}
.prog b{display:block;font-size:24px;letter-spacing:-.02em;overflow-wrap:anywhere}.prog p{color:var(--mut);margin:14px 0 0}
.prog .bar{display:block;margin:22px auto 0}.prog .bar i{width:94%;animation:fill 20s cubic-bezier(.2,.6,.4,1)}
.prog:before{content:"";display:block;width:58px;height:100px;margin:0 auto 22px;border-radius:14px;border:3px solid #334155;
background:linear-gradient(180deg,transparent 40%,rgba(34,197,94,.35) 50%,transparent 60%) 0 0/100% 200%,#f8fafc;animation:sweep 1.8s linear infinite}
@keyframes fill{from{width:4%}to{width:94%}}@keyframes sweep{from{background-position:0 100%,0 0}to{background-position:0 -100%,0 0}}
@media (prefers-reduced-motion:reduce){.phone,.scan,.v-url:after{animation:none}.scan{display:none}html{scroll-behavior:auto}}
"""

ICON_CHECK = ('<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M5 10.5l3 3 7-7" stroke="currentColor" '
              'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>')
ICON_GH = ('<svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 '
           '5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52'
           '-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82'
           '-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1'
           '.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46'
           '.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>')


NAV_MIN = ('<header class="nav"><div class="w"><a class="logo" href="/" aria-label="Launch Check home"><img src="/favicon.svg" '
           'alt="" width="26" height="26"><span>Launch<i>Check</i></span></a></div></header>')


def scan_form(d, v, url_value, src, keys, first):
    """The scan form. The first one carries the ids the existing script and autostart use."""
    ids = (' id="f"', ' id="b"') if first else ("", "")
    label = esc(d.get("button", "Check my app — free"))
    return (f'<form method="post" action="/check" class="lcf"{ids[0]}>'
            f'<input type="text" name="url" inputmode="url" autocomplete="url" spellcheck="false" autocapitalize="off" required '
            f'placeholder="https://your-app.com" value="{esc(url_value) if first else ""}" aria-label="Your site\'s address" maxlength="2000">'
            f'<input type="hidden" name="v" value="{esc(v)}"><input type="hidden" name="src" value="{esc(src)}">'
            + ("" if keys else '<input type="hidden" name="k" value="0">')
            + f'<button type="submit"{ids[1]}>{label}</button></form>')


def phone(extra_class=""):
    return (f'<div class="phone {extra_class}"><div class="screen"><div class="sb" aria-hidden="true"><span>9:41</span><i>●●● ▮</i></div><img src="/example-phone.jpg" width="375" height="1080" '
            'alt="An AI-built landing page on a 375 px phone with three problems boxed: #1 text hidden under a badge, '
            '#2 a cut-off button, #3 grey text too faint to read" fetchpriority="high"><div class="scan"></div></div></div>')


def render(*, d, v, url_value, error, src, keys, github, price, price_was, dfy_price, dfy_on, stripe_on=False, og_maker=True):
    err = f'<div class="err" role="alert">{esc(error)}</div>' if error else ""
    source = f'<div class="src">Source: {esc(d["source"])}</div>' if d.get("source") else ""
    lead = d.get("sub") or ("We open your page in a real phone browser, box what's broken, and give you the exact CSS fix "
                            "for each problem. Under a minute, free, no signup.")
    form1 = scan_form(d, v, url_value, src, keys, True)
    form2 = scan_form(d, v, "", src, keys, False)
    meta = "".join(f"<span>{ICON_CHECK}{t}</span>" for t in ("Free", "No signup", "Under a minute", "Open source"))
    dfy_plan = (f"""<div class="plan"><h3>Done for you</h3><div class="price">{esc(dfy_price)}<small> per site</small></div>
<ul><li>The founder fixes the boxed problems for you</li><li>Within 24 h, by email or a pull request</li>
<li>You pay only after a clean re-scan</li></ul><a class="btn" href="#top">Scan first</a></div>""" if dfy_on else "")
    n_plans = 3 if dfy_on else 2
    was = f' <s style="color:var(--mut);font-size:20px;font-weight:600">{esc(price_was)}</s>' if price_was else ""
    return f"""<header class="nav"><div class="w"><a class="logo" href="/" aria-label="Launch Check home">
<img src="/favicon.svg" alt="" width="26" height="26"><span>Launch<i>Check</i></span></a>
<nav aria-label="Sections"><ul><li><a href="#how">How it works</a></li><li><a href="#checks">What it checks</a></li>
<li><a href="#pricing">Pricing</a></li><li><a href="#faq">FAQ</a></li></ul></nav>
<a class="pill" href="{esc(github)}" rel="noopener">{ICON_GH}Open source</a></div></header>
<main id="top">
<section class="hero"><div class="w hg"><div>
<span class="eyebrow"><b>Free</b>For Lovable, Bolt, v0 &amp; Cursor sites</span>
<h1>{headline(d["h1"])}</h1>
<p class="lead">{esc(lead)}</p>
{err}{form1}
<div class="bar" aria-hidden="true"><i></i></div>
<div class="meta">{meta}</div>{source}
</div>
<div class="stage" aria-label="Example result">{phone()}
<div class="call c1" aria-hidden="true"><b>#1</b>Text hidden under a badge</div>
<div class="call c2" aria-hidden="true"><b>#2</b>Button text cut off</div>
<div class="call c3" aria-hidden="true"><b>#3</b>Grey text too faint</div>
<div class="fixc" aria-hidden="true"><small>Exact fix for #3</small><code>.card p {{<br>&nbsp; color: <s>#9ca3af</s> <ins>#6b7280</ins>;<br>}}</code>
<div class="cr">Contrast <i class="bad">2.5:1</i> → <i class="ok">4.8:1</i></div></div>
</div></div></section>

<div class="strip"><div class="w"><span>Made for sites built with</span><b>Lovable</b><b>Bolt</b><b>v0</b><b>Replit</b><b>Cursor</b><b>Claude Code</b></div></div>

<section class="s" id="how"><div class="w">
<p class="kick">How it works</p><h2>Paste a link. See what your visitors see.</h2>
<p class="sl">Most visitors open your site on a phone. Your AI builder showed you a desktop preview. Launch Check shows you the phone.</p>
<div class="steps">
<div class="step"><h3>Paste your URL</h3><p>Any public page. No signup, no code to install.</p><div class="vis"><div class="v-url">https://your-app.com</div></div></div>
<div class="step"><h3>We open it on a phone</h3><p>A real phone browser at 375 px renders your page and measures every element.</p><div class="vis"><div class="v-ph"><i></i><i></i><i></i><i></i></div></div></div>
<div class="step"><h3>Get the exact fixes</h3><p>Each problem boxed on the screenshot, with the CSS change that fixes it.</p><div class="vis"><div class="v-fix">.hero .cta {{<br>&nbsp; white-space: <s>nowrap</s> <ins>normal</ins>;<br>}}</div></div></div>
</div></div></section>

<section class="s" id="checks"><div class="w">
<p class="kick">What it checks</p><h2>The problems AI builders leave behind.</h2>
<p class="sl">Measured on the rendered page, not guessed from the code. Every finding comes with its fix.</p>
<div class="bento">
<div class="tile"><div class="tv"><div class="sw"><span style="color:#9ca3af">Faint</span><em style="font-style:normal;color:#fda4af">2.5:1</em></div>
<div class="sw"><span style="color:#6b7280">Readable</span><em style="font-style:normal;color:#86efac">4.8:1</em></div></div>
<h3>Text too faint to read</h3><p>Contrast below the WCAG minimum, with the closest colour that passes.</p></div>
<div class="tile"><div class="tv"><div class="tap">Buy<span class="finger"></span></div><span style="color:var(--mut);font-weight:800">→</span><div class="tap ok">Buy</div></div>
<h3>Buttons too small to tap</h3><p>Targets smaller than a fingertip, or packed too close together.</p></div>
<div class="tile"><div class="tv"><div class="cut">Start your free trial today</div></div>
<h3>Cut-off and overlapping text</h3><p>Buttons that clip their label, badges that sit on top of headlines.</p></div>
<div class="tile wide"><div class="tv"><div class="wider"><i></i></div></div>
<h3>Wider than the screen</h3><p>Fixed widths that make the whole page scroll sideways on a phone.</p></div>
<div class="tile wide"><div class="tv"><div class="serp"><u>your-app.com</u><b>Untitled</b><em>No description: Google and ChatGPT guess what you do.</em></div></div>
<h3>What Google and ChatGPT can read</h3><p>Title, description, share image and the basics search and AI answers rely on.</p></div>
<div class="tile wide"><div class="tv"><div class="key">const supabase = createClient(url,<br>&nbsp; "<em>eyJhbGciOi… service_role</em>")</div></div>
<h3>Secret keys in your code</h3><p>Stripe, OpenAI, Gemini, Supabase and Firebase keys shipped to every visitor's browser.</p></div>
<div class="tile wide"><div class="tv"><div class="key" style="border-color:rgba(96,165,250,.45)">content-security-policy: <em style="color:#93c5fd">missing</em><br>strict-transport-security: <em style="color:#93c5fd">missing</em></div></div>
<h3>Security headers</h3><p>The headers that stop clickjacking and injected scripts. A quick check, not a penetration test.</p></div>
</div></div></section>

<section class="s" id="example"><div class="w">
<p class="kick">Example result</p><h2>A real report, on a real AI-built page.</h2>
<p class="sl">This page looked fine in the builder's preview. On a phone, three things were broken.</p>
<div class="ex">{phone()}
<ol class="exl">
<li><b>#1</b><div><strong>Text hidden under a badge</strong><span>The "NEW: GPT agents" badge sits on top of the headline.</span></div></li>
<li><b>#2</b><div><strong>Button text cut off</strong><span>"Start your free trial today" is clipped. Fix: <code>white-space: normal</code></span></div></li>
<li><b>#3</b><div><strong>Grey text too faint to read</strong><span>Contrast 2.5:1 on white. Fix: <code>color: #6b7280</code> (4.8:1)</span></div></li>
</ol></div></div></section>

<section class="s" id="pricing"><div class="w">
<p class="kick">Pricing</p><h2>Free to check. Cheap to fix.</h2>
<p class="sl">The scan is free forever. Pay only when you want every fix, ready to paste.</p>
<div class="plans" style="--n:{n_plans};--pw:{1160 if dfy_on else 820}px">
<div class="plan"><h3>Free scan</h3><div class="price">$0</div>
<ul><li>Your page rendered on a real phone</li><li>Top 3 problems boxed, with their fixes</li><li>Secret keys, Google &amp; ChatGPT, headers</li>
<li>A link to your result for 14 days</li></ul><a class="btn" href="#top">Check my site</a></div>
<div class="plan hot"><h3>Fix Pack<span class="tagx">Most useful</span></h3><div class="price">{esc(price)}{was}<small> one-off</small></div>
<ul><li>Every problem boxed on phone, tablet and desktop</li><li>The exact fix for each, plus the files</li>
<li>A prompt you paste into Lovable, Bolt or Cursor</li><li>Refund if it doesn't help</li></ul><a class="btn" href="#top">Scan, then get the pack</a>
<a href="/fix/demo" style="display:block;text-align:center;margin-top:12px;color:var(--ink2);font-size:14px">See a sample Fix Pack →</a></div>
{dfy_plan}</div>
<p class="note">Prices in US dollars.{" Secure checkout by Stripe." if stripe_on else ""}</p>
</div></section>

<section class="s" id="faq"><div class="w">
<p class="kick">FAQ</p><h2>Questions</h2>
<div class="faq">
<details><summary>What does Launch Check read?</summary><p>Only what any visitor's browser downloads from the URL you give it: the page, its scripts and styles, and its response headers. It never logs in, never submits forms and never tries to break in.</p></details>
<details><summary>Is this a security audit?</summary><p>No. It finds keys shipped in your public code and missing security headers. It is not a penetration test and does not replace one.</p></details>
<details><summary>Do you keep my site or my results?</summary><p>Your result stays available for 14 days so the link keeps working, then it is gone. Nothing is written to disk and the page's content is never stored.</p></details>
<details><summary>Which sites does it work on?</summary><p>Any public web page, whatever built it: Lovable, Bolt, v0, Replit, Cursor, Claude Code, Webflow, WordPress or hand-written HTML.</p></details>
<details><summary>What is in the Fix Pack?</summary><p>Every problem the scan found, boxed on phone, tablet and desktop screenshots, with the exact change for each and a prompt you can paste into your AI builder. <a href="/fix/demo">See a sample</a>.</p></details>
<details><summary>Can I run it myself?</summary><p>Yes. The checker is open source and runs as a skill in Claude Code, Cursor, Codex and other agents. <a href="{esc(github)}" rel="noopener">Get it on GitHub</a>.</p></details>
</div></div></section>

<section class="final"><div class="w"><h2>See your site the way your visitors do.</h2><p>Paste your link. The result is ready in under a minute.</p>{form2}</div></section>
</main>
<footer><div class="w"><a class="logo" href="/"><img src="/favicon.svg" alt="" width="22" height="22"><span>Launch<i>Check</i></span></a>
<a href="{esc(github)}" rel="noopener">Open source</a>{'<a href="/og">OG image maker</a>' if og_maker else ''}<a href="/fix/demo">Sample Fix Pack</a>
<span class="sp">Made by <a href="https://ces107.github.io/">César Pereiro</a></span></div></footer>"""


def headline(h1):
    """Accent the clause after the first comma or full stop: "See your site on a phone, <em>problems marked.</em> Free."."""
    text = esc(h1)
    m = re.search(r"([,.?]) (.+?[.?!])(\s|$)", text)
    if not m:
        return text
    return f"{text[:m.start(2)]}<em>{m.group(2)}</em>{text[m.end(2):]}"
