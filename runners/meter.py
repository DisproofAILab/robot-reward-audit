"""Count inference dispatches and retain only public evidence from the response stream."""
import http.client
import json
import secrets
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

MODEL = "gpt-6.1-sol"
ORIGIN = "chatgpt.com"
EXCLUDED = {"host", "connection", "transfer-encoding", "content-length", "accept-encoding", "upgrade", "proxy-authorization"}


class Meter:
    def __init__(self, limit=200):
        self.limit = limit
        self.events = []
        self.response_formats = []
        self.sources = []
        self.models = set()
        self.blocked = False
        self.stopped = False
        self.lock = threading.Lock()
        self.prefix = "/" + secrets.token_hex(20)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.meter = self
        self.url = f"http://127.0.0.1:{self.server.server_port}{self.prefix}"

    def start(self):
        self.started = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def reserve(self, route):
        with self.lock:
            if self.stopped:
                return None
            if len(self.events) >= self.limit:
                self.blocked = True
                return None
            event = {"n": len(self.events)+1, "route": route, "sent_at": datetime.now(timezone.utc).isoformat()}
            self.events.append(event)
            return event

    def evidence(self, line, request):
        if not line.startswith(b"data: "):
            return
        try:
            event = json.loads(line[6:])
        except (ValueError, UnicodeDecodeError):
            return
        if event.get("type") in ("error", "response.failed", "response.incomplete"):
            problem = event.get("error") or (event.get("response") or {}).get("error") or {}
            request["upstream_error"] = str(problem)[:400]
        response = event.get("response") or {}
        if response.get("model"):
            self.models.add(response["model"])
        if event.get("type") == "response.completed":
            request["completed"] = True
            usage = response.get("usage") or {}
            request["usage"] = {k: v for k,v in usage.items() if k in ("input_tokens","output_tokens","total_tokens","output_tokens_details")}
        item = event.get("item") or {}
        if event.get("type") == "response.output_item.done" and item.get("type") == "web_search_call":
            self.sources.append({"request": request["n"], "item": item})


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        meter = self.server.meter
        url = urlsplit(self.path)
        route = url.path.removeprefix(meter.prefix)
        if not url.path.startswith(meter.prefix + "/") or route not in ("/responses", "/responses/compact", "/compact"):
            self.send_error(404)
            return
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= 192 * 1024**2:
            self.send_error(411)
            return
        body = self.rfile.read(size)
        event = meter.reserve(route)
        if event is None:
            self.send_error(403, "Study request budget reached")
            return
        event["request_encoding"] = self.headers.get("Content-Encoding")
        if not event["request_encoding"]:
            request_json = json.loads(body)
            event["model"] = request_json.get("model")
            if event["n"] == 1:
                event["request_keys"] = sorted(request_json)
                event["reasoning_config"] = request_json.get("reasoning")
                event["input_types"] = sorted({i.get("type","") for i in request_json.get("input",[]) if isinstance(i,dict)})
            additions = [item for item in request_json.get("input", []) if isinstance(item,dict) and item.get("type")=="additional_tools"]
            tools = list(request_json.get("tools", []))
            for block in additions:
                tools.extend(block.get("tools", []))
            if event["n"] == 1 or additions:
                event["tool_inventory"] = [{k:t.get(k) for k in ("type","name","external_web_access") if k in t} for t in tools if isinstance(t,dict)]
                for summary, tool in zip(event["tool_inventory"], tools):
                    if isinstance(tool,dict) and tool.get("type")=="namespace":
                        summary["members"] = [t.get("name") for t in tool.get("tools",[]) if isinstance(t,dict)]
                event["additional_tool_keys"] = [list(item) for item in additions]
            if event["n"] == 1:
                first = json.dumps(request_json.get("input", []),ensure_ascii=False).lower()
                event["unexpected_source_labels"] = [x for x in ("libero","unicraft","robocasa","robot-reward-audit","robodojo","gpt-gear","/users/mac/phd/") if x in first]
                if event["unexpected_source_labels"]:
                    self.send_error(400,"Unexpected experimental identifier in initial model context")
                    return
            if event["model"] != MODEL:
                self.send_error(400, "Unexpected study model")
                return
        headers = {k:v for k,v in self.headers.items() if k.lower() not in EXCLUDED}
        headers["Accept-Encoding"] = "identity"
        upstream = http.client.HTTPSConnection(ORIGIN, timeout=420)
        try:
            path = "/backend-api/codex" + route
            upstream.request("POST", path, body, headers)
            reply = upstream.getresponse()
            event["http_status"] = reply.status
            event["response_type"] = reply.getheader("Content-Type")
            event["response_encoding"] = reply.getheader("Content-Encoding")
            meter.response_formats.append((event["response_type"],event["response_encoding"]))
            self.send_response(reply.status)
            for k, v in reply.getheaders():
                if k.lower() not in EXCLUDED:
                    self.send_header(k, v)
            self.end_headers()
            if route == "/responses" and reply.status == 200:
                for line in reply:
                    meter.evidence(line, event)
                    self.wfile.write(line)
                    self.wfile.flush()
            else:
                self.wfile.write(reply.read())
        except (OSError, http.client.HTTPException) as exc:
            event["transport_error"] = type(exc).__name__
            if "http_status" not in event:
                self.send_error(502, "Upstream model transport unavailable")
        finally:
            upstream.close()
