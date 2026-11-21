"""Run the frozen pairs of episodes in their original order."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = Path(os.environ.get("AUDIT_OUTPUT", ROOT / "runs")).resolve() / "sim"


def build_plan(settings):
    rows = []
    for scene in settings["tasks"]:
        for metadata in ("provided", "hidden"):
            for pair, slot in enumerate(scene["slots"][metadata], 1):
                rows.append(dict(slot=slot, task=scene["task"], metadata=metadata, pair=pair))
    rows.sort(key=lambda row: int(row["slot"]))
    if [row["slot"] for row in rows] != [f"{i:03d}" for i in range(1, 49)]:
        raise ValueError("The schedule must assign each of the 48 slots once")
    return rows


def check_scenes(settings, scenes):
    from environments.libero_plus.native import LiberoNative

    for scene in scenes:
        suite, index = scene["task"].split("/")
        args = argparse.Namespace(suite=suite, task_id=int(index), seed=settings["seed"],
                                  init_state_index=settings["init_state_index"], image_size=256,
                                  max_native_steps=settings["policy_steps"] + settings["settling_steps"],
                                  save_video=False, output=RESULTS)
        native = LiberoNative(args)
        try:
            digest = hashlib.sha256(native.env.get_sim_state().tobytes()).hexdigest()
            assert digest == scene["post_reset_sha256"]
            for name, view in native.observe()["views"].items():
                rgb = view["rgb"]
                assert rgb.shape == (256, 256, 3)
                if rgb.max() == rgb.min():
                    raise ValueError(f"{scene['task']} returned a blank {name} view")
        finally:
            native.close()
        print("Verified", scene["task"], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--check-scenes", action="store_true")
    parser.add_argument("--task", help="Run only the eight scheduled slots for this task ID")
    args = parser.parse_args()
    settings = json.loads((ROOT / "experiments/libero-plus.json").read_text())
    scenes = [s for s in settings["tasks"] if args.task is None or s["task"] == args.task]
    if not scenes:
        parser.error(f"Task is absent from the frozen schedule: {args.task}")
    slots = build_plan(settings)
    RESULTS.mkdir(parents=True, exist_ok=True)
    plan_file = RESULTS / "plan.json"
    if not plan_file.exists():
        plan_file.write_text(json.dumps(slots, indent=2) + "\n")
    if json.loads(plan_file.read_text()) != slots:
        raise ValueError("Output directory contains a different schedule")
    if args.plan_only:
        for slot in slots:
            if args.task is None or slot["task"] == args.task:
                print(slot["slot"], slot["task"], slot["metadata"], slot["pair"])
        return
    if args.check_scenes:
        check_scenes(settings, scenes)
        return

    from simulate import run

    states = {scene["task"]: scene["post_reset_sha256"] for scene in scenes}
    with (RESULTS / ".running.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for slot in slots:
            if slot["task"] not in states:
                continue
            folder = RESULTS / slot["slot"]
            if (folder / "result.json").is_file():
                continue
            if folder.exists():
                raise RuntimeError(f"Incomplete occupied slot {folder.name}. Inspect before resuming.")
            result = run(slot["task"], slot["metadata"], folder,
                         web=settings["pair_tool_settings"][slot["pair"] - 1],
                         expected_reset=states[slot["task"]])
            if result["status"] != "completed":
                raise RuntimeError(f"Infrastructure failure in slot {folder.name}")


if __name__ == "__main__":
    main()
