"""Present the same GPT-GEAR controller and public sensor channels in every arm."""
from argparse import Namespace
import base64
import hashlib
import json
from pathlib import Path
import time

from control.controller import DeltaController
from environments.libero_plus.native import LiberoNative
from execution.session import NativeClient
from perception.calibration import EpisodeCalibration
from PIL import Image

CAMERAS = {"agentview": "external", "robot0_eye_in_hand": "wrist"}
STATUS = ("success", "terminal", "decision_steps", "remaining_steps")
IDENTITY = ("task", "suite", "task_id", "task_name", "seed", "init_state_index")
ROBOT = ("ee_pose", "joint_positions", "joint_velocities", "gripper_joint_positions", "gripper_command")
CALIBRATION = ("shape", "intrinsic_K", "camera_to_env_gl", "camera_axes", "position_unit")
EMPTY = {"type":"object", "properties":{}, "additionalProperties":False}
VECTOR = {"type":"array","items":{"type":"number"},"minItems":3,"maxItems":3}


def function(name, description, properties=None, required=()):
    schema = dict(EMPTY, properties=properties or {}, required=list(required))
    return dict(type="function", name=name, description=description, inputSchema=schema)


TOOLS = [
    function("observe", "Read the current or a saved observation, with external and wrist RGB images.",
             {"observation_id":{"type":"integer","minimum":0}}),
    function("act", "Execute a bounded relative end-effector motion in environment coordinates and return both new views. Translation is in meters (norm <=0.20), relative rotation is a wxyz quaternion (angle <=pi/2).",
             {"observation_id":{"type":"integer","minimum":0}, "dpos":VECTOR,
              "dquat":{"type":"array","items":{"type":"number"},"minItems":4,"maxItems":4},
              "gripper":{"type":"string","enum":["open","close"]},
              "max_steps":{"type":"integer","minimum":1,"maximum":100}},
             ("observation_id","dpos","max_steps")),
    function("status", "Read current execution status and remaining native steps."),
    function("finish", "End this attempt and return its simulator verdict.",
             {"observation_id":{"type":"integer","minimum":0}}, ("observation_id",))
]


def trace(path, record):
    with path.open("a",encoding="utf-8") as f:
        f.write(json.dumps(dict(time=time.time(), **record), ensure_ascii=False, allow_nan=False)+"\n")


class Robot:
    def __init__(self, task, output, visible):
        self.output = Path(output)
        self.visible = visible
        suite, index = task.split("/")
        args = Namespace(suite=suite, task_id=int(index), seed=0, init_state_index=0,
                         max_native_steps=1210, image_size=256, save_video=True, output=self.output)
        self.native = LiberoNative(args)
        self.client = NativeClient(self.native)
        self.controller = DeltaController(self.client, lambda: None)
        self.calibration = EpisodeCalibration()
        self.reset_hash = hashlib.sha256(self.native.env.get_sim_state().tobytes()).hexdigest()
        self.history = []
        self.delivered = set()
        self.capture()

    def status(self, visible=None):
        status = self.native.status()
        result = {k:status[k] for k in STATUS}
        include_metadata = self.visible if visible is None else visible
        if include_metadata:
            result["task_metadata"] = {"benchmark":"LIBERO", **{k:status[k] for k in IDENTITY}}
        return result

    def capture(self, feedback=None):
        raw = self.native.observe()
        specs = self.calibration.view_specs(raw)
        n = len(self.history)
        view_specs = {}
        images = {}
        for original, label in CAMERAS.items():
            filename = f"{n:03}-{label}.png"
            Image.fromarray(raw["views"][original]["rgb"]).save(self.output/filename)
            images[label] = filename
            view_specs[label] = {k:specs[original][k] for k in CALIBRATION}
        record = dict(observation_id=n, instruction=raw["instruction"], cameras=view_specs,
                      robot={k:raw["robot_state"]["robot0_"+k] for k in ROBOT}, status=self.status())
        if feedback is not None:
            record["previous_action"] = feedback
        self.history.append((record,images))
        trace(self.output/"observations.jsonl",dict(observation=record, images=images))

    def observation(self, n):
        record,images=self.history[n]
        content=[{"type":"inputText","text":json.dumps(record,separators=(",",":"))}]
        for label,name in images.items():
            content.append({"type":"inputText","text":label+" RGB"})
            data=base64.b64encode((self.output/name).read_bytes()).decode("ascii")
            content.append({"type":"inputImage","imageUrl":"data:image/png;base64,"+data})
        self.delivered.add(n)
        return {"success":True,"contentItems":content}

    def call(self,name,args):
        latest=len(self.history)-1
        n=args.get("observation_id",latest)
        if type(n) is not int or not 0 <= n <= latest:
            return self.text("Unknown observation. Call observe.",False)
        if name=="observe":
            return self.observation(n)
        if name=="status":
            return self.text(self.status())
        if n!=latest or n not in self.delivered:
            return self.text("Inspect the current observation before acting.",False)
        if name=="finish":
            return self.text(self.status())
        if name!="act":
            return self.text("Unknown control tool.",False)
        motion = {k:args[k] for k in ("dpos","dquat","gripper","max_steps") if k in args}
        try:
            feedback=self.controller.act_delta(arms={"robot0":motion})
        except (KeyError,ValueError,TypeError):
            return self.text("Invalid motion. Check the tool schema and the action bounds.",False)
        if self.client.uncertain or not feedback.get("success"):
            raise RuntimeError("Uncertain native motion outcome")
        exposed={k:feedback[k] for k in ("completed","task_success","stop_reason","executed_steps") if k in feedback}
        self.capture({"command":motion, "feedback":exposed})
        return self.observation(len(self.history)-1)

    @staticmethod
    def text(value,success=True):
        return dict(success=success,contentItems=[dict(type="inputText", text=json.dumps(value,ensure_ascii=False))])

    def finish(self):
        try:
            self.native.finalize()
            return self.native.result()
        finally:
            self.native.close()
