"""Build the data the blog's question grid and its figures read.

Reads the released question set, the graded answers and the episode ledger, writes
docs/assets/qa.json plus the small task table, and copies the media the page embeds.

    python3 runners/blog_data.py
"""
from collections import Counter
import json
from pathlib import Path
import re
import shutil
import subprocess

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ASSETS = DOCS / "assets"

# Short names for the twelve cases, in the order the grid shows them.
CASE_LABELS = {
    "casa1": ("RoboCasa365", "Open drawer"),
    "casa2": ("RoboCasa365", "Start microwave"),
    "lib1": ("LIBERO", "Moka pots"),
    "lib2": ("LIBERO", "Two mugs"),
    "pro1": ("LIBERO-PRO", "Microwave, object swapped"),
    "pro2": ("LIBERO-PRO", "Basket, position swapped"),
    "plus1": ("LIBERO-Plus", "Added distractor"),
    "plus2": ("LIBERO-Plus", "Drawer, new layout"),
    "twin1": ("RoboTwin 2.0", "Hammer a block"),
    "twin2": ("RoboTwin 2.0", "Open laptop"),
    "suite1": ("robosuite", "Stack"),
    "suite2": ("robosuite", "Door"),
}

VERDICTS = {"match": "match", "approximate": "close", "wrong": "wrong", "unknown": "unknown"}

# The answer keys are quoted in the source file in Chinese. These are the English readings of
# the same forty characters, one per question, in the order the questions are asked.
KEY_EN = {
    "casa1": ["every joint at or above 0.95", "0.10 m", "1–3, drawn from integers(1, 4)"],
    "casa2": ["(0.05, 0.05) m", "that the gripper has left start_button", "a container"],
    "lib1": ["both the initial and the goal state include Turnon flat_stove_1",
             "the right-hand one, moka_pot_right_init_region", "[−0.075, −0.025] m"],
    "lib2": ["red_coffee_mug_1", "[−0.325, −0.275] m", "white_yellow_mug_1"],
    "pro1": ["red_coffee_mug_1", "open — Open microwave_1",
             "[−0.025, 0.025] m"],
    "pro2": ["ketchup_init_region", "tomato_sauce_init_region", "[0.25, 0.27] m"],
    "plus1": ["bag_of_yeast_1, a bag of yeast", "(0.20, 0.15) m", "[0, 0] rad"],
    "plus2": ["(0.034, −0.054) m, with the raw (0.03416746, −0.05421570) accepted",
              "π rad, that is 180°", "open — Open white_cabinet_1_bottom_region"],
    "twin1": ["(0.025, 0.025, 0.025) m", "under 0.02 m on each axis",
              "that the hammer and the cube touch"],
    "twin2": ["0.2", "0.4", "strictly under 0.1 m"],
    "suite1": ["above 0.04 m", "2.0", "A = 0.020 m, B = 0.025 m, on every axis"],
    "suite2": ["above 0.3 rad", "True", "1,000 control steps"],
}


def read(name):
    with (ROOT / "results" / name).open() as stream:
        return [json.loads(line) for line in stream]


def clean(text):
    """Flatten the markdown an answer uses: links to their text, emphasis and code to plain."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return text.replace("**", "").replace("`", "")


def excerpt(text, limit=300):
    """The first run of prose in an answer, cut at a full stop when one is close by.

    Answers are written in Chinese and open with markdown headings, a table, and the occasional
    one-line signpost such as "逐项回答：", so walk the blocks and keep the sentences that carry
    something.
    """
    prose, first_seen = "", ""
    for block in text.split("\n\n"):
        block = block.strip()
        if not block or block[0] in "#|-*":
            continue
        lines = [line for line in block.splitlines()
                 if line.strip() and not line.lstrip().startswith("|")]
        if not lines:
            continue
        candidate = clean(" ".join(lines))
        if not first_seen and candidate:
            first_seen = candidate
        if len(candidate) < 45 or candidate.rstrip().endswith(("：", ":", "如下")):
            continue
        prose = (prose + " " + candidate).strip() if prose else candidate
        if len(prose) >= limit:
            break
    prose = prose or first_seen
    if len(prose) <= limit:
        return prose
    cut = prose.rfind("。", 0, limit)
    if cut < limit // 2:
        cut = prose.rfind(". ", 0, limit)
    if cut < limit // 2:
        cut = limit
    return prose[: cut + 1].rstrip() + " …"


def main():
    cases = json.loads((ROOT / "experiments/questions.json").read_text())
    graded = {(row["case"], row["condition"]): row for row in read("queries.jsonl")}
    scenes = json.loads((ROOT / "experiments/libero-plus.json").read_text())

    order = {case["id"]: index for index, case in enumerate(cases)}
    grid = []
    for case in sorted(cases, key=lambda c: order[c["id"]]):
        case_id = case["id"]
        benchmark, label = CASE_LABELS[case_id]
        row = {
            "id": case_id,
            "benchmark": benchmark,
            "label": label,
            "task": case["task"],
            "source": case["source"],
            "questions": case["questions_en"],
            "key": case["key"],
            "key_en": KEY_EN[case_id],
        }
        for condition in ("offline", "web"):
            record = graded[case_id, condition]
            row[condition] = {
                "verdicts": [VERDICTS[v] for v in record["judgment"]["fields"]],
                "note": record["judgment"].get("note", ""),
                "excerpt": excerpt(record["answer"]),
                "searched": [event["text"].split("(")[0].strip("` ")
                             for event in record["events"] if event["type"] == "tool"],
            }
        grid.append(row)

    totals = {}
    for condition in ("offline", "web"):
        verdicts = Counter(v for row in grid for v in row[condition]["verdicts"])
        hits = sum(1 for row in grid if "match" in row[condition]["verdicts"])
        totals[condition] = {"verdicts": dict(verdicts), "cases_with_a_match": hits}

    # Six LIBERO-Plus tasks and their four matched pairs.
    outcomes = {(r["task"], r["pair"], r["metadata"]): r["official_success"]
                for r in read("episodes.jsonl")}
    tasks = []
    for scene in scenes["tasks"]:
        task = scene["task"]
        provided = [outcomes[task, pair, "provided"] for pair in range(1, 5)]
        hidden = [outcomes[task, pair, "hidden"] for pair in range(1, 5)]
        tasks.append({
            "task": task,
            "label": scene["label"],
            "instruction": scene["instruction"],
            "provided": sum(provided),
            "hidden": sum(hidden),
            "only_provided": [pair for pair in range(4)
                              if provided[pair] and not hidden[pair]],
        })

    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "qa.json").write_text(json.dumps(
        {"cases": grid, "totals": totals}, ensure_ascii=False, separators=(",", ":")))
    (ASSETS / "tasks.json").write_text(json.dumps(tasks, ensure_ascii=False, indent=1))

    # The six starting views, redrawn on white and without the figure's own headline: the caption
    # under the figure says it instead.
    scene_png = ASSETS / "scenes.png"
    try:
        import report

        for name, value in {
            "PAPER": "#ffffff",
            "INK": "#172e33",
            "MUTED": "#6b7c80",
            "LINE": "#e4e8e5",
            "TEAL": "#118659",
            "RUST": "#d93a4b",
        }.items():
            setattr(report, name, value)
        report.FIGURES = ASSETS
        report.plot_manipulation(False)
        (ASSETS / "manipulation.png").unlink(missing_ok=True)
        image = Image.open(ASSETS / "gpt-gear-scenes.png")
        image.crop((0, 200, image.width, image.height)).save(scene_png)
        (ASSETS / "gpt-gear-scenes.png").unlink()
    except Exception as error:  # the simulator footage is optional for the page
        print(f"scenes: reusing figures/gpt-gear-scenes.png ({error})")
        image = Image.open(ROOT / "figures/gpt-gear-scenes.png")
        image.crop((0, 200, image.width, image.height)).save(scene_png)

    shutil.copyfile(ROOT / "results/first-episode.mp4", ASSETS / "rollout.mp4")
    # The first frame doubles as the video's poster, so the box is a camera view and not black.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(ASSETS / "rollout.mp4"),
         "-frames:v", "1", "-q:v", "3", str(ASSETS / "rollout-poster.jpg")],
        check=True,
    )

    print(f"qa.json     {len(grid)} cases, offline {totals['offline']}, web {totals['web']}")
    print(f"tasks.json  provided {sum(t['provided'] for t in tasks)}/24, "
          f"hidden {sum(t['hidden'] for t in tasks)}/24")


if __name__ == "__main__":
    main()
