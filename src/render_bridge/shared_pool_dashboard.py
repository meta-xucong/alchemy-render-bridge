"""Public, read-only fleet status dashboard served by the Bridge."""


STATUS_DASHBOARD_HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RenderBridge 节点看板</title><style>
:root{font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:#172734;background:#f2f6f8}*{box-sizing:border-box}body{margin:0}
header{background:#122736;color:#fff;padding:22px max(20px,calc((100vw - 1180px)/2))}main{max-width:1180px;margin:22px auto;padding:0 18px}
h1{font-size:23px;margin:0}h2{font-size:17px;margin:0 0 12px}.sub{color:#c7d7df;font-size:13px;margin-top:3px}
.toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:16px 0}.status{color:#526875;font-size:13px}.error{color:#a63838}
button,a{font:inherit}button{padding:8px 13px;border:1px solid #185b7b;border-radius:7px;background:#185b7b;color:white;cursor:pointer}a{color:#185b7b}
.summary{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:10px;margin:14px 0}.metric,.node{background:white;border:1px solid #dce5e9;border-radius:10px}
.metric{padding:13px 15px}.metric-label{color:#607582;font-size:12px}.metric-value{font-size:27px;font-weight:650;line-height:1.2;margin-top:4px}
.panel{background:white;border:1px solid #dce5e9;border-radius:10px;padding:17px;margin:15px 0}.nodes{display:grid;grid-template-columns:repeat(auto-fill,minmax(265px,1fr));gap:11px}
.node{padding:14px;border-left:4px solid #8d9ba4}.node.available{border-left-color:#25845a}.node.busy{border-left-color:#b27a16}.node.attention{border-left-color:#c05d26}.node.offline,.node.unknown{border-left-color:#9b4e4e}.node.draining{border-left-color:#657580}
.node-head{display:flex;align-items:center;justify-content:space-between;gap:8px}.node-label{font-size:16px;font-weight:650}.badge{font-size:12px;font-weight:600;border-radius:999px;padding:3px 9px;background:#e9eef1;color:#40535f;white-space:nowrap}
.available .badge{background:#e4f4eb;color:#176b43}.busy .badge{background:#fff2d9;color:#885700}.attention .badge{background:#fff0e5;color:#a74815}.offline .badge,.unknown .badge{background:#fae8e8;color:#923e3e}
.node-meta{margin-top:12px;color:#5c707c;font-size:13px}.node-meta div+div{margin-top:4px}.empty{color:#637782;padding:14px 2px}.foot{color:#657580;font-size:12px;padding:4px 0 25px}
@media(max-width:800px){.summary{grid-template-columns:repeat(3,minmax(95px,1fr))}}
@media(max-width:460px){.summary{grid-template-columns:repeat(2,minmax(95px,1fr))}.panel{padding:13px}}
</style></head><body><header><h1>RenderBridge 节点看板</h1><div class="sub">VPS 节点总体状态 · 自动刷新</div></header>
<main><div class="toolbar"><div id="refreshState" class="status" role="status" aria-live="polite">正在读取节点状态…</div><div><button id="refresh" type="button">立即刷新</button> <a href="/bridge/operator">Operator 管理</a></div></div>
<section id="summary" class="summary" aria-label="节点汇总"></section><section class="panel"><h2>全部节点</h2><div id="nodes" class="nodes"><div class="empty">正在读取节点列表…</div></div></section>
<div class="foot">此看板只公开节点别名、可用状态、心跳时间和支持能力；任务、工作区和管理数据仍受保护。</div></main>
<script>
(()=>{
const $=id=>document.getElementById(id),timerDelay=5000;
const labels={available:'可用',busy:'执行中',attention:'需处理',offline:'离线',draining:'排空中',unknown:'状态未知'};
let timer=null,inFlight=null,hidden=false;
function stopTimer(){if(timer!==null){clearTimeout(timer);timer=null}}
function schedule(){stopTimer();if(hidden)return;timer=setTimeout(()=>{timer=null;refresh().catch(()=>{})},timerDelay)}
function text(tag,value,className){const e=document.createElement(tag);e.textContent=String(value??'—');if(className)e.className=className;return e}
function number(value){return Number.isSafeInteger(value)&&value>=0?String(value):'0'}
function age(value){if(!Number.isSafeInteger(value)||value<0)return'未收到心跳';if(value<10)return'刚刚';if(value<60)return value+' 秒前';const minutes=Math.floor(value/60);if(minutes<60)return minutes+' 分钟前';const hours=Math.floor(minutes/60);if(hours<24)return hours+' 小时前';return Math.floor(hours/24)+' 天前'}
function render(data){
 const s=data&&data.summary&&typeof data.summary==='object'?data.summary:{};
 const metrics=[['节点总数','nodes_total'],['在线','nodes_online'],['可用','nodes_available'],['执行中','nodes_busy'],['需处理','nodes_attention'],['排空中','nodes_draining'],['离线','nodes_offline'],['未知','nodes_unknown']];
 const summary=$('summary');summary.replaceChildren();for(const [label,key] of metrics){const card=document.createElement('div');card.className='metric';card.append(text('div',label,'metric-label'),text('div',number(s[key]),'metric-value'));summary.append(card)}
 const host=$('nodes');host.replaceChildren();const nodes=Array.isArray(data?.nodes)?data.nodes:[];
 if(!nodes.length){host.append(text('div','暂未登记节点。','empty'));return}
 for(const item of nodes){const status=Object.prototype.hasOwnProperty.call(labels,item?.availability)?item.availability:'unknown';const card=document.createElement('article');card.className='node '+status;
  const head=document.createElement('div');head.className='node-head';head.append(text('div',item?.label||'节点','node-label'),text('span',labels[status],'badge'));
  const meta=document.createElement('div');meta.className='node-meta';meta.append(text('div','心跳：'+age(item?.heartbeat_age_seconds)));
  const profiles=Array.isArray(item?.profiles)?item.profiles.filter(x=>typeof x==='string').join('、'):'Unknown';meta.append(text('div','能力：'+(profiles||'未报告')));
  if(item?.state==='draining')meta.append(text('div','管理状态：排空中'));
  card.append(head,meta);host.append(card)
 }
}
async function refresh(){if(inFlight)return inFlight;const pending=(async()=>{try{const response=await fetch('/bridge/status/v1',{method:'GET',headers:{'Accept':'application/json'},cache:'no-store'});if(!response.ok)throw new Error('HTTP '+response.status);const data=await response.json();render(data);$('refreshState').textContent='最近更新：'+new Date().toLocaleTimeString('zh-CN')+' · 每 5 秒自动刷新';$('refreshState').className='status'}catch(error){$('refreshState').textContent='暂时无法获取状态，正在重试。';$('refreshState').className='status error';throw error}finally{if(!hidden)schedule()}})();inFlight=pending;try{await pending}finally{if(inFlight===pending)inFlight=null}}
document.addEventListener('visibilitychange',()=>{hidden=document.hidden;if(hidden)stopTimer();else refresh().catch(()=>{})});
window.addEventListener('beforeunload',stopTimer);$('refresh').onclick=()=>refresh().catch(()=>{});refresh().catch(()=>{});
})();
</script></body></html>'''
