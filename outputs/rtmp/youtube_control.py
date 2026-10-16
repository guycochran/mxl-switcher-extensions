#!/usr/bin/env python3
"""Loopback-only control surface for a 4K multi-view YouTube output."""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import re
import secrets
import signal
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from rtmps_output import Output, destination_url, parser as output_parser
from youtube_8up_canvas import Input


FLOW_LABEL = re.compile(r"^ZoomISO Cloud Instance Output (\d+) Video$")
MAX_REQUEST_BYTES = 8192


def discover_zoomiso_video(
    domain: pathlib.Path,
    active_seconds: float = 30.0,
    preferred_flow_ids: set[str] | None = None,
) -> list[Input]:
    """Return one freshest active ZoomISO flow per numbered output."""
    now = time.time()
    found: list[tuple[int, str, Input, float]] = []
    for definition in domain.glob("*.mxl-flow/flow_def.json"):
        try:
            value = json.loads(definition.read_text(encoding="utf-8"))
            match = FLOW_LABEL.match(str(value.get("label", "")))
            data = definition.parent / "data"
            if not match or value.get("media_type") != "video/v210" or not data.exists():
                continue
            mtime = data.stat().st_mtime
            if now - mtime > active_seconds:
                continue
            flow_id = str(value["id"])
            if preferred_flow_ids and flow_id not in preferred_flow_ids:
                continue
            device_id = str(value.get("device_id", ""))
            number = int(match.group(1))
            found.append((number, device_id, Input(f"ZOOM {number}", flow_id), mtime))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
    found.sort(key=lambda item: (item[0], -item[3], item[1], item[2].flow_id))
    unique: dict[int, Input] = {}
    for number, _device_id, source, _mtime in found:
        unique.setdefault(number, source)
    return [unique[number] for number in sorted(unique)]


class Controller:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.lock = threading.RLock()
        self.csrf = secrets.token_urlsafe(32)
        self.output: Output | None = None
        self.worker: threading.Thread | None = None
        self.server_url = args.server_url
        self.configured = False
        self.last_action = "Waiting for destination"

    def sources(self) -> list[Input]:
        return discover_zoomiso_video(
            pathlib.Path(self.args.domain),
            self.args.active_seconds,
            set(self.args.preferred_flow or ()),
        )

    def snapshot(self) -> dict[str, Any]:
        sources = self.sources()
        encoder_label = (
            "H.264 High / x264 CPU / 30,000 Kbps CBR"
            if self.args.video_encoder == "x264enc"
            else "H.264 High / NVENC / 30,000 Kbps CBR"
        )
        with self.lock:
            output = None if self.output is None else self.output.snapshot()
            running = self.worker is not None and self.worker.is_alive()
            return {
                "service": f"YouTube 4K {self.args.source_count}-up",
                "configured": self.configured,
                "running": running,
                "ready_to_start": len(sources) >= self.args.source_count and self.configured and not running,
                "source_count": len(sources),
                "sources": [source.label for source in sources[:self.args.source_count]],
                "required_source_count": self.args.source_count,
                "audio_ready": bool(self.args.audio_flow),
                "server_url": self.server_url,
                "profile": {
                    "canvas": "3840x2160p30",
                    "layout": "2x2 native 1080p sources" if self.args.source_count == 4 else "8 sources + status tile",
                    "video": encoder_label,
                    "audio": "Stereo AAC / 128 Kbps / 44.1 kHz",
                    "transport": "RTMPS",
                },
                "last_action": self.last_action,
                "output": output,
            }

    def configure_and_start(self, server_url: str, stream_key: str) -> None:
        destination_url(server_url, stream_key)
        sources = self.sources()
        if len(sources) < self.args.source_count:
            raise ValueError(
                f"{self.args.source_count} live ZoomISO video outputs are required; found {len(sources)}"
            )
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                raise ValueError("output is already running")
            run_args = argparse.Namespace(**vars(self.args))
            run_args.input = sources[:self.args.source_count]
            run_args.video_flow = None
            run_args.server_url = server_url
            self.server_url = server_url
            self.configured = True
            self.output = Output(run_args, stream_key)
            self.worker = threading.Thread(target=self.output.run, name="youtube-output", daemon=True)
            self.worker.start()
            self.last_action = "Stream start requested"

    def stop(self, forget: bool = False) -> None:
        with self.lock:
            if self.output is not None:
                self.output.stop_event.set()
            self.last_action = "Stream stopped"
            if forget:
                self.configured = False
                self.server_url = self.args.server_url
                self.output = None
                self.last_action = "Stream stopped and key forgotten"


def page(csrf: str, source_count: int, video_encoder: str = "nvh264enc") -> bytes:
    escaped = html.escape(csrf, quote=True)
    encoder_label = "x264 CPU H.264 High" if video_encoder == "x264enc" else "NVENC H.264 High"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="{escaped}"><title>MXL YouTube 4K</title>
<style>
:root{{--bg:#071018;--panel:#101d28;--line:#263947;--text:#edf7fb;--muted:#90a7b5;--cyan:#26d9e9;--green:#49e58a;--amber:#ffc857;--red:#ff667a}}
*{{box-sizing:border-box}} body{{margin:0;background:radial-gradient(circle at 80% -10%,#16445b 0,transparent 37%),var(--bg);color:var(--text);font:15px/1.45 system-ui,-apple-system,sans-serif}}
.shell{{max-width:1180px;margin:0 auto;padding:34px}} header{{display:flex;align-items:end;justify-content:space-between;margin-bottom:24px}} h1{{font-size:34px;line-height:1;margin:0;letter-spacing:-1.2px}} .eyebrow{{color:var(--cyan);font-weight:800;letter-spacing:2px;font-size:12px}} .tag{{color:var(--muted)}}
.grid{{display:grid;grid-template-columns:1.15fr .85fr;gap:18px}} .card{{background:color-mix(in srgb,var(--panel) 94%,transparent);border:1px solid var(--line);border-radius:16px;padding:22px;box-shadow:0 18px 50px #0006}} h2{{font-size:17px;margin:0 0 18px}}
.meter{{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin-bottom:18px}} .tile{{border:1px solid var(--line);border-radius:10px;padding:11px;background:#09141d}} .tile b{{display:block;font-size:22px}} .tile span{{color:var(--muted);font-size:12px}}
label{{display:block;color:var(--muted);font-size:12px;font-weight:700;margin:13px 0 6px}} input{{width:100%;padding:13px 14px;border-radius:9px;border:1px solid var(--line);background:#071018;color:var(--text);font:inherit;outline:none}} input:focus{{border-color:var(--cyan);box-shadow:0 0 0 3px #26d9e922}}
.buttons{{display:flex;gap:10px;margin-top:18px}} button{{border:0;border-radius:9px;padding:12px 17px;font-weight:800;cursor:pointer}} .go{{background:var(--cyan);color:#031116;flex:1}} .stop{{background:#5a2430;color:#fff}} .forget{{background:#263744;color:#fff}}
.status{{padding:13px;border-radius:10px;background:#09141d;border:1px solid var(--line);margin-bottom:15px}} .dot{{display:inline-block;width:10px;height:10px;border-radius:50%;background:var(--amber);margin-right:8px}} .dot.live{{background:var(--green);box-shadow:0 0 12px var(--green)}} .dot.bad{{background:var(--red)}}
.sources{{display:grid;grid-template-columns:1fr 1fr;gap:8px}} .source{{border:1px solid var(--line);background:#09141d;border-radius:8px;padding:10px;color:var(--muted)}} .source.ok{{color:var(--text);border-color:#245d48}} .spec{{display:grid;gap:11px}} .spec div{{display:flex;justify-content:space-between;border-bottom:1px solid var(--line);padding-bottom:9px}} .spec span{{color:var(--muted)}} .note{{font-size:12px;color:var(--muted);margin-top:14px}} #message{{min-height:22px;color:var(--amber);margin-top:12px}}
@media(max-width:800px){{.grid{{grid-template-columns:1fr}}.meter{{grid-template-columns:1fr 1fr}}.shell{{padding:20px}}}}
</style></head><body><main class="shell"><header><div><div class="eyebrow">MXL SWITCHER CORE</div><h1>YouTube 4K Control</h1></div><div class="tag">{source_count}-up · 2160p30 · secure RTMPS</div></header>
<div class="grid"><section class="card"><h2>Destination & launch</h2><div class="status"><span id="dot" class="dot"></span><b id="state">Checking system…</b><div id="detail" class="tag"></div></div>
<form id="launch"><label for="url">YouTube RTMPS server URL</label><input id="url" autocomplete="off" spellcheck="false" value="rtmps://a.rtmps.youtube.com/live2">
<label for="key">Stream key</label><input id="key" type="password" autocomplete="off" placeholder="Paste key — it is kept only in memory" required>
<div class="buttons"><button class="go" id="start" type="submit">Start stream</button><button class="stop" id="stop" type="button">Stop</button><button class="forget" id="forget" type="button">Stop + forget key</button></div></form><div id="message"></div>
<p class="note">Set the broadcast to <b>Unlisted</b> in YouTube Studio before starting. This page is reachable only through the local SSH tunnel. The key is never returned by the API, printed, logged, placed in a command, or saved to disk.</p></section>
<aside class="card"><h2>Transmission profile</h2><div class="meter"><div class="tile"><b id="count">0/{source_count}</b><span>live sources</span></div><div class="tile"><b>4K</b><span>3840×2160</span></div><div class="tile"><b>30</b><span>frames/sec</span></div><div class="tile"><b>30M</b><span>video bitrate</span></div></div><div id="sources" class="sources"></div><h2 style="margin-top:22px">Signal chain</h2><div class="spec"><div><span>Video</span><b>{encoder_label}</b></div><div><span>Audio</span><b>AAC stereo · 128K</b></div><div><span>GOP</span><b>2 seconds</b></div><div><span>Transport</span><b>RTMPS</b></div></div></aside></div></main>
<script>
const csrf=document.querySelector('meta[name=csrf-token]').content,msg=document.getElementById('message');
async function api(path,body){{const r=await fetch(path,{{method:body?'POST':'GET',headers:body?{{'content-type':'application/json','x-csrf-token':csrf}}:{{}},body:body?JSON.stringify(body):undefined,cache:'no-store'}});const j=await r.json();if(!r.ok)throw Error(j.error||'Request failed');return j}}
async function refresh(){{try{{const s=await api('/api/state'),required=s.required_source_count;document.getElementById('count').textContent=`${{Math.min(s.source_count,required)}}/${{required}}`;document.getElementById('url').value=s.server_url;const dot=document.getElementById('dot'),state=document.getElementById('state'),detail=document.getElementById('detail');dot.className='dot'+(s.output?.ready?' live':s.running?'':' bad');state.textContent=s.output?.ready?'LIVE — video and audio advancing':s.running?'Connecting to YouTube':s.source_count>=required?'Ready for destination':`Waiting for ${{required}} live ZoomISO outputs`;detail.textContent=s.last_action;document.getElementById('start').disabled=s.running||s.source_count<required;const box=document.getElementById('sources');box.innerHTML='';for(let i=0;i<required;i++){{const d=document.createElement('div');d.className='source'+(i<s.sources.length?' ok':'');d.textContent=s.sources[i]||`INPUT ${{i+1}} — waiting`;box.appendChild(d)}}}}catch(e){{msg.textContent=e.message}}}}
document.getElementById('launch').onsubmit=async e=>{{e.preventDefault();msg.textContent='Starting…';try{{await api('/api/start',{{server_url:document.getElementById('url').value,stream_key:document.getElementById('key').value}});document.getElementById('key').value='';msg.textContent='Start requested. YouTube Studio should receive the signal shortly.';refresh()}}catch(e){{msg.textContent=e.message}}}};
document.getElementById('stop').onclick=async()=>{{try{{await api('/api/stop',{{forget:false}});msg.textContent='Stop requested.';refresh()}}catch(e){{msg.textContent=e.message}}}};
document.getElementById('forget').onclick=async()=>{{try{{await api('/api/stop',{{forget:true}});document.getElementById('key').value='';msg.textContent='Stopped. The in-memory key was forgotten.';refresh()}}catch(e){{msg.textContent=e.message}}}};
refresh();setInterval(refresh,2000);
</script></body></html>""".encode("utf-8")


def handler_for(controller: Controller):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, value: object) -> None:
            body = json.dumps(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/":
                body = page(
                    controller.csrf,
                    controller.args.source_count,
                    controller.args.video_encoder,
                )
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Frame-Options", "DENY")
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/state":
                self._json(HTTPStatus.OK, controller.snapshot())
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            if not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), controller.csrf):
                self._json(HTTPStatus.FORBIDDEN, {"error": "invalid request token"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 2 or length > MAX_REQUEST_BYTES:
                    raise ValueError("invalid request size")
                value = json.loads(self.rfile.read(length))
                if self.path == "/api/start":
                    controller.configure_and_start(str(value["server_url"]), str(value["stream_key"]))
                elif self.path == "/api/stop":
                    controller.stop(bool(value.get("forget", False)))
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                self._json(HTTPStatus.OK, {"ok": True})
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


def parser() -> argparse.ArgumentParser:
    result = output_parser()
    result.description = __doc__
    result.set_defaults(stream_key_file=None, port=18121)
    result.add_argument("--active-seconds", type=float, default=30.0)
    result.add_argument("--source-count", type=int, choices=(4, 8), default=4)
    result.add_argument(
        "--preferred-flow", action="append",
        help="restrict automatic discovery to an explicitly selected ZoomISO video flow",
    )
    result.add_argument(
        "--container-bind", action="store_true",
        help="allow 0.0.0.0 inside a container whose port is published on host loopback only",
    )
    for action in result._actions:
        if action.dest == "stream_key_file":
            action.required = False
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    loopback = args.bind in {"127.0.0.1", "::1", "localhost"}
    if not loopback and not (args.container_bind and args.bind == "0.0.0.0"):
        raise SystemExit("refusing non-loopback control bind")
    controller = Controller(args)
    server = ThreadingHTTPServer((args.bind, args.port), handler_for(controller))

    def stop(*_args: object) -> None:
        controller.stop(forget=True)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({"event": "control-ready", "bind": args.bind, "port": args.port}), flush=True)
    try:
        server.serve_forever()
    finally:
        controller.stop(forget=True)
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
