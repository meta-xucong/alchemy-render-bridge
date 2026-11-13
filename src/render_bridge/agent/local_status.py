"""Loopback-only, read-only self-status page for a shared-pool node agent."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import urlsplit


_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RenderBridge 本机节点状态</title><style>
body{font:15px/1.55 system-ui,sans-serif;max-width:900px;margin:32px auto;padding:0 18px;color:#17212b;background:#f4f7fa}
header,.card{background:white;border:1px solid #dce4ec;border-radius:14px;padding:18px;margin:12px 0}
h1{font-size:22px;margin:0 0 4px}.muted{color:#627181;font-size:13px}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}
.metric{background:#f7f9fb;border-radius:10px;padding:12px}.metric strong{display:block;font-size:25px}.row{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
button{border:0;border-radius:8px;background:#1457d9;color:white;padding:9px 14px;font:inherit;cursor:pointer}ul{padding-left:20px}li{margin:8px 0}
</style></head><body><header><div class="row"><div><h1>本机 RenderBridge 节点</h1><div id="node">正在读取节点…</div></div>
<button id="refresh" type="button">立即刷新</button></div><p class="muted">每 5 秒自动更新。未知和待处理任务单独显示。</p><div id="tracking" class="muted">统计启用时间：读取中</div><div id="updated" class="muted">尚未更新</div></header>
<section class="card"><h2>当前状态</h2><div id="current">正在读取…</div></section>
<section class="card"><h2>累计执行次数</h2><div class="metrics">
<div class="metric">总开始数<strong id="total">—</strong></div><div class="metric">成功<strong id="success">—</strong></div>
<div class="metric">失败<strong id="failed">—</strong></div><div class="metric">执行中<strong id="progress">—</strong></div>
<div class="metric">待处理<strong id="waiting">—</strong></div><div class="metric">结果未知<strong id="unknown">—</strong></div>
<div class="metric">已取消<strong id="canceled">—</strong></div></div><p class="muted">成功按 Bridge 已提交图片回执统计；失败仅统计 Bridge 已确认的终态失败。排队中和开始前取消不会计入总开始数。</p></section>
<section class="card"><h2>最近执行</h2><ul id="recent"><li>尚无记录</li></ul></section>
<script>
const labels={active:'正常',draining:'暂停接新任务',revoked:'已撤销',online:'在线',available:'空闲',busy:'忙碌',attention:'需要处理',offline:'离线',assigned:'已分配',claimed:'已领取',running:'执行中',uploading:'上传结果',awaiting_user:'等待本机操作',execution_unknown:'执行结果待确认',succeeded:'成功',failed:'失败',cancelled:'已取消',queued:'排队中',artifacts_ready:'图片已提交'};
const label=(v)=>labels[v]||v||'未知'; const set=(id,v)=>{document.getElementById(id).textContent=String(v??0)};
function stamp(v){if(!v)return '—';const d=new Date(v);return Number.isNaN(d.valueOf())?'—':d.toLocaleString()}
function elapsed(v){if(!v)return '';const seconds=Math.max(0,Math.floor((Date.now()-new Date(v).valueOf())/1000));return ` · 已运行 ${Math.floor(seconds/60)}分${seconds%60}秒`}
function render(data){const n=data.node||{},m=data.metrics||{},c=data.current_execution;const heartbeat=Number.isInteger(n.heartbeat_age_seconds)?` · 心跳 ${n.heartbeat_age_seconds} 秒前`:'';document.getElementById('node').textContent=`${n.node_id||'未知节点'} · ${n.executor_instance||''} · ${label(n.state)} / ${label(n.availability)}${heartbeat}`;
document.getElementById('current').textContent=c?`${label(c.state)} · ${c.profile||'未知工具'} · 开始于 ${stamp(c.started_at)}${elapsed(c.started_at)}${c.counted?'':' · 尚未计入执行次数'}`:'当前没有分配中的任务';
set('total',m.total_started);set('success',m.succeeded);set('failed',m.failed);set('progress',m.in_progress);set('waiting',m.awaiting_user);set('unknown',m.unknown);set('canceled',m.canceled);
const list=document.getElementById('recent');list.replaceChildren();for(const item of data.recent_executions||[]){const li=document.createElement('li');const reason=item.failure_code?` · ${item.failure_code}`:'';li.textContent=`${label(item.state)} · ${item.profile||'未知工具'} · ${stamp(item.started_at)}${reason}`;list.append(li)}if(!list.children.length){const li=document.createElement('li');li.textContent='尚无记录';list.append(li)}document.getElementById('tracking').textContent=`统计启用时间：${stamp(data.tracking_since)}`;document.getElementById('updated').textContent=`最近更新：${new Date().toLocaleTimeString()}`}
let busy=false;async function refresh(){if(busy)return;busy=true;try{const r=await fetch('/api/status',{cache:'no-store',credentials:'same-origin'});if(!r.ok)throw Error();render(await r.json())}catch{document.getElementById('current').textContent='状态暂不可用，节点生成进程会继续运行。下方数据为上次成功读取。';document.getElementById('updated').textContent=`读取失败：${new Date().toLocaleTimeString()}`}finally{busy=false}}
document.getElementById('refresh').addEventListener('click',refresh);refresh();setInterval(refresh,5000);
</script></body></html>"""


class _LocalStatusServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def start_local_status_server(node_client, port=8766):
    """Bind only to IPv4 loopback; a bind failure must not stop the node worker."""
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("Local status port must be between 0 and 65535")

    class Handler(BaseHTTPRequestHandler):
        server_version = ""
        sys_version = ""

        def log_message(self, format, *args):
            return

        def _send(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            port_value = self.server.server_port
            expected_host = f"127.0.0.1:{port_value}"
            if self.headers.get("Host", "").lower() != expected_host:
                self._send(403, b"Forbidden", "text/plain; charset=utf-8")
                return
            path = urlsplit(self.path)
            if path.query or path.fragment:
                self._send(404, b"Not found", "text/plain; charset=utf-8")
                return
            if path.path in {"/", "/index.html"}:
                self._send(200, _PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path.path != "/api/status":
                self._send(404, b"Not found", "text/plain; charset=utf-8")
                return
            origin = self.headers.get("Origin")
            expected_origin = f"http://127.0.0.1:{port_value}"
            if origin is not None and origin != expected_origin:
                self._send(403, b"Forbidden", "text/plain; charset=utf-8")
                return
            try:
                value = node_client.status()
                body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if len(body) > 65_536:
                    raise ValueError("status too large")
            except Exception:
                body = b'{"status":"unavailable"}'
                self._send(503, body, "application/json; charset=utf-8")
                return
            self._send(200, body, "application/json; charset=utf-8")

    server = _LocalStatusServer(("127.0.0.1", port), Handler)
    server_thread = threading.Thread(target=server.serve_forever,
                                     name="shared-node-local-status", daemon=True)
    server_thread.start()
    return server, server_thread, f"http://127.0.0.1:{server.server_port}/"


def stop_local_status_server(server, server_thread):
    if server is None:
        return
    server.shutdown()
    server.server_close()
    if server_thread is not None:
        server_thread.join(timeout=2)
