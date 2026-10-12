#!/usr/bin/env python3
"""netwatch の CSV をブラウザでグラフ表示する(標準ライブラリのみ)。

  python3 netwatch_view.py netwatch.csv [--port 8080] [--host 0.0.0.0]
ブラウザで http://<このマシンのIP>:8080/ を開く。
"""
import argparse
import http.server
import os

PAGE = r"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>netwatch</title>
<style>
:root{--bg:#fff;--fg:#222;--sub:#777;--card:#f3f3f3;--node:#fff;--line:#bbb}
@media(prefers-color-scheme:dark){:root{--bg:#161616;--fg:#e6e6e6;--sub:#999;--card:#222;--node:#2b2b2b;--line:#555}}
body{margin:0;padding:12px 16px;background:var(--bg);color:var(--fg);font:14px system-ui,sans-serif}
h1{font-size:16px;margin:0 0 8px}h2{font-size:14px;margin:18px 0 6px}
#bar{display:flex;gap:14px;flex-wrap:wrap;align-items:center;margin-bottom:8px}
select{font:inherit;padding:2px 6px}
#topo{width:100%;max-width:1120px;display:block;background:var(--card);border-radius:8px}
#topo text{fill:var(--fg);font-size:13px}#topo .s{fill:var(--sub);font-size:11px}
#heat{width:100%;display:block;background:var(--card);border-radius:8px}
#tip{height:18px;color:var(--sub);font-size:12px}
table{border-collapse:collapse;width:100%;max-width:1120px;font-size:13px}
td,th{padding:4px 8px;text-align:left;border-bottom:1px solid var(--line)}th{color:var(--sub);font-weight:normal}
.g{display:inline-block;padding:0 8px;border-radius:9px;color:#fff;font-size:12px}
.key{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--sub)}
.grad{width:120px;height:10px;border-radius:5px;background:linear-gradient(90deg,hsl(120,70%,45%),hsl(60,70%,45%),hsl(0,70%,45%))}
</style></head><body>
<h1>netwatch <span id="info" style="color:var(--sub);font-weight:normal"></span></h1>
<div id="bar">
 判定窓 <select id="win"><option value="10">10秒</option><option value="30" selected>30秒</option><option value="120">2分</option></select>
 履歴 <select id="range"><option value="300">5分</option><option value="900" selected>15分</option><option value="3600">1時間</option><option value="21600">6時間</option></select>
 <span class="key">良い<span class="grad"></span>悪い</span>
 <span class="key"><span style="width:12px;height:12px;background:#222;border:1px solid #777"></span>ロス(応答なし)</span>
 <span class="key"><span style="width:12px;height:12px;background:#888"></span>ICMP非応答</span>
</div>
<svg id="topo" viewBox="0 0 1120 250"></svg>
<h2>時間経過(1マス=1回の測定。右が最新)</h2>
<canvas id="heat"></canvas><div id="tip"></div>
<h2>悪化イベント <select id="evf"><option value="BAD">BAD のみ</option><option value="ALL" selected>WARN + BAD</option></select>
 <select id="evt"><option value="">全ての層</option></select></h2>
<table id="ev"></table>
<script>
// netwatch.py の LIMITS と同じ値 (WARN, BAD)
const LIM={gw:{avg:[20,60],jit:[10,30]},onu:{avg:[25,70],jit:[10,30]},isp:{avg:[40,100],jit:[15,40]},
  ext:{avg:[60,150],jit:[20,60]},dns:{avg:[100,300],jit:[50,150]}};
const LOSS=[5,10],MIN_LOST=2; // 窓内で2回以上落ちたときだけロスとして数える
const LABEL={gw:"②ルーター",onu:"③ONU/モデム",isp:"④ISP初段",dns:"DNS"};
const kind=c=>c.startsWith("ext_")?"ext":c;
const name=c=>LABEL[c]||("⑤外部 "+c.slice(4));
let cols=[],rows=[];
// 0(良)〜1(悪) に正規化。WARN で 0.5、BAD で 1.0
function norm(v,[w,b]){return v<=0?0:v<w?0.5*v/w:Math.min(1,0.5+0.5*(v-w)/(b-w))}
function sampleScore(c,v){return v===null?1:norm(v,LIM[kind(c)].avg)}
function color(s){return `hsl(${120*(1-s)},70%,45%)`}
function winStats(ci,n){
  const d=rows.slice(-n).map(r=>r.v[ci]);if(!d.length)return null;
  const ok=d.filter(x=>x!==null),loss=100*(d.length-ok.length)/d.length;
  const avg=ok.length?ok.reduce((a,b)=>a+b)/ok.length:null;
  let jit=0;for(let i=1;i<ok.length;i++)jit+=Math.abs(ok[i]-ok[i-1]);jit=ok.length>1?jit/(ok.length-1):0;
  const l=LIM[kind(cols[ci])];
  const lostN=d.length-ok.length;
  const score=Math.max(lostN<MIN_LOST?0:norm(loss,LOSS),avg===null?1:norm(avg,l.avg),norm(jit,l.jit));
  return{loss,avg,jit,max:ok.length?Math.max(...ok):null,score};
}
function status(n){
  const st=cols.map((_,i)=>winStats(i,n));
  const extOk=cols.some((c,i)=>kind(c)==="ext"&&st[i]&&st[i].loss<100);
  cols.forEach((c,i)=>{if(st[i]&&["gw","onu","isp"].includes(c)&&st[i].loss>=100&&extOk)st[i].na=true});
  return st;
}
function el(t,a,txt){const e=document.createElementNS("http://www.w3.org/2000/svg",t);
  for(const k in a)e.setAttribute(k,a[k]);if(txt!==undefined)e.textContent=txt;return e}
function drawTopo(){
  const n=+document.getElementById("win").value,st=status(n),svg=document.getElementById("topo");
  svg.innerHTML="";
  const idx=c=>cols.indexOf(c);
  const nodes=[["①端末",75],["ルーター(AP)",320],["ONU/モデム",565],["ISP",810],["インターネット",1045]];
  const ext=cols.map((c,i)=>kind(c)==="ext"?i:-1).filter(i=>i>=0);
  const links=[
    {ci:[idx("gw")],lab:"Wi-Fi ① 〜 ②"},{ci:[idx("onu")],lab:"② 〜 ③ (有線)"},
    {ci:[idx("isp")],lab:"③ 〜 ④"},{ci:ext,lab:"④ 〜 ⑤"}];
  const Y=90;
  links.forEach((lk,i)=>{
    const x1=nodes[i][1]+55,x2=nodes[i+1][1]-55;
    const ss=lk.ci.filter(j=>j>=0&&st[j]).map(j=>st[j]);
    let col="#888",sub="未検出",dash="6 6";
    if(ss.length){
      dash="";
      if(ss.every(s=>s.na)){sub="ICMP非応答"}
      else{const w=ss.reduce((a,b)=>a.score>=b.score?a:b);col=color(w.score);
        sub=(w.avg===null?"応答なし":w.avg.toFixed(0)+"ms")+" / ロス"+w.loss.toFixed(0)+"% / 揺れ"+w.jit.toFixed(0)}
    }
    svg.appendChild(el("line",{x1,y1:Y,x2,y2:Y,stroke:col,"stroke-width":14,"stroke-linecap":"round","stroke-dasharray":dash}));
    svg.appendChild(el("text",{x:(x1+x2)/2,y:Y-18,"text-anchor":"middle"},lk.lab));
    svg.appendChild(el("text",{x:(x1+x2)/2,y:Y+32,"text-anchor":"middle",class:"s"},sub));
  });
  nodes.forEach(([t,x])=>{
    svg.appendChild(el("rect",{x:x-55,y:Y-26,width:110,height:52,rx:8,fill:"var(--node)",stroke:"var(--line)","stroke-width":2}));
    svg.appendChild(el("text",{x,y:Y+5,"text-anchor":"middle"},t));
  });
  const extra=[...ext,idx("dns")].filter(i=>i>=0);
  extra.forEach((j,k)=>{
    const s=st[j],x=927+(k-(extra.length-1)/2)*130,y=190;
    svg.appendChild(el("rect",{x:x-58,y:y-20,width:116,height:40,rx:8,fill:s?color(s.score):"#888"}));
    svg.appendChild(el("text",{x,y:y-2,"text-anchor":"middle",style:"fill:#fff;font-weight:600"},name(cols[j])));
    svg.appendChild(el("text",{x,y:y+13,"text-anchor":"middle",style:"fill:#fff;font-size:11px"},
      s?(s.avg===null?"応答なし":s.avg.toFixed(0)+"ms / ロス"+s.loss.toFixed(0)+"%"):"-"));
  });
}
const CELL=6,LBL=100,RH=22;
let view={};
function drawHeat(){
  const cv=document.getElementById("heat"),ctx=cv.getContext("2d");
  const W=cv.clientWidth,n=Math.max(10,Math.floor((W-LBL-8)/CELL));
  const want=+document.getElementById("range").value;
  // 横幅に収まらない分は何件かまとめて1マスにし、最悪値で色を付ける
  const take=rows.slice(-want),per=Math.max(1,Math.ceil(take.length/n)),H=cols.length*RH+8,dpr=devicePixelRatio||1;
  cv.width=W*dpr;cv.height=H*dpr;cv.style.height=H+"px";ctx.setTransform(dpr,0,0,dpr,0,0);
  const cells=Math.ceil(take.length/per);view={per,take,x0:W-8-cells*CELL};
  const st=status(+document.getElementById("win").value);
  ctx.font="12px system-ui";ctx.textBaseline="middle";
  cols.forEach((c,ci)=>{
    const y=4+ci*RH;ctx.fillStyle=getComputedStyle(document.body).color;ctx.textAlign="left";
    ctx.fillText(name(c),4,y+RH/2-2);
    const na=st[ci]&&st[ci].na;
    for(let k=0;k<cells;k++){
      const seg=take.slice(k*per,(k+1)*per).map(r=>r.v[ci]);
      const lost=seg.some(v=>v===null),ok=seg.filter(v=>v!==null);
      if(na)ctx.fillStyle="#888";
      else if(lost&&ok.length===0)ctx.fillStyle="#222";
      else{const s=Math.max(...ok.map(v=>sampleScore(c,v)),lost?0.9:0);ctx.fillStyle=color(s)}
      ctx.fillRect(view.x0+k*CELL,y,CELL-1,RH-3);
    }
  });
}
document.getElementById("heat").onmousemove=e=>{
  if(!view.take)return;const r=e.target.getBoundingClientRect();
  const ci=Math.floor((e.clientY-r.top-4)/RH),k=Math.floor((e.clientX-r.left-view.x0)/CELL);
  const seg=view.take.slice(k*view.per,(k+1)*view.per);
  const tip=document.getElementById("tip");
  if(ci<0||ci>=cols.length||k<0||!seg.length){tip.textContent="";return}
  const v=seg.map(x=>x.v[ci]).map(x=>x===null?"LOST":x.toFixed(1)+"ms").join(", ");
  tip.textContent=`${name(cols[ci])}  ${seg[0].t.slice(11)}  ${v.length>80?v.slice(0,80)+"…":v}`;
};
let evs=[];
function parseCsv(t){const L=t.replace(/\r/g,"").split("\n").filter(Boolean);return L.slice(1).map(l=>l.split(","))}
function fmtDur(s){s=Math.round(s);return s<60?s+"秒":s<3600?Math.floor(s/60)+"分"+(s%60)+"秒":Math.floor(s/3600)+"時間"+Math.floor(s%3600/60)+"分"}
function drawEvents(){
  const only=document.getElementById("evf").value==="BAD",tg=document.getElementById("evt").value;
  // start と end を層ごとに突き合わせる(end が無い start は継続中)
  const open={},list=[];
  evs.forEach(([t,key,label,ev,g,loss,avg,dur,rssi,...dg])=>{const diag=dg.join(",");
    if(ev==="start")open[key]={t0:t,key,label,g:"WARN",ongoing:true,rssi,diag};
    else if(open[key]){const o=open[key];delete open[key];
      list.push({...o,t1:t,g,loss,avg,dur,ongoing:false,rssi:o.rssi||rssi,diag:o.diag||diag})}
    else list.push({t0:"(不明)",t1:t,key,label,g,loss,avg,dur,ongoing:false,rssi,diag});
  });
  Object.values(open).forEach(o=>list.push(o));
  const rows=list.filter(e=>(!only||e.g==="BAD")&&(!tg||e.key===tg))
    .sort((a,b)=>(b.t0>a.t0?1:-1)).slice(0,100);
  let h="<tr><th>開始</th><th>終了</th><th>継続</th><th>層</th><th>最悪</th><th>最大ロス</th><th>最大遅延</th><th>電波</th><th>そのときの診断</th></tr>";
  rows.forEach(e=>{
    h+=`<tr><td>${e.t0}</td><td>${e.ongoing?"<b>継続中</b>":e.t1.slice(11)}</td>`+
       `<td>${e.ongoing?"-":fmtDur(e.dur)}</td><td>${e.label}</td>`+
       `<td><span class="g" style="background:${e.g==="BAD"?"hsl(0,70%,45%)":"hsl(45,80%,45%)"}">${e.ongoing?"進行中":(+e.loss>=100?"断":e.g)}</span></td>`+
       `<td>${e.loss===""||e.loss===undefined?"-":e.loss+"%"}</td><td>${e.avg===""||e.avg===undefined?"-":e.avg+"ms"}</td>`+
       `<td>${e.rssi?e.rssi+"dBm":"-"}</td><td style="color:var(--sub)">${e.diag||""}</td></tr>`});
  if(!rows.length)h+='<tr><td colspan="9" style="color:var(--sub)">該当するイベントはありません</td></tr>';
  document.getElementById("ev").innerHTML=h;
  const sel=document.getElementById("evt");
  if(sel.options.length<=1+0){[...new Set(evs.map(e=>e[1]))].forEach(k=>{
    const o=document.createElement("option");o.value=k;o.textContent=evs.find(e=>e[1]===k)[2];sel.appendChild(o)})}
}
async function load(){
  const t=await (await fetch("data.csv?n="+(+document.getElementById("range").value+60)+"&"+Date.now())).text();
  const L=t.replace(/\r/g,"").split("\n"),head=L[0].split(",");cols=head.slice(1);
  rows=L.slice(1).filter(l=>l&&!l.startsWith("time,")).map(l=>{const f=l.split(",");
    return{t:f[0],v:cols.map((_,i)=>f[i+1]===""||f[i+1]===undefined?null:parseFloat(f[i+1]))}});
  document.getElementById("info").textContent=rows.length?`${rows[rows.length-1].t} 更新 / ${rows.length}件`:"(データなし)";
  drawTopo();drawHeat();
  evs=parseCsv(await (await fetch("events.csv?"+Date.now())).text());
  drawEvents();
}
["evf","evt"].forEach(i=>document.getElementById(i).onchange=drawEvents);
["win","range"].forEach(i=>document.getElementById(i).onchange=()=>{drawTopo();drawHeat()});
addEventListener("resize",drawHeat);
load();setInterval(load,2000);
</script></body></html>"""


def tail_csv(path, max_bytes):
    """ヘッダ行 + 末尾 max_bytes 分の行だけを返す(大きな CSV でも転送量を抑える)。"""
    try:
        with open(path, "rb") as f:
            head = f.readline()
            size = f.seek(0, os.SEEK_END)
            if size - len(head) <= max_bytes:
                f.seek(len(head))
                return head + f.read()
            f.seek(size - max_bytes)
            f.readline()  # 途中で切れた先頭行を捨てる
            return head + f.read()
    except OSError:
        return b"time\n"


def main():
    ap = argparse.ArgumentParser(description="netwatch CSV ビューア")
    ap.add_argument("csv", help="netwatch.py --log で出力した CSV")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0", help="127.0.0.1 にすると SSH トンネル経由のみ")
    a = ap.parse_args()
    path = os.path.abspath(a.csv)
    ev_path = os.path.splitext(path)[0] + ".events.csv"

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            route, _, query = self.path.partition("?")
            if route == "/data.csv":
                n = int(query.split("n=")[1].split("&")[0]) if "n=" in query else 3600
                body = tail_csv(path, min(n * 80, 2_000_000))  # 1行 約60バイト
                ctype = "text/csv; charset=utf-8"
            elif route == "/events.csv":
                body = tail_csv(ev_path, 300_000)
                ctype = "text/csv; charset=utf-8"
            elif route in ("/", "/index.html"):
                body, ctype = PAGE.encode(), "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    print(f"CSV: {path}\nhttp://{a.host}:{a.port}/  (Ctrl+C で終了)")
    try:
        http.server.ThreadingHTTPServer((a.host, a.port), H).serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

