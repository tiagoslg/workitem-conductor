"""On-demand localhost web server for the read-only dashboard.

Stdlib ``http.server`` only — no web framework, consistent with the project's
minimal-deps posture. Serves one self-contained HTML page and a small JSON
endpoint backed by :func:`scan.collect`. Bound to loopback; read-only. Sharing
beyond localhost would require auth + transport design (not built).
"""

from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..workspaces import WorkspaceRegistry
from .scan import collect

INDEX_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>conductor dashboard</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; margin: 0; padding: 1.5rem;
         background: #0f1115; color: #e6e6e6; }
  h1 { font-size: 1.2rem; margin: 0 0 .25rem; }
  .meta { color: #8a8f98; font-size: .8rem; margin-bottom: 1.25rem; }
  .project { background: #171a21; border: 1px solid #242833; border-radius: 10px;
             padding: 1rem 1.25rem; margin-bottom: 1rem; }
  .project h2 { font-size: 1rem; margin: 0 0 .5rem; }
  .project .path { color: #6b7280; font-weight: 400; font-size: .78rem; }
  .empty { color: #6b7280; font-style: italic; }
  .err { color: #e0a458; }
  table { width: 100%; border-collapse: collapse; font-size: .85rem; }
  th { text-align: left; color: #8a8f98; font-weight: 600; padding: .3rem .5rem;
       border-bottom: 1px solid #242833; }
  td { padding: .4rem .5rem; border-bottom: 1px solid #1d212b; vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  .chip { display: inline-block; padding: .1rem .5rem; border-radius: 999px;
          font-size: .72rem; font-weight: 600; }
  .s-completed { background:#16351f; color:#7ee787; }
  .s-running   { background:#16263a; color:#79c0ff; }
  .s-ready     { background:#1d2b3a; color:#9ecbff; }
  .s-draft     { background:#2a2d35; color:#adb3bd; }
  .s-needs_human, .s-blocked { background:#3a1d1d; color:#ff9492; }
  .active { color:#d2a8ff; font-weight:700; }
  .issues { color:#e0a458; font-size:.78rem; }
  .id { font-family: ui-monospace, monospace; font-size:.8rem; color:#c9d1d9; }
</style>
</head>
<body>
<h1>conductor dashboard</h1>
<div class="meta" id="meta">loading…</div>
<div id="projects"></div>
<script>
function chip(status){ return '<span class="chip s-'+status+'">'+status+'</span>'; }
function render(data){
  document.getElementById('meta').textContent =
    'workspace: ' + data.workspace + ' · ' + data.projects.length +
    ' project(s) · updated ' + data.generated;
  const root = document.getElementById('projects');
  root.innerHTML = '';
  for(const p of data.projects){
    const div = document.createElement('div');
    div.className = 'project';
    let html = '<h2>'+p.name+' <span class="path">'+p.path+'</span></h2>';
    if(p.error){
      html += '<div class="err">'+p.error+'</div>';
    } else if(p.workitems.length === 0){
      html += '<div class="empty">no workitems yet</div>';
    } else {
      html += '<table><thead><tr><th></th><th>workitem</th><th>stage</th>'
            + '<th>status</th><th>next</th><th>issues</th><th>updated</th></tr></thead><tbody>';
      for(const w of p.workitems){
        const star = w.active ? '<span class="active">●</span>' : '';
        const issues = w.open_issues.length
          ? '<span class="issues">'+w.open_issues.length+' open</span>' : '';
        html += '<tr><td>'+star+'</td>'
              + '<td><span class="id">'+w.id+'</span><br>'+w.title+'</td>'
              + '<td>'+w.stage+'</td>'
              + '<td>'+chip(w.status)+'</td>'
              + '<td>'+w.next_action+'</td>'
              + '<td>'+issues+'</td>'
              + '<td>'+w.updated_at+'</td></tr>';
      }
      html += '</tbody></table>';
    }
    div.innerHTML = html;
    root.appendChild(div);
  }
}
async function refresh(){
  try {
    const r = await fetch('/api/state');
    render(await r.json());
  } catch(e){ document.getElementById('meta').textContent = 'error: ' + e; }
}
refresh();
setInterval(refresh, 4000);
</script>
</body>
</html>
"""


def build_handler(registry: WorkspaceRegistry, workspace: str | None):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
            if self.path in ("/", "/index.html"):
                self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path.startswith("/api/state"):
                payload = json.dumps(collect(registry, workspace)).encode("utf-8")
                self._send(200, payload, "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *args) -> None:  # silence default stderr logging
            pass

    return Handler


def serve(
    registry: WorkspaceRegistry,
    workspace: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8787,
    open_browser: bool = True,
) -> None:
    """Run the dashboard until interrupted. Falls back to an ephemeral port."""
    handler = build_handler(registry, workspace)
    try:
        httpd = ThreadingHTTPServer((host, port), handler)
    except OSError:
        httpd = ThreadingHTTPServer((host, 0), handler)  # port taken -> any free
    actual_port = httpd.server_address[1]
    url = f"http://{host}:{actual_port}/"
    print(f"conductor dashboard on {url}  (read-only · Ctrl-C to stop)")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ndashboard stopped")
    finally:
        httpd.server_close()
