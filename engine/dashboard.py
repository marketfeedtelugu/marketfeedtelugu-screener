"""The screener dashboard: one self-contained HTML file per scan (no external files, works offline).
Everything is also written as plain HTML + SVG (engine/dashboard_static.py), so the page shows its funnel,
charts and backtest even where scripts never run (the Claude app's file and browser panes for a file
outside the project, Quick Look, chat previews). Where scripts run, they swap in the interactive version.

Tabs: Screener (funnel, trade plans, near-misses, a chart for every stock with entry / stop / target,
resistance, the strategy's own moving averages and every past signal with its outcome), Backtest,
Forward test, Strategy (the card in plain words).
"""

import html
import json
import math
import re

from . import dashboard_static


def clean(x):
    """JSON-safe: nan and inf become null."""
    if isinstance(x, float):
        return None if (math.isnan(x) or math.isinf(x)) else round(x, 4)
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    return x


def write(path, scan, card_words, card_raw, backtest=None, forward=None):
    payload = {"scan": scan, "card_words": card_words, "card": card_raw, "backtest": slim_backtest(backtest),
               "forward": forward}
    blob = json.dumps(clean(payload), separators=(",", ":"), default=str).replace("</", "<\\/")
    title = "%s screener" % scan.get("title", "Strategy")
    st = dashboard_static.render(clean(payload))
    fill = {"__TITLE__": html.escape(title), "__DATA__": blob}
    fill.update({"__STATIC_%s__" % k.upper(): v for k, v in st.items()})
    # one pass, so text inside the data or the static HTML is never read as a placeholder
    doc = re.sub(r"__(?:TITLE|DATA|STATIC_[A-Z0-9]+)__", lambda m: fill.get(m.group(0), m.group(0)), TEMPLATE)
    with open(path, "w") as fh:
        fh.write(doc)
    return path


def slim_backtest(res):
    if not res:
        return None
    keep = dict(res)
    return keep


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#f6f5f1;--panel:#fff;--ink:#1c1c1a;--muted:#6a6963;--line:#e3e1d9;--soft:#efeee8;--accent:#1f5fbf;
--up:#12804c;--down:#c53a2e;--warn:#b07415;--c1:#1f5fbf;--c2:#b7509a;--c3:#2a9d8f;--c4:#e07b18;--c5:#7a5cc4;
--candle-up:#12804c;--candle-down:#c53a2e;--grid:#ecebe4}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#111213;--panel:#1a1b1d;--ink:#ecebe6;
--muted:#a09f98;--line:#2c2d30;--soft:#222327;--accent:#7aa7f0;--up:#3fbf7f;--down:#ef6b5e;--warn:#e0b050;
--c1:#7aa7f0;--c2:#e08ccf;--c3:#5fd0c0;--c4:#f2a65a;--c5:#b39cf0;--candle-up:#3fbf7f;--candle-down:#ef6b5e;--grid:#232427}}
:root[data-theme="dark"]{--bg:#111213;--panel:#1a1b1d;--ink:#ecebe6;--muted:#a09f98;--line:#2c2d30;--soft:#222327;
--accent:#7aa7f0;--up:#3fbf7f;--down:#ef6b5e;--warn:#e0b050;--c1:#7aa7f0;--c2:#e08ccf;--c3:#5fd0c0;--c4:#f2a65a;
--c5:#b39cf0;--candle-up:#3fbf7f;--candle-down:#ef6b5e;--grid:#232427}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--ink);
font:14.5px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:22px 16px 60px}
header h1{font-size:24px;margin:0}header .sub{color:var(--muted);font-size:13.5px;margin-top:2px}
.tabs{display:flex;gap:4px;margin:18px 0 6px;border-bottom:1px solid var(--line);overflow-x:auto}
.tabs button{background:none;border:0;border-bottom:2px solid transparent;color:var(--muted);padding:9px 14px;
font:inherit;font-weight:600;cursor:pointer;white-space:nowrap}.tabs button.on{color:var(--ink);border-color:var(--accent)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;margin:12px 0}
h2{font-size:17px;margin:0 0 10px}h3{font-size:15px;margin:0 0 6px}
.muted{color:var(--muted)}.small{font-size:12.5px}.pos{color:var(--up)}.neg{color:var(--down)}
.badge{display:inline-block;padding:1px 9px;border-radius:999px;font-size:11.5px;font-weight:700;letter-spacing:.03em}
.b-PASS,.b-bullish,.b-positive{background:color-mix(in srgb,var(--up) 18%,transparent);color:var(--up)}
.b-REJECT,.b-bearish,.b-negative{background:color-mix(in srgb,var(--down) 18%,transparent);color:var(--down)}
.b-REVIEW,.b-neutral,.b-mixed,.b-insufficient,.b-pending{background:color-mix(in srgb,var(--warn) 20%,transparent);color:var(--warn)}
.b-new{background:color-mix(in srgb,var(--accent) 16%,transparent);color:var(--accent)}
.funnel .row{display:grid;grid-template-columns:64px 1fr;gap:12px;align-items:start;margin:7px 0}
.funnel .n{font-size:20px;font-weight:700;text-align:right;font-variant-numeric:tabular-nums}
.funnel .bar{background:var(--accent);color:var(--panel);border-radius:6px;padding:6px 10px;font-weight:600;font-size:13.5px;
min-width:fit-content;white-space:normal}
.funnel .why{color:var(--muted);font-size:12px;margin-top:2px}
.wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{color:var(--muted);font-weight:600;cursor:pointer;user-select:none}th.l,td.l{text-align:left}
tbody tr{cursor:pointer}tbody tr:hover{background:var(--soft)}tbody tr.sel{background:color-mix(in srgb,var(--accent) 12%,transparent)}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}
.kpi div{background:var(--soft);border-radius:10px;padding:10px 12px}.kpi b{display:block;font-size:19px}.kpi span{color:var(--muted);font-size:12px}
.verdict{border-left:4px solid var(--accent);background:var(--soft);padding:10px 14px;border-radius:8px}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:8px}
input[type=search]{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:6px 10px;font:inherit;min-width:180px}
label.tg{display:flex;gap:6px;align-items:center;color:var(--muted);font-size:13px}
.chartbox{position:relative}canvas{width:100%;display:block;touch-action:pan-y}
.tip{position:absolute;pointer-events:none;background:var(--panel);border:1px solid var(--line);border-radius:8px;
padding:6px 9px;font-size:12px;display:none;white-space:nowrap;box-shadow:0 4px 14px rgba(0,0,0,.12)}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--muted);margin-top:6px}
.legend i{display:inline-block;width:14px;height:3px;border-radius:2px;vertical-align:middle;margin-right:5px}
.checks td{text-align:left;white-space:normal;vertical-align:top}.checks td:first-child{width:76px}.checks td:nth-child(2){width:120px;font-weight:600}
.dots span{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:3px;background:var(--line)}
.dots span.y{background:var(--up)}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}ul{padding-left:20px;margin:6px 0}li{margin:3px 0}
pre{white-space:pre-wrap;font:12.5px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--soft);padding:12px;border-radius:8px;margin:0;overflow-x:auto}
.grid2{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);gap:12px}
@media (max-width:860px){.grid2{grid-template-columns:1fr}}
.hide{display:none}footer{margin-top:36px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:12px}
.theme{float:right;background:none;border:1px solid var(--line);color:var(--muted);border-radius:8px;padding:4px 10px;cursor:pointer;font:inherit;font-size:12.5px}
.tabs a{color:var(--muted);padding:9px 14px;font-weight:600;white-space:nowrap}
h2.sec{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:30px 0 4px}
.grid-charts{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,440px),1fr));gap:0 12px}
svg.sv{width:100%;height:auto;aspect-ratio:900/380;display:block}.sv-bg{fill:var(--panel)}.sv-grid{stroke:var(--grid);stroke-width:1}
.sv-t,.sv-lt{font:11px -apple-system,BlinkMacSystemFont,sans-serif;fill:var(--muted)}.sv-lt{fill:var(--panel);font-weight:600}
.sv-up{fill:var(--candle-up)}.sv-dn{fill:var(--candle-down)}.sv-wick-up{stroke:var(--candle-up);fill:none}.sv-wick-dn{stroke:var(--candle-down);fill:none}
.sv-vol-up{fill:var(--candle-up);opacity:.35}.sv-vol-dn{fill:var(--candle-down);opacity:.35}
.sv-ov{fill:none;stroke-width:1.5}.sv-c1{stroke:var(--c1)}.sv-c2{stroke:var(--c2)}.sv-c3{stroke:var(--c3)}.sv-c4{stroke:var(--c4)}.sv-c5{stroke:var(--c5)}
.sv-risk{fill:var(--down);opacity:.10}.sv-reward{fill:var(--up);opacity:.10}.sv-band{fill:var(--accent);opacity:.25}
.sv-l-ent,.sv-l-stp,.sv-l-tgt,.sv-l-res{stroke-width:1.5}.sv-l-ent{stroke:var(--accent)}.sv-l-stp{stroke:var(--down)}.sv-l-tgt{stroke:var(--up)}.sv-l-res{stroke:var(--warn)}
.sv-f-ent{fill:var(--accent)}.sv-f-stp{fill:var(--down)}.sv-f-tgt{fill:var(--up)}.sv-f-res{fill:var(--warn)}.sv-dash{stroke-dasharray:4 3}
.sv-sig{fill:var(--accent)}.sv-win{stroke:var(--up);stroke-width:1.5}.sv-loss{stroke:var(--down);stroke-width:1.5}.sv-wind{fill:var(--up)}.sv-lossd{fill:var(--down)}
.sv-eq{fill:none;stroke-width:2}.eq-s{stroke:var(--accent)}.eq-b{stroke:var(--muted)}
.lg-c1{background:var(--c1)}.lg-c2{background:var(--c2)}.lg-c3{background:var(--c3)}.lg-c4{background:var(--c4)}.lg-c5{background:var(--c5)}
.lg-ent,.lg-eq-s{background:var(--accent)}.lg-stp{background:var(--down)}.lg-tgt{background:var(--up)}.lg-res{background:var(--warn)}.lg-eq-b{background:var(--muted)}
</style></head><body><main>
<header><button class="theme" id="theme">Theme</button><h1 id="h1">__STATIC_H1__</h1><div class="sub" id="sub">__STATIC_SUB__</div></header>
<nav class="tabs" id="tabs">__STATIC_NAV__</nav>
<section id="tab-screener">__STATIC_SCREENER__</section><section id="tab-backtest">__STATIC_BACKTEST__</section>
<section id="tab-forward">__STATIC_FORWARD__</section><section id="tab-strategy">__STATIC_STRATEGY__</section>
<footer>Education and tooling, not investment advice. "Matched the rules" is not a recommendation to buy; the decision
and the risk are yours. Data: NSE index lists, Yahoo Finance bars (free, unofficial, can lag) or Kite imports, NSE
surveillance and board-meeting lists. Backtests use today's index list for the past (survivorship bias) and assume
the stop is hit first when a bar touches both. Past results do not predict future results. Built with the marketfeed
screener skill for Claude Code.</footer>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
(function(){
const D=JSON.parse(document.getElementById('data').textContent);
const S=D.scan, $=s=>document.querySelector(s);
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function inr(x,d){if(x==null||isNaN(x))return 'n/a';d=d||0;const neg=x<0;x=Math.abs(x);let s=x.toFixed(d);let [w,f]=s.split('.');
 if(w.length>3){let h=w.slice(0,-3),t=w.slice(-3);h=h.replace(/\B(?=(\d{2})+(?!\d))/g,',');w=h+','+t}return (neg?'-':'')+w+(f?'.'+f:'')}
const num=(x,d,s)=>x==null||isNaN(x)?'n/a':((s&&x>0?'+':'')+x.toFixed(d==null?1:d));
const cls=x=>x==null?'':(x>0?'pos':x<0?'neg':'');
try{const t=localStorage.getItem('mfs-theme');if(t)document.documentElement.dataset.theme=t}catch(e){}
$('#theme').onclick=()=>{const cur=document.documentElement.dataset.theme||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
 const nx=cur==='dark'?'light':'dark';document.documentElement.dataset.theme=nx;try{localStorage.setItem('mfs-theme',nx)}catch(e){};redraw()};
$('#h1').textContent=S.title;
$('#sub').innerHTML=esc(S.universe_label)+' &middot; '+esc(S.timeframe)+' chart &middot; last bar '+esc(S.asof)+
 (S.timeframe!=='1d'&&S.timeframe!=='1w'?' &middot; daily filters as of '+esc(S.daily_asof):'')+' &middot; generated '+esc(S.generated.replace('T',' '));
const TABS=[['screener','Screener'],['backtest','Backtest'],['forward','Forward test'],['strategy','Strategy']];
$('#tabs').innerHTML=TABS.map(([k,l],i)=>'<button data-t="'+k+'" class="'+(i?'':'on')+'">'+l+'</button>').join('');
document.querySelectorAll('#tabs button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#tabs button').forEach(x=>x.classList.toggle('on',x===b));
 TABS.forEach(([k])=>$('#tab-'+k).classList.toggle('hide',k!==b.dataset.t));redraw()});
TABS.forEach(([k],i)=>$('#tab-'+k).classList.toggle('hide',i>0));
// each tab draws on its own: if one fails, the others still draw and the failed one keeps its static version
const safe=(name,f)=>{try{f()}catch(err){console.error('dashboard: '+name+' tab kept its static version:',err)}};

// ---------------------------------------------------------------- chart
const charts=[];
function redraw(){charts.forEach(c=>c.draw())}
window.addEventListener('resize',redraw);
function Chart(box,ch){
 const cv=document.createElement('canvas'),tip=document.createElement('div');tip.className='tip';
 box.innerHTML='';box.className='chartbox';box.appendChild(cv);box.appendChild(tip);
 const leg=document.createElement('div');leg.className='legend';box.appendChild(leg);
 const cols=['--c1','--c2','--c3','--c4','--c5'];
 let L=[];
 (ch.overlays||[]).forEach((o,k)=>L.push('<span><i style="background:'+css(cols[k%5])+'"></i>'+esc(o.label)+'</span>'));
 if(ch.plan){L.push('<span><i style="background:'+css('--accent')+'"></i>entry</span>','<span><i style="background:'+css('--down')+'"></i>stop</span>');
  if(ch.plan.target)L.push('<span><i style="background:'+css('--up')+'"></i>target</span>');if(ch.plan.resistance)L.push('<span><i style="background:'+css('--warn')+'"></i>resistance</span>')}
 if(ch.signals&&ch.signals.length)L.push('<span>&#9650; past signals ('+ch.signals.length+'): green = won, red = lost</span>');
 leg.innerHTML=L.join('');
 const self={draw(){
  if(box.offsetParent===null)return;
  const W=box.clientWidth,H=Math.max(260,Math.min(430,W*0.55)),dpr=window.devicePixelRatio||1;
  cv.width=W*dpr;cv.height=H*dpr;cv.style.height=H+'px';const g=cv.getContext('2d');g.setTransform(dpr,0,0,dpr,0,0);
  const n=ch.c.length,padR=64,padT=8,volH=Math.round(H*0.16),padB=20,ph=H-volH-padT-padB-4;
  let lo=Math.min(...ch.l),hi=Math.max(...ch.h);
  const P=ch.plan;if(P){[P.entry,P.stop,P.target].forEach(v=>{if(v!=null){lo=Math.min(lo,v);hi=Math.max(hi,v)}});
   if(P.resistance&&P.resistance<hi*1.15)hi=Math.max(hi,P.resistance)}
  const span=(hi-lo)||1;lo-=span*0.03;hi+=span*0.03;
  const cw=(W-padR)/n,y=v=>padT+(hi-v)/(hi-lo)*ph,x=i=>i*cw+cw/2;
  g.fillStyle=css('--panel');g.fillRect(0,0,W,H);
  g.strokeStyle=css('--grid');g.lineWidth=1;g.fillStyle=css('--muted');g.font='11px -apple-system,sans-serif';g.textAlign='left';
  for(let k=0;k<=4;k++){const v=lo+(hi-lo)*k/4,yy=y(v);g.beginPath();g.moveTo(0,yy);g.lineTo(W-padR,yy);g.stroke();g.fillText(inr(v,v<100?2:0),W-padR+4,yy+4)}
  const vmax=Math.max(...ch.v)||1;
  for(let i=0;i<n;i++){const up=ch.c[i]>=ch.o[i];g.fillStyle=css(up?'--candle-up':'--candle-down');g.strokeStyle=g.fillStyle;
   const xx=x(i),bw=Math.max(1,cw*0.65);g.beginPath();g.moveTo(xx,y(ch.h[i]));g.lineTo(xx,y(ch.l[i]));g.stroke();
   const t=y(Math.max(ch.o[i],ch.c[i])),b=y(Math.min(ch.o[i],ch.c[i]));g.fillRect(xx-bw/2,t,bw,Math.max(1,b-t));
   g.globalAlpha=0.35;const vh=ch.v[i]/vmax*volH;g.fillRect(xx-bw/2,H-padB-vh,bw,vh);g.globalAlpha=1}
  (ch.overlays||[]).forEach((o,k)=>{g.strokeStyle=css(cols[k%5]);g.lineWidth=1.5;g.beginPath();let on=false;
   o.values.forEach((v,i)=>{if(v==null){on=false;return}if(!on){g.moveTo(x(i),y(v));on=true}else g.lineTo(x(i),y(v))});g.stroke()});
  function hline(v,c,label,dash){if(v==null)return;const yy=y(v);g.strokeStyle=css(c);g.lineWidth=1.5;g.setLineDash(dash||[]);
   const x0=P&&P.signal_i!=null?x(Math.max(0,Math.min(P.signal_i,n-1)-Math.round(n*0.15))):0;g.beginPath();g.moveTo(x0,yy);g.lineTo(W-padR,yy);g.stroke();g.setLineDash([]);
   g.fillStyle=css(c);g.fillRect(W-padR,yy-8,padR,16);g.fillStyle=css('--panel');g.fillText(label+' '+inr(v,2),W-padR+3,yy+4)}
  if(P&&P.entry!=null&&P.stop!=null){const x0=P.signal_i!=null?x(Math.max(0,Math.min(P.signal_i,n-1)-Math.round(n*0.15))):0;
   g.globalAlpha=0.10;g.fillStyle=css('--down');g.fillRect(x0,y(P.entry),W-padR-x0,y(P.stop)-y(P.entry));
   if(P.target!=null){g.fillStyle=css('--up');g.fillRect(x0,y(P.target),W-padR-x0,y(P.entry)-y(P.target))}g.globalAlpha=1}
  if(P){hline(P.resistance,'--warn','R',[4,3]);hline(P.target,'--up','T');hline(P.stop,'--down','S');hline(P.entry,'--accent','E')}
  (ch.signals||[]).forEach(s=>{const xs=x(s.s);g.fillStyle=css('--accent');g.beginPath();const yy=y(ch.l[s.s])+10;
   g.moveTo(xs,yy-6);g.lineTo(xs-5,yy+3);g.lineTo(xs+5,yy+3);g.closePath();g.fill();
   if(s.e!=null){const c=css(s.r>0?'--up':'--down');g.strokeStyle=c;g.fillStyle=c;g.lineWidth=1.5;g.setLineDash([3,3]);
    g.beginPath();g.moveTo(x(s.e),y(s.ep));g.lineTo(x(s.x),y(s.xp));g.stroke();g.setLineDash([]);
    g.beginPath();g.arc(x(s.e),y(s.ep),3,0,7);g.fill();g.beginPath();g.arc(x(s.x),y(s.xp),3,0,7);g.fill()}});
  if(P&&P.signal_i!=null){const xs=x(P.signal_i);g.strokeStyle=css('--accent');g.globalAlpha=0.25;g.lineWidth=cw;g.beginPath();g.moveTo(xs,padT);g.lineTo(xs,padT+ph);g.stroke();g.globalAlpha=1}
  g.fillStyle=css('--muted');g.textAlign='center';const step=Math.max(1,Math.ceil(n/Math.max(2,Math.floor((W-padR)/95))));
  for(let i=0;i<n;i+=step){g.fillText(lab(ch.t[i]),x(i),H-5)}
  self._m={x,cw,n,y}
 }};
 function lab(t){return t.length>10?t.slice(5,10)+' '+t.slice(11):t.slice(2)}
 cv.addEventListener('mousemove',ev=>{if(!self._m)return;const r=cv.getBoundingClientRect(),i=Math.floor((ev.clientX-r.left)/self._m.cw);
  if(i<0||i>=ch.c.length){tip.style.display='none';return}
  let s='<b>'+esc(ch.t[i])+'</b><br>O '+inr(ch.o[i],2)+' H '+inr(ch.h[i],2)+'<br>L '+inr(ch.l[i],2)+' C '+inr(ch.c[i],2)+'<br>Vol '+inr(ch.v[i]);
  (ch.overlays||[]).forEach(o=>{if(o.values[i]!=null)s+='<br>'+esc(o.label)+' '+inr(o.values[i],2)});
  (ch.signals||[]).forEach(z=>{if(z.s===i)s+='<br><b>signal</b>'+(z.e!=null?': in '+inr(z.ep,2)+', out '+inr(z.xp,2)+' ('+esc(z.why)+') '+num(z.r,2,1)+'R':' (not filled: '+esc(z.nofill)+')')});
  tip.innerHTML=s;tip.style.display='block';const tx=ev.clientX-r.left+14;tip.style.left=(tx+180>r.width?tx-200:tx)+'px';tip.style.top=(ev.clientY-r.top+10)+'px'});
 cv.addEventListener('mouseleave',()=>tip.style.display='none');
 charts.push(self);self.draw();return self}

// ---------------------------------------------------------------- screener tab
safe('screener',()=>{
function sortable(tbl,rows,cols,onClick,selKey){
 let key=null,dir=1;const tb=tbl.querySelector('tbody');
 function render(){let r=rows.slice();if(key!=null){const c=cols[key];r.sort((a,b)=>{const va=c.v(a),vb=c.v(b);
   if(va==null)return 1;if(vb==null)return -1;return (va>vb?1:va<vb?-1:0)*dir})}
  tb.innerHTML=r.map(x=>'<tr data-k="'+esc(x[selKey])+'">'+cols.map(c=>'<td class="'+(c.l?'l ':'')+(c.c?c.c(x):'')+'">'+c.f(x)+'</td>').join('')+'</tr>').join('');
  tb.querySelectorAll('tr').forEach(tr=>tr.onclick=()=>{tb.querySelectorAll('tr').forEach(z=>z.classList.remove('sel'));tr.classList.add('sel');onClick(tr.dataset.k)})}
 tbl.querySelector('thead').innerHTML='<tr>'+cols.map((c,i)=>'<th class="'+(c.l?'l':'')+'" data-i="'+i+'">'+c.h+'</th>').join('')+'</tr>';
 tbl.querySelectorAll('th').forEach(th=>th.onclick=()=>{const i=+th.dataset.i;dir=key===i?-dir:(cols[i].l?1:-1);key=i;render()});
 render();return {set(r){rows=r;render()}}}

const NW=S.news||null;
const lnk=(u,t)=>/^https?:\/\//.test(u||'')?'<a href="'+esc(u)+'" target="_blank" rel="noopener noreferrer">'+esc(t)+'</a>':esc(t);
function newsHtml(sym){
 if(!NW)return '<p class="small muted" style="margin-top:10px">News not checked in this run (scan without --no-news, or run the news command).</p>';
 const sn=(NW.stocks||{})[sym],note=(NW.notes||{})[sym];
 if(!sn&&!note)return '<p class="small muted" style="margin-top:10px">No news fetched for this stock (only PASS / REVIEW plans are checked).</p>';
 let h='<h3 style="margin-top:14px">News <span class="muted small">'+(NW.live?'live':'replay')+', up to '+esc(NW.until)+'</span></h3>';
 if(note)h+='<div class="verdict small" style="margin:6px 0">Your catalyst note: '+esc(note.text)+'</div>';
 if(sn){const lv={red:'neg',check:'',info:'muted'};
  if(sn.flags&&sn.flags.length)h+='<ul class="small">'+sn.flags.slice(0,6).map(f=>'<li class="'+(lv[f.level]||'')+'"><b>'+esc(f.level.toUpperCase())+'</b> '+esc(f.text)+'</li>').join('')+'</ul>';
  const fl=(sn.filings||[]).slice(0,8);
  h+='<div class="small"><b>NSE filings, last '+NW.filing_days+' days ('+(sn.filings||[]).length+')</b>'+(fl.length?'<ul>'+fl.map(f=>'<li>'+(f.important?'<b>!</b> ':'')+esc((f.time_ist||'').slice(0,10))+' '+lnk(f.link,f.subject)+(f.tags&&f.tags.length?' <span class="muted">['+esc(f.tags.join(', '))+']</span>':'')+'</li>').join('')+'</ul>':'<p class="muted">none'+(sn.filings_ok?'':' (NSE did not answer)')+'</p>')+'</div>';
  const hd=(sn.headlines||[]).slice(0,6);
  h+='<div class="small"><b>Headlines, last '+NW.headline_days+' days ('+(sn.headlines||[]).length+')</b>'+(hd.length?'<ul>'+hd.map(x=>'<li>'+esc((x.time_ist||'').slice(0,10))+' '+lnk(x.link,x.title)+' <span class="muted">'+esc(x.publisher||'')+'</span></li>').join('')+'</ul>':'<p class="muted">none'+(sn.headlines_ok?'':' (feed did not answer)')+'</p>')+'</div>';
  (sn.notes||[]).forEach(n=>h+='<p class="small muted">'+esc(n)+'</p>')}
 return h+'<p class="small muted">Headlines are leads, not facts: open the filing or story before acting. Good news never upgrades a check.</p>'}
const sc=$('#tab-screener');
const reg=S.regime||{};
let regHtml='';
if(reg.state){regHtml='<div class="card"><h2>Market <span class="badge b-'+esc(reg.state)+'">'+esc(reg.label)+'</span> '+
 (reg.gate?'<span class="muted small">regime gate on: risk x'+reg.risk_multiplier+'</span>':'<span class="muted small">regime gate off (shown for context)</span>')+'</h2><div class="small muted">'+
 (reg.facts||[]).map(esc).join(' &middot; ')+'</div>'+(S.top_sectors&&S.top_sectors.length?'<div class="small" style="margin-top:6px">Top sectors: <b>'+S.top_sectors.map(esc).join(', ')+'</b></div>':'')+'</div>'}
const maxN=Math.max(...S.layers.map(l=>l.count),1);
let fun='<div class="card funnel"><h2>The funnel</h2>'+S.layers.map(l=>'<div class="row"><div class="n">'+l.count+'</div><div><div class="bar" style="width:'+
 Math.max(4,l.count/maxN*100)+'%">'+esc(l.label)+'</div>'+(l.rejects.length?'<div class="why">out: '+l.rejects.slice(0,4).map(([k,v])=>esc(k)+' '+v).join(', ')+'</div>':'')+'</div></div>').join('')+'</div>';
const C=S.candidates||[], WL=S.watchlist||[];
sc.innerHTML=regHtml+fun+
 '<div class="card"><h2>Trade plans <span class="muted small">'+C.length+' triggered, '+C.filter(c=>c.status!=='REJECT').length+' passed validation</span></h2>'+
 (C.length?'<div class="controls"><input type="search" id="q1" placeholder="Filter symbol or sector"><label class="tg"><input type="checkbox" id="hideRej"> hide REJECT</label></div><div class="wrap"><table id="t1"><thead></thead><tbody></tbody></table></div>':
 '<p class="muted">No stock triggered every entry rule on the last bar. Zero trades is a normal, healthy result.</p>')+'</div>'+
 '<div class="grid2"><div class="card"><h3 id="ctitle">Chart</h3><div id="chart"><p class="muted small">Click a stock to see its chart.</p></div></div><div class="card"><h3>Checks</h3><div id="checks" class="small muted">The automated checks for a trade plan appear here.</div></div></div>'+
 '<div class="card"><h2>Near misses <span class="muted small">passed every filter; dots = entry rules true on the last bar</span></h2>'+
 (WL.length?'<div class="controls"><input type="search" id="q2" placeholder="Filter symbol or sector"></div><div class="wrap"><table id="t2"><thead></thead><tbody></tbody></table></div>':'<p class="muted">No stock passed the filters.</p>')+'</div>'+
 (NW&&Object.keys(NW.sectors||{}).length?'<div class="card"><h2>Sector news <span class="muted small">last '+NW.headline_days+' days: news explains a move, price decides</span></h2>'+Object.entries(NW.sectors).map(([sec,hs])=>'<div class="small" style="margin:8px 0"><b>'+esc(sec)+'</b>'+(hs.length?'<ul>'+hs.slice(0,4).map(x=>'<li>'+esc((x.time_ist||'').slice(0,10))+' '+lnk(x.link,x.title)+' <span class="muted">'+esc(x.publisher||'')+'</span></li>').join('')+'</ul>':' <span class="muted">no headlines</span>')+'</div>').join('')+((NW.errors||[]).length?'<p class="small muted">'+NW.errors.map(esc).join('<br>')+'</p>':'')+'</div>':'')+
 (S.warnings&&S.warnings.length?'<div class="card small muted">'+S.warnings.map(w=>'&bull; '+esc(w)).join('<br>')+'</div>':'');
const rules=(D.card&&D.card.entry&&D.card.entry.when)||[];
function show(sym){const c=C.find(x=>x.symbol===sym)||WL.find(x=>x.symbol===sym);if(!c)return;
 charts.length=0;$('#ctitle').innerHTML=esc(c.symbol)+' <span class="muted small">'+esc(c.name||'')+' &middot; '+esc(c.sector||'')+'</span>';
 if(c.chart)Chart($('#chart'),c.chart);else $('#chart').innerHTML='<p class="muted small">No chart embedded for this stock (only the top 40 near misses carry one).</p>';
 if(c.checks){const sz=c.sizing||{};$('#checks').innerHTML='<div style="margin-bottom:8px"><span class="badge b-'+c.status+'">'+c.status+'</span> <span class="badge b-'+c.state+'">'+(c.state==='pending'?'order still live':'new signal')+'</span> <span class="muted">signal bar '+esc(c.signal_bar)+'</span></div>'+
  (sz.ok?'<div class="kpi" style="margin-bottom:10px"><div><b>'+sz.qty+'</b><span>shares</span></div><div><b>&#8377;'+inr(sz.position_value)+'</b><span>position</span></div><div><b>&#8377;'+inr(sz.risk_rupees)+'</b><span>at risk</span></div>'+
  (sz.net_at_target!=null?'<div><b>&#8377;'+inr(sz.net_at_target)+'</b><span>net at target</span></div>':'')+'</div>':'')+
  '<table class="checks"><tbody>'+c.checks.map(k=>'<tr><td><span class="badge b-'+k.status+'">'+k.status+'</span></td><td>'+esc(k.check)+'</td><td>'+esc(k.detail)+'</td></tr>').join('')+'</tbody></table>'+newsHtml(c.symbol)}
 else{$('#checks').innerHTML='<div><b>'+(c.rules_true==null?'':c.rules_true+' of '+c.rules_total)+'</b> entry rules true on the last bar:</div><ul>'+rules.map((r,i)=>'<li class="'+(c.flags&&c.flags[i]?'pos':'neg')+'">'+(c.flags&&c.flags[i]?'&#10003; ':'&#10007; ')+esc(r)+'</li>').join('')+'</ul>'}
 $('#chart').scrollIntoView({behavior:'smooth',block:'nearest'})}
if(C.length){const cols=[
 {h:'Status',l:1,f:x=>'<span class="badge b-'+x.status+'">'+x.status+'</span>',v:x=>({PASS:0,REVIEW:1,REJECT:2})[x.status]},
 {h:'Symbol',l:1,f:x=>'<b>'+esc(x.symbol)+'</b>'+(x.state==='pending'?' <span class="badge b-pending">live</span>':''),v:x=>x.symbol},
 {h:'Sector',l:1,f:x=>esc((x.sector||'').slice(0,26)),v:x=>x.sector},
 {h:'Order',l:1,f:x=>esc({market:'market',stop:'buy-stop',limit:'limit'}[x.signal.type]||x.signal.type),v:x=>x.signal.type},
 {h:'Entry',f:x=>inr(x.entry,2),v:x=>x.entry},{h:'Stop',f:x=>inr(x.stop,2),v:x=>x.stop},
 {h:'Stop %',f:x=>num((x.entry-x.stop)/x.entry*100,1),v:x=>(x.entry-x.stop)/x.entry},
 {h:'Target',f:x=>x.target?inr(x.target,2):'trail',v:x=>x.target},{h:'R:R',f:x=>x.rr?num(x.rr,1):'-',v:x=>x.rr},
 {h:'Qty',f:x=>x.sizing&&x.sizing.ok?x.sizing.qty:'-',v:x=>x.sizing&&x.sizing.qty},
 {h:'Position &#8377;',f:x=>x.sizing&&x.sizing.ok?inr(x.sizing.position_value):'-',v:x=>x.sizing&&x.sizing.position_value},
 {h:'Risk &#8377;',f:x=>x.sizing&&x.sizing.ok?inr(x.sizing.risk_rupees):'-',v:x=>x.sizing&&x.sizing.risk_rupees},
 {h:'RS',f:x=>x.rs_rank==null?'-':x.rs_rank,v:x=>x.rs_rank}];
 const t=sortable($('#t1'),C,cols,show,'symbol');
 const ap=()=>{const q=($('#q1').value||'').toLowerCase(),h=$('#hideRej').checked;t.set(C.filter(x=>(!h||x.status!=='REJECT')&&(x.symbol.toLowerCase().includes(q)||(x.sector||'').toLowerCase().includes(q))))};
 $('#q1').oninput=ap;$('#hideRej').onchange=ap;show(C[0].symbol)}
if(WL.length){const cols=[
 {h:'Symbol',l:1,f:x=>'<b>'+esc(x.symbol)+'</b>',v:x=>x.symbol},{h:'Sector',l:1,f:x=>esc((x.sector||'').slice(0,26)),v:x=>x.sector},
 {h:'Rules true',l:1,f:x=>x.rules_true==null?'-':'<span class="dots">'+(x.flags||[]).map(f=>'<span class="'+(f?'y':'')+'"></span>').join('')+'</span> '+x.rules_true+'/'+x.rules_total,v:x=>x.rules_true},
 {h:'Close',f:x=>inr(x.close,2),v:x=>x.close},{h:'1m %',f:x=>num(x.ret_21,1,1),c:x=>cls(x.ret_21),v:x=>x.ret_21},
 {h:'ATR %',f:x=>num(x.atr_pct,1),v:x=>x.atr_pct},{h:'Traded &#8377; cr',f:x=>num(x.tv_cr,0),v:x=>x.tv_cr},
 {h:'RS',f:x=>x.rs_rank==null?'-':x.rs_rank,v:x=>x.rs_rank}];
 const t2=sortable($('#t2'),WL,cols,show,'symbol');
 $('#q2').oninput=()=>{const q=$('#q2').value.toLowerCase();t2.set(WL.filter(x=>x.symbol.toLowerCase().includes(q)||(x.sector||'').toLowerCase().includes(q)))};
 if(!C.length)show(WL[0].symbol)}
});

// ---------------------------------------------------------------- line chart (equity)
function LineChart(box,series){const cv=document.createElement('canvas');box.innerHTML='';box.className='chartbox';box.appendChild(cv);
 const leg=document.createElement('div');leg.className='legend';leg.innerHTML=series.map(s=>'<span><i style="background:'+css(s.color)+'"></i>'+esc(s.label)+'</span>').join('');box.appendChild(leg);
 const self={draw(){if(box.offsetParent===null)return;const W=box.clientWidth,H=Math.max(220,Math.min(340,W*0.42)),dpr=window.devicePixelRatio||1;
  cv.width=W*dpr;cv.height=H*dpr;cv.style.height=H+'px';const g=cv.getContext('2d');g.setTransform(dpr,0,0,dpr,0,0);
  g.fillStyle=css('--panel');g.fillRect(0,0,W,H);const all=series.flatMap(s=>s.pts.map(p=>p[1]));let lo=Math.min(...all),hi=Math.max(...all);const sp=(hi-lo)||1;lo-=sp*.05;hi+=sp*.05;
  const padR=56,padB=18,n=Math.max(...series.map(s=>s.pts.length));const y=v=>8+(hi-v)/(hi-lo)*(H-padB-8);
  g.strokeStyle=css('--grid');g.fillStyle=css('--muted');g.font='11px -apple-system,sans-serif';
  for(let k=0;k<=4;k++){const v=lo+(hi-lo)*k/4;g.beginPath();g.moveTo(0,y(v));g.lineTo(W-padR,y(v));g.stroke();g.fillText(num(v,0),W-padR+4,y(v)+4)}
  series.forEach(s=>{const m=s.pts.length;g.strokeStyle=css(s.color);g.lineWidth=2;g.beginPath();s.pts.forEach((p,i)=>{const xx=i/(m-1||1)*(W-padR);i?g.lineTo(xx,y(p[1])):g.moveTo(xx,y(p[1]))});g.stroke()});
  const p0=series[0].pts;g.textAlign='center';[0,Math.floor(p0.length/2),p0.length-1].forEach(i=>{if(p0[i])g.fillText(String(p0[i][0]).slice(0,10),Math.min(W-padR-30,Math.max(30,i/(p0.length-1||1)*(W-padR))),H-4)})}};
 charts.push(self);self.draw();return self}

// ---------------------------------------------------------------- backtest tab
safe('backtest',()=>{
const B=D.backtest, bt=$('#tab-backtest');
function srow(name,s){if(!s||!s.n)return '<tr><td class="l">'+name+'</td><td colspan="8" class="muted l">no trades</td></tr>';
 return '<tr><td class="l">'+name+'</td><td>'+s.n+'</td><td>'+num(s.win_rate,1)+'%</td><td class="pos">'+num(s.avg_win_r,2,1)+'</td><td class="neg">'+num(s.avg_loss_r,2,1)+
 '</td><td class="'+cls(s.expectancy_r)+'"><b>'+num(s.expectancy_r,2,1)+'</b></td><td>'+num(s.gross_expectancy_r,2,1)+'</td><td>'+(s.profit_factor==null?'&infin;':num(s.profit_factor,2))+'</td><td>'+num(s.max_dd_r,1)+'</td><td>'+num(s.avg_bars,1)+'</td></tr>'}
if(!B){bt.innerHTML='<div class="card"><h2>No backtest yet</h2><p class="muted">Run <code>screener.py backtest --strategy '+esc(S.strategy)+'</code> and rescan: the results appear here.</p></div>'}
else{const w=B.with_funnel,a=w.all,pf=B.portfolio_funnel||{},bm=B.benchmark||{};
 bt.innerHTML='<div class="card"><h2>Verdict <span class="badge b-'+esc(B.verdict.tone)+'">'+esc(B.verdict.tone)+'</span></h2><div class="verdict">'+B.verdict.lines.map(esc).join('<br>')+'</div>'+
 '<p class="small muted">'+esc(B.timeframe)+' chart, '+esc(B.window[0])+' to '+esc(B.window[1])+' ('+B.sessions+' sessions), '+B.stocks+' stocks, '+B.signals+' signals. Filters: '+esc((B.filters_on||[]).join(', '))+
 '. Card version '+esc(B.card_hash||'')+'. Net of Indian charges and slippage; 1R = the money at risk from entry to stop.</p></div>'+
 '<div class="card"><div class="kpi"><div><b>'+(a.n||0)+'</b><span>trades (with filters)</span></div><div><b>'+num(a.win_rate,1)+'%</b><span>win rate</span></div>'+
 '<div><b class="'+cls(a.expectancy_r)+'">'+num(a.expectancy_r,2,1)+'R</b><span>expectancy, net</span></div><div><b class="'+cls(w.out_of_sample.expectancy_r)+'">'+num(w.out_of_sample.expectancy_r,2,1)+'R</b><span>out-of-sample ('+(w.out_of_sample.n||0)+')</span></div>'+
 '<div><b>'+num(pf.cagr_pct,1)+'%</b><span>portfolio CAGR</span></div><div><b>'+num(bm.cagr_pct,1)+'%</b><span>Nifty 500 CAGR</span></div><div><b class="neg">'+num(pf.max_drawdown_pct,1)+'%</b><span>max drawdown</span></div></div></div>'+
 '<div class="card"><h2>Per trade</h2><div class="wrap"><table><thead><tr><th class="l">Slice</th><th>Trades</th><th>Win</th><th>Avg win</th><th>Avg loss</th><th>Expectancy</th><th>Gross</th><th>PF</th><th>Max DD R</th><th>Bars</th></tr></thead><tbody>'+
 srow('Rules alone',B.setup_only.all)+srow('Rules + filters',a)+srow('&nbsp; in-sample',w.in_sample)+srow('&nbsp; out-of-sample'+(B.oos_from?' (from '+esc(B.oos_from)+')':''),w.out_of_sample)+
 ['bullish','neutral','bearish'].map(k=>srow('Rules alone, '+k+' market',B.setup_only.by_regime[k])).join('')+
 Object.keys(w.by_year).map(y=>srow('&nbsp; '+y,w.by_year[y])).join('')+'</tbody></table></div></div>'+
 '<div class="card"><h2>Portfolio with your limits vs the Nifty 500</h2><div id="eq"></div><p class="small muted">&#8377;'+inr(pf.start_capital)+' &rarr; &#8377;'+inr(pf.final_equity)+', '+pf.trades+' trades taken, '+pf.skipped_for_limits+' skipped for limits, in the market '+num(pf.time_in_market_pct,0)+'% of days. Nifty 500 is the price index (no dividends).</p></div>'+
 '<div class="card small"><h3>How trades ended</h3>'+Object.entries(a.exit_mix||{}).map(([k,v])=>esc(k)+' '+v).join(' &middot; ')+'</div>';
 if(pf.curve&&pf.curve.length>1&&bm.curve){const c0=pf.curve[0][1],b0=bm.curve[0][1];
  LineChart($('#eq'),[{label:'Strategy (₹100 start)',color:'--accent',pts:pf.curve.map(p=>[p[0],p[1]/c0*100])},{label:'Nifty 500 (100 start)',color:'--muted',pts:bm.curve.map(p=>[p[0],p[1]/b0*100])}])}}
});

// ---------------------------------------------------------------- forward tab
safe('forward',()=>{
const F=D.forward, fw=$('#tab-forward');
if(!F){fw.innerHTML='<div class="card"><h2>No forward test running</h2><p class="muted">Start one with <code>screener.py forward start --strategy '+esc(S.strategy)+'</code>. It freezes the card and paper-trades every new signal from then on, on bars nobody has seen yet.</p></div>'}
else{const s=F.with_filters||{},ref=F.backtest_ref;
 fw.innerHTML='<div class="card"><h2>Forward test since '+esc(F.meta.start)+'</h2>'+(F.card_changed?'<p class="neg small">The card was edited after this test started. This test keeps running the frozen version; restart it to test the new rules.</p>':'')+
 '<div class="kpi"><div><b>'+(F.sessions||0)+'</b><span>sessions</span></div><div><b>'+(F.signals||0)+'</b><span>signals</span></div><div><b>'+(s.n||0)+'</b><span>closed trades</span></div><div><b>'+(F.open_trades||0)+'</b><span>open now ('+num(F.open_r,2,1)+'R)</span></div>'+
 '<div><b class="'+cls(s.expectancy_r)+'">'+(s.n?num(s.expectancy_r,2,1)+'R':'-')+'</b><span>forward expectancy</span></div><div><b>'+(ref?num(ref.out_of_sample.expectancy_r,2,1)+'R':'-')+'</b><span>backtest out-of-sample</span></div><div><b>'+(s.n?num(s.win_rate,0)+'%':'-')+'</b><span>forward win rate</span></div><div><b>'+(ref?num(ref.all.win_rate,0)+'%':'-')+'</b><span>backtest win rate</span></div></div>'+
 '<p class="small muted">'+(s.n&&s.n<30?'Only '+s.n+' closed trades: too few to judge. Keep it running.':'')+(F.note?esc(F.note):'')+'</p></div>'+
 '<div class="card"><h2>Paper trades</h2><div class="controls"><label class="tg"><input type="checkbox" id="fwall"> also show signals your filters skipped</label></div><div id="fwt"></div></div>';
 const ft=()=>{const all=$('#fwall').checked,rows=(F.trades||[]).filter(t=>all||t.in_funnel);
 $('#fwt').innerHTML=rows.length?'<div class="wrap"><table><thead><tr><th class="l">Symbol</th><th class="l">Signal</th><th>Entry</th><th>Stop</th><th>Target</th><th class="l">Exit</th><th>Exit &#8377;</th><th class="l">How</th><th>R net</th><th class="l">Filters</th></tr></thead><tbody>'+
 rows.slice().reverse().map(t=>'<tr><td class="l"><b>'+esc(t.symbol)+'</b></td><td class="l">'+esc(t.signal_date)+'</td><td>'+inr(t.entry,2)+'</td><td>'+inr(t.stop0,2)+'</td><td>'+(t.target?inr(t.target,2):'-')+'</td><td class="l">'+(t.open?'<span class="badge b-new">open</span>':esc(t.exit_date))+'</td><td>'+inr(t.exit,2)+'</td><td class="l">'+esc(t.exit_reason)+'</td><td class="'+cls(t.r_net)+'">'+num(t.r_net,2,1)+'</td><td class="l small">'+(t.in_funnel?'passed':'<span class="muted">'+esc(t.funnel_why||'')+'</span>')+'</td></tr>').join('')+'</tbody></table></div>':'<p class="muted">No trade has filled yet'+(all?'':' that passed your filters')+'.</p>'};
 $('#fwall').onchange=ft;ft()}
});

// ---------------------------------------------------------------- strategy tab
safe('strategy',()=>{
$('#tab-strategy').innerHTML='<div class="card"><h2>The strategy in plain words</h2><pre>'+esc(D.card_words)+'</pre></div><div class="card"><details><summary>Strategy card (JSON, shareable)</summary><pre>'+esc(JSON.stringify(D.card,null,2))+'</pre></details></div>';
});
})();
</script></body></html>
"""
