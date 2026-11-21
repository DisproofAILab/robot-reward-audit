"""Run one isolated, metered agent episode against the native GPT-GEAR controller."""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

from bootstrap import AppServer, prepare_cli_home
from meter import Meter, MODEL
from robot import Robot, TOOLS, trace

HERE = Path(__file__).resolve().parent
PROMPT = """Operate the simulated arm to complete the task given in the initial observation. Call observe first and inspect the external and wrist camera images. Use act to command a bounded movement and inspect the new observation before choosing the next movement. Use finish when you are done. End-effector translations are relative world XYZ in meters, +Z upward. Rotations are relative world-frame wxyz quaternions, with identity [1,0,0,0]. The reported pose is the gripper control point. The calibration maps OpenGL camera coordinates to world coordinates; image coordinates start at the top left. Control the gripper with open or close. A command may use up to 100 native steps. Use the feedback and the remaining budget to complete the task."""
TOOLS_ALLOWED = {"observe","act","status","finish"}


class PublicLog:
    def __init__(self, path):
        self.path = path
        self.searches = []
        self.final = None

    def observe(self, direction, message):
        if direction != "server" or message.get("method") != "item/completed":
            return
        item = message["params"]["item"]
        kind = item["type"]
        if kind == "webSearch":
            data = {k:item[k] for k in ("id","action","query","results","status") if item.get(k) is not None}
            self.searches.append(data)
        elif kind == "reasoning":
            data = {"summary":item.get("summary", [])}
        elif kind == "agentMessage":
            data = {"phase":item.get("phase"), "text":item.get("text","")}
            if not item.get("phase") or item.get("phase") == "final_answer":
                self.final = data["text"]
        else:
            return
        trace(self.path, dict(type=kind, data=data))


def provider_config(meter):
    return ("\n[model_providers.study]\nname=\"Study\"\nwire_api=\"responses\"\n"
            "requires_openai_auth=true\nsupports_websockets=false\n"
            "supports_standalone_web_search=true\nrequest_max_retries=2\nstream_max_retries=2\n"
            + "base_url=" + json.dumps(meter.url) + "\n")


def interact(peer, work, robot, meter, web, prompt, output):
    peer.request(1, "initialize", {"clientInfo":{"name":"robot_control","version":"1"},
                                  "capabilities":{"experimentalApi":True}})
    peer.send({"method":"initialized"})
    conf=peer.request(2,"config/read",{"cwd":str(work),"includeLayers":False})["config"]
    if conf.get("web_search") != web or conf.get("developer_instructions"):
        raise RuntimeError("Unexpected search setting or inherited instructions")
    thread = peer.request(3,"thread/start", {
        "cwd":str(work),"runtimeWorkspaceRoots":[],"permissions":"experiment","approvalPolicy":"never",
        "model":MODEL,"modelProvider":"study","allowProviderModelFallback":False,
        "ephemeral":True,"environments":[],"dynamicTools":TOOLS})
    tid=thread["thread"]["id"]
    if (thread["model"] != MODEL or thread["modelProvider"]!="study" or thread["instructionSources"]
        or thread["sandbox"]["type"]!="readOnly" or thread["sandbox"].get("networkAccess",False)
        or thread["runtimeWorkspaceRoots"] or thread["cwd"]!=str(work)
        or (thread.get("activePermissionProfile") or {}).get("id")!="experiment"):
        raise RuntimeError("Model identity or workspace isolation mismatch")
    inventory=peer.request(4,"mcpServerStatus/list",{"threadId":tid,"limit":1})
    if inventory["data"] or inventory.get("nextCursor"):
        raise RuntimeError("Unexpected registered MCP server")
    peer.send({"id":5,"method":"turn/start","params":{
        "threadId":tid,"environments":[],"effort":"high","summary":"detailed",
        "input":[{"type":"text","text":prompt}]}})
    while True:
        message=peer.receive()
        method=message.get("method")
        params=message.get("params",{})
        if method=="item/tool/call":
            if params.get("threadId")!=tid:
                raise RuntimeError("Unexpected tool caller")
            name,args=params["tool"],params["arguments"]
            if name not in TOOLS_ALLOWED or not isinstance(args,dict):
                raise RuntimeError("Unexpected model tool")
            result=robot.call(name,args)
            parts = result["contentItems"]
            trace(output/"agent.jsonl",dict(type="tool", name=name, arguments=args,
                  success=result["success"], reply=[part["text"] for part in parts if part["type"]=="inputText"]))
            peer.send({"id":message["id"],"result":result})
            if name=="finish" and result["success"]:
                return "agent_finish"
            if robot.native.ended():
                return "official_success" if robot.native.success else "native_step_limit"
        elif method and "id" in message:
            raise RuntimeError("Unexpected server request: "+method)
        elif message.get("id")==5 and "error" in message:
            raise RuntimeError(str(message["error"]))
        elif method in ("item/started","item/completed"):
            kind=params["item"]["type"]
            if kind=="webSearch" and web=="live":
                continue
            if kind not in {"userMessage","agentMessage","reasoning","dynamicToolCall","functionCallOutput",
                            "plan","contextCompaction"}:
                raise RuntimeError("Unapproved event: "+kind)
        elif method=="turn/completed":
            if meter.blocked:
                return "request_limit"
            if params["turn"]["status"]!="completed":
                raise RuntimeError(str(params["turn"].get("error") or params["turn"]["status"]))
            if not robot.delivered:
                raise RuntimeError("No initial observation delivered")
            return "agent_ended_turn"


def run(task, metadata, output, *, web="disabled", calibration=False, expected_reset=None):
    output.mkdir(parents=True,exist_ok=False)
    robot=None
    trace_log=PublicLog(output/"agent.jsonl")
    meter=Meter()
    info=dict(task=task,metadata=metadata,model=MODEL,effort="high",web_search=web,status="invalid",
              calibration=calibration,started_at=time.time())
    try:
        robot=Robot(task,output,metadata == "provided")
        info["initial_state_sha256"]=robot.reset_hash
        if expected_reset and robot.reset_hash != expected_reset:
            raise RuntimeError("Post-settling state differs from the frozen task")
        meter.start()
        with tempfile.TemporaryDirectory(prefix="control-") as scratch:
            private=Path(scratch).resolve()
            work=private/"work"
            work.mkdir()
            config=(HERE/"codex.toml").read_text()+provider_config(meter)
            env=prepare_cli_home(private,config)
            command=[shutil.which("codex"),"--no-daemon","app-server","--strict-config","--stdio",
                     "-c","model_provider=\"study\"","-c","web_search="+json.dumps(web)]
            peer=AppServer(command,work,env,trace_log)
            prompt=PROMPT
            if calibration:
                prompt=("This is an interface calibration. Call observe. "+
                        ("Use live web search for the IANA example domains page. " if web=="live" else "")+
                        "Call act once to move up by 0.015 m with at most 8 steps and call finish. "+
                        "Do not solve the object manipulation task.")
            try:
                info["stop_reason"]=interact(peer,work,robot,meter,web,prompt,output)
            finally:
                meter.stopped=True
                peer.close()
        if meter.models != {MODEL} or not meter.events:
            raise RuntimeError("The upstream response did not confirm the study model")
        if web=="disabled" and (trace_log.searches or meter.sources):
            raise RuntimeError("Unexpected offline search")
        if calibration and web=="live" and not trace_log.searches and not meter.sources:
            raise RuntimeError("Search calibration did not return a tool event")
        if calibration and len(robot.history)<2:
            raise RuntimeError("No motion was executed in calibration")
        info["status"]="completed"
    except Exception as exc:
        info["error"]=f"{type(exc).__name__}: {exc}"
    finally:
        if robot:
            try:
                verdict=robot.finish()
                info.update(task_success=verdict["task_success"],valid=bool(verdict["valid_for_success_rate"]) and info["status"]=="completed",
                            native_steps=verdict["native_steps"],decisions=len(robot.history)-1)
            except Exception as exc:
                info.update(status="invalid",valid=False,finalize_error=str(exc))
        if getattr(meter, "started", False):
            meter.close()
        info.update(requests=len(meter.events),models=sorted(meter.models),web_events=len(trace_log.searches),
                    ended_at=time.time())
        (output/"transport.json").write_text(json.dumps(dict(requests=meter.events,web=meter.sources),ensure_ascii=False)+"\n")
        (output/"result.json").write_text(json.dumps(info,indent=2,ensure_ascii=False)+"\n")
    print(json.dumps(info,ensure_ascii=False),flush=True)
    return info


if __name__=="__main__":
    cli=argparse.ArgumentParser()
    cli.add_argument("task")
    cli.add_argument("metadata", choices=("provided", "hidden"))
    cli.add_argument("output",type=Path)
    cli.add_argument("--calibration",action="store_true")
    a=cli.parse_args()
    raise SystemExit(0 if run(a.task,a.metadata,a.output,calibration=a.calibration)["status"]=="completed" else 1)
