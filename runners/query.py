"""Run the twelve frozen pairs in independent workspaces and retain every attempt."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(os.environ.get("AUDIT_OUTPUT", ROOT / "runs")).resolve()
INDEX = OUTPUT / "query/index.jsonl"


def main():
    cases = json.loads((ROOT / "experiments/questions.json").read_text())
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    with INDEX.open("a+", encoding="utf-8") as stream:
        import fcntl
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stream.seek(0)
        completed = {(r["case"], r["condition"]) for r in map(json.loads, stream) if r["status"] == "completed"}
        for case in cases:
            for mode in ("offline", "web"):
                if (case["id"], mode) in completed:
                    continue
                args = [sys.executable, "-B", str(ROOT / "runners/bootstrap.py"),
                        "--task", case["task"], "--benchmark", case["benchmark"],
                        "--metadata", json.dumps(case["metadata"], ensure_ascii=False),
                        "--model", "gpt-6.1-sol", "--effort", "high", "--mode", mode]
                run = subprocess.run(args, capture_output=True, text=True,
                                     env={**os.environ, "AUDIT_SESSIONS":str(INDEX.parent)})
                session = next(Path(line[9:]) for line in run.stdout.splitlines() if line.startswith("Session: "))
                manifest = json.loads((session / "results/manifest.json").read_text())
                row = dict(case=case["id"], condition=mode, session=session.name, status=manifest["status"],
                           searches=len(manifest["web_actions"]))
                stream.write(json.dumps(row) + "\n")
                stream.flush()
                print(row, flush=True)
                if run.returncode:
                    raise RuntimeError(manifest.get("error", "incomplete session"))


if __name__ == "__main__":
    main()
