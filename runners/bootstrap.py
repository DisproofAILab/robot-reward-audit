"""Prepare and record one benchmark question-and-answer session."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from queue import Queue
import shutil
from string import Template
import subprocess
import sys
import tempfile
from threading import Thread
import uuid

from transcript import Transcript


ROOT = Path(__file__).resolve().parent
QUESTION = "请使用 read_workspace_file 读取本轮工作区的 AGENTS.md，然后回答文件中的问题。"
FILE_TOOL = {
    "type": "function",
    "name": "read_workspace_file",
    "description": "读取本轮问答工作区中的文件。工作区内只有 AGENTS.md。",
    "inputSchema": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    },
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def prepare_workspace(task_name, metadata, benchmark):
    template = Template((ROOT.parent / "experiments/query-prompt.txt").read_text(encoding="utf-8"))
    task = {"task_name": task_name, "metadata": metadata}
    instructions = template.substitute(benchmark=benchmark, task_json=json.dumps(task, ensure_ascii=False, indent=2))
    sessions = Path(os.environ.get("AUDIT_SESSIONS", ROOT.parent / "runs/query"))
    sessions.mkdir(exist_ok=True, parents=True)
    session = sessions / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12])
    session.mkdir(mode=0o700)
    workspace = session / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text(instructions, encoding="utf-8")
    return workspace, instructions


def prepare_cli_home(private_dir, config):
    codex_home = private_dir / "codex"
    codex_home.mkdir(mode=0o700)
    (codex_home / "config.toml").write_text(config, encoding="utf-8")
    existing_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    credentials = existing_home / "auth.json"
    if credentials.is_file():
        (codex_home / "auth.json").symlink_to(credentials.resolve())
    # Keep host login/transport separate from tools. Do not inherit a daemon,
    # plugin catalog, provider override, thread ID, or the user's shell profile.
    env = {name: os.environ[name] for name in
           ("OPENAI_API_KEY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
            "SSL_CERT_FILE", "SSL_CERT_DIR", "LANG") if name in os.environ}
    env.update(HOME=str(private_dir), CODEX_HOME=str(codex_home), PATH=os.defpath, TMPDIR=str(private_dir))
    return env


class AppServer:
    """One stdio peer. Collect the session transcript in memory until exit."""

    def __init__(self, command, workspace, env, transcript):
        self.process = subprocess.Popen(command, cwd=workspace, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.transcript = transcript
        self.inbox = Queue()
        self.stderr = bytearray()
        self.stdout_reader = Thread(target=self.read_stdout)
        self.stderr_reader = Thread(target=lambda: self.stderr.extend(self.process.stderr.read()))
        self.stdout_reader.start()
        self.stderr_reader.start()

    def read_stdout(self):
        try:
            for line in self.process.stdout:
                self.inbox.put(line)
        finally:
            self.inbox.put(None)

    def send(self, message):
        line = json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
        self.process.stdin.write(line)
        self.process.stdin.flush()
        self.transcript.observe("client", message)

    def record_server_line(self, line):
        try:
            message = json.loads(line)
        except (ValueError, UnicodeError) as error:
            raise ValueError("Codex emitted an invalid JSONL line") from error
        self.transcript.observe("server", message)
        return message

    def receive(self):
        line = self.inbox.get()
        if line is None:
            raise RuntimeError("Codex closed stdout before completing the request")
        return self.record_server_line(line)

    def request(self, request_id, method, params):
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = self.receive()
            if "method" in message and "id" in message:
                raise RuntimeError("Unexpected server request during setup: " + message["method"])
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message["result"]

    def close(self):
        try:
            self.process.stdin.close()
        except BrokenPipeError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.stdout_reader.join()
        self.stderr_reader.join()
        while not self.inbox.empty():
            line = self.inbox.get()
            if line is not None:
                self.record_server_line(line)


def workspace_read(params, instructions):
    # Use the bootstrap snapshot. Model-provided strings never reach open().
    permitted = (params.get("tool") == FILE_TOOL["name"]
                 and params.get("arguments") == {"path": "AGENTS.md"})
    text = instructions if permitted else "访问被拒绝。本轮只提供 AGENTS.md。"
    return {"success": permitted, "contentItems": [{"type": "inputText", "text": text}]}


def conduct_session(peer, workspace, instructions, manifest):
    peer.request(1, "initialize", {
        "clientInfo": {"name": "benchmark_qa", "version": "1"},
        "capabilities": {"experimentalApi": True},
    })
    peer.send({"method": "initialized"})
    settings = peer.request(2, "config/read", {"cwd": str(workspace), "includeLayers": False})["config"]
    if settings.get("web_search") != manifest["web_search"]:
        raise RuntimeError("Codex did not confirm the requested web search setting")
    if settings.get("instructions") or settings.get("developer_instructions"):
        raise RuntimeError("Unexpected installation-specific instructions in the effective configuration")
    thread = peer.request(3, "thread/start", {
        "cwd": str(workspace), "runtimeWorkspaceRoots": [],
        "permissions": "experiment", "approvalPolicy": "never",
        "model": manifest["model"], "modelProvider": "openai", "allowProviderModelFallback": False,
        "ephemeral": True, "environments": [], "dynamicTools": [FILE_TOOL],
    })
    thread_id = thread["thread"]["id"]
    manifest["thread_id"] = thread_id
    if ((thread.get("activePermissionProfile") or {}).get("id") != "experiment"
            or thread["approvalPolicy"] != "never"
            or thread["cwd"] != str(workspace) or thread["runtimeWorkspaceRoots"] != []
            or thread["sandbox"].get("networkAccess", False)
            or thread["sandbox"]["type"] != "readOnly"
            or thread["instructionSources"] or thread["model"] != manifest["model"]
            or thread["modelProvider"] != "openai"):
        raise RuntimeError("Codex did not confirm the requested workspace permissions or instruction sources")
    inventory = peer.request(4, "mcpServerStatus/list", {"threadId": thread_id, "limit": 1})
    if inventory["data"] or inventory.get("nextCursor"):
        raise RuntimeError("An external MCP server is registered for this thread")

    peer.send({"id": 5, "method": "turn/start", "params": {
        "threadId": thread_id, "environments": [], "effort": manifest["effort"], "summary": "detailed",
        "input": [{"type": "text", "text": QUESTION}],
    }})
    while True:
        message = peer.receive()
        method = message.get("method")
        params = message.get("params", {})
        if method == "thread/settings/updated" and params["threadSettings"]["environments"] != []:
            raise RuntimeError("Codex attached an unexpected execution environment")
        if method in ("item/started", "item/completed"):
            item = params["item"]
            kind = item["type"]
            if kind == "webSearch" and manifest["web_search"] == "live":
                continue
            # A client can ask Codex to enumerate its already-verified empty
            # registry. This is a local discovery call, even in offline mode.
            if (kind == "mcpToolCall" and item.get("server") == "codex"
                    and item.get("tool") in ("list_mcp_resources", "list_mcp_resource_templates")
                    and not (item.get("arguments") or {}).get("server")):
                if method == "item/completed":
                    result = item.get("result") or {}
                    structured = result.get("structuredContent") or {}
                    if not isinstance(structured, dict) or any(structured.values()):
                        raise RuntimeError("Unexpected non-empty structured MCP discovery result")
                    for part in result.get("content", []):
                        listing = json.loads(part["text"])
                        if not isinstance(listing, dict) or any(listing.values()):
                            raise RuntimeError("Unexpected non-empty local MCP discovery result")
                    manifest.setdefault("local_discovery", []).append(item["tool"])
                continue
            if kind not in ("userMessage", "agentMessage", "reasoning", "dynamicToolCall",
                            "functionCallOutput", "plan", "contextCompaction"):
                detail = (str(item.get("server")) + "/" + str(item.get("tool"))) if kind == "mcpToolCall" else kind
                raise RuntimeError("Unexpected Codex activity in the isolated turn: " + detail)
        if "id" in message and method:
            if method != "item/tool/call" or params.get("threadId") != thread_id:
                raise RuntimeError("Unexpected server request: " + method)
            result = workspace_read(params, instructions)
            peer.send({"id": message["id"], "result": result})
            if result["success"]:
                manifest["task_delivered"] = True
        elif message.get("id") == 5:
            if "error" in message:
                raise RuntimeError(str(message["error"]))
            manifest["turn_id"] = message["result"]["turn"]["id"]
        elif method == "turn/completed" and params.get("threadId") == thread_id:
            turn = params["turn"]
            manifest["turn_id"] = turn["id"]
            if turn["status"] != "completed":
                raise RuntimeError(str(turn.get("error") or turn["status"]))
            if not manifest["task_delivered"]:
                raise RuntimeError("The model finished without retrieving the prepared task")
            return


def save_results(session, transcript, stderr, config, manifest):
    task_file = session / "workspace" / "AGENTS.md"
    workspace_text = task_file.read_text(encoding="utf-8") if task_file.is_file() else None
    markdown, final_answer = transcript.render(workspace_text)
    manifest["config_sha256"] = hashlib.sha256(config.encode("utf-8")).hexdigest()
    manifest["web_actions"] = [item.get("action") or {"query": item.get("query", "")}
                                for item in transcript.items.values() if item["type"] == "webSearch"]
    if manifest.get("web_search") == "disabled" and manifest["web_actions"]:
        manifest.update(status="failed", error="A web search event was observed in an offline session")
    returned = [{"id": item["id"], "results": item["results"]} for item in transcript.items.values()
                if item["type"] == "webSearch" and item.get("results")]
    if returned:
        manifest["web_results"] = returned
    if transcript.token_usage is not None:
        manifest["token_usage"] = transcript.token_usage
    if manifest["status"] == "completed" and not final_answer.strip():
        manifest.update(status="failed", error="No completed final answer was received")
    with tempfile.TemporaryDirectory(dir=session) as staging_dir:
        staging = Path(staging_dir)
        (staging / "transcript.md").write_text(markdown, encoding="utf-8")
        if stderr and manifest["status"] != "completed":
            (staging / "stderr.log").write_bytes(stderr)
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")
        staging.rename(session / "results")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, help="本轮提供给模型的任务名称")
    parser.add_argument("--benchmark", required=True, help="题面标注的基准和版本")
    parser.add_argument("--mode", choices=("offline", "web"), default="offline",
                        help="web 开放 Codex 托管网页检索，两种模式的本地环境均保持隔离")
    parser.add_argument("--metadata", default="{}", help="提供给模型的 JSON 对象，默认 {}")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max", "ultra"])
    args = parser.parse_args()
    try:
        metadata = json.loads(args.metadata)
    except ValueError as error:
        parser.error("Invalid metadata JSON: " + str(error))
    if not args.task.strip() or not isinstance(metadata, dict):
        parser.error("--task must be non-empty and --metadata must be a JSON object")
    codex = shutil.which("codex")
    if not codex:
        parser.error("codex was not found on PATH")
    version = subprocess.check_output([codex, "--version"], text=True).strip()
    config = (ROOT / "codex.toml").read_text(encoding="utf-8")
    workspace, instructions = prepare_workspace(args.task, metadata, args.benchmark)
    session = workspace.parent
    print("Session:", session, flush=True)
    search_setting = "live" if args.mode == "web" else "disabled"
    manifest = {"task": args.task, "benchmark": args.benchmark, "mode": args.mode, "web_search": search_setting,
                "metadata": metadata, "model": args.model, "effort": args.effort,
                "cli_version": version, "started_at": utc_now(), "task_delivered": False, "status": "failed"}
    transcript = Transcript()
    peer = None
    try:
        with tempfile.TemporaryDirectory(prefix="benchmark-qa-") as temp:
            env = prepare_cli_home(Path(temp), config)
            command = [codex, "--no-daemon", "app-server", "--strict-config", "--stdio",
                       "-c", 'web_search="' + search_setting + '"']
            peer = AppServer(command, workspace, env, transcript)
            try:
                conduct_session(peer, workspace, instructions, manifest)
                manifest["status"] = "completed"
            finally:
                peer.close()
                manifest["process_returncode"] = peer.process.returncode
            if peer.process.returncode:
                raise RuntimeError("Codex exited with status " + str(peer.process.returncode))
    except KeyboardInterrupt:
        manifest.update(status="interrupted", error="Interrupted by operator")
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        manifest.update(status="failed", error=str(error))
    manifest["ended_at"] = utc_now()
    save_results(session, transcript, peer.stderr if peer else b"", config, manifest)
    print(manifest["status"], session / "results", flush=True)
    if "error" in manifest:
        print(manifest["error"], file=sys.stderr)
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
