"""Rebuild the blog figures from the released answers, verdicts and camera frames."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
FIGURES = ROOT / "figures"
PAPER, INK, MUTED, LINE = "#faf9f5", "#172e33", "#425a60", "#d5dcda"
TEAL, RUST = "#086d67", "#a64929"


def records(name):
    with (RESULTS / name).open() as stream:
        return [json.loads(line) for line in stream]


def canvas(width, height):
    fig = plt.figure(figsize=(width, height), facecolor=PAPER)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set(xlim=(0, 1), ylim=(0, 1))
    ax.axis("off")
    return fig, ax


def label(ax, x, y, text, *, size=12, color=INK, bold=False, **style):
    ax.text(x, y, text, fontsize=size, color=color, va="center",
            weight="bold" if bold else "normal", **style)


def save(fig, name, dpi=175):
    fig.savefig(FIGURES / name, dpi=dpi, facecolor=PAPER)
    plt.close(fig)


QUERY_TASKS = {
    "casa1": ("Open drawer", "打开抽屉"),
    "casa2": ("Start microwave", "启动微波炉"),
    "lib1": ("Moka pots", "摩卡壶"),
    "lib2": ("Two mugs", "两只杯子"),
    "pro1": ("Microwave · object swap", "微波炉换物体"),
    "pro2": ("Basket · position swap", "篮子换位置"),
    "plus1": ("Added object", "新增干扰物"),
    "plus2": ("Drawer · new layout", "抽屉布局变化"),
    "twin1": ("Hammer a block", "用锤子敲方块"),
    "twin2": ("Open laptop", "打开笔记本电脑"),
    "suite1": ("Stack", "Stack · 叠方块"),
    "suite2": ("Door", "Door · 开门"),
}


def plot_queries(chinese=False):
    cases = json.loads((ROOT / "experiments/questions.json").read_text())
    answers = records("queries.jsonl")
    scores = {(r["case"], r["condition"]): r["judgment"]["fields"] for r in answers}
    assert len(scores) == len(answers) == 2 * len(cases) == 24
    assert all(len(fields) == 3 for fields in scores.values())
    totals = {}
    for mode in ("offline", "web"):
        case_scores = {case["id"]: scores[case["id"], mode] for case in cases}
        totals[mode] = draw_query_grid(cases, case_scores, mode, chinese)
    return totals


def draw_query_grid(cases, case_scores, mode, chinese):
    n = sum(fields.count("match") for fields in case_scores.values())
    denominator = sum(map(len, case_scores.values()))
    matched_cases = sum("match" in fields for fields in case_scores.values())

    fig, ax = canvas(6, 8) if chinese else canvas(8.4, 5.8)
    if mode == "offline":
        stem = "query"
        heading = "关掉搜索，Agent 还能答出什么？" if chinese else "What did the agent know with search off?"
    else:
        stem = "query-search"
        heading = "同样的问题，打开搜索后呢？" if chinese else "Same questions, with search on"
    subhead = (f"{denominator} 项隐藏细节，精确答对 {n} 项，分布在 {matched_cases} 个案例中。"
               if chinese else f"{n} of {denominator} hidden details matched exactly, across {matched_cases} of {len(cases)} cases.")
    label(ax, .055, .94, heading, size=20, bold=True)
    label(ax, .055, .864, subhead, size=12, color=TEAL, bold=True)
    bench_x, task_x, score_x = .055, .40, .76
    headers = ("评测名称", "案例", "三个问题") if chinese else ("Benchmark", "Case", "Three questions")
    for x, header in zip((bench_x, task_x, score_x), headers, strict=True):
        label(ax, x, .78, header, color=MUTED, size=10)
    last = None
    for i, case in enumerate(cases):
        y = .718 - i * .052
        benchmark = case["benchmark"]
        if benchmark != last:
            ax.plot([.055, .95], [y + .036] * 2, color=LINE, lw=.8)
            label(ax, bench_x, y, benchmark, size=12, bold=True)
            last = benchmark
        label(ax, task_x, y, QUERY_TASKS[case["id"]][int(chinese)], size=11)
        for question, score in enumerate(case_scores[case["id"]]):
            filled = score == "match"
            ax.scatter(score_x + .055 * question, y, marker="s", s=95,
                       facecolor=TEAL if filled else PAPER, edgecolor=TEAL if filled else "#a9b5b2",
                       linewidth=1.2)
    caption = ("每格是一问，依题目顺序排列。实心格表示与固定版本的答案精确匹配。"
               if chinese else "Each square is one question in prompt order. Filled squares match the pinned answer key.")
    label(ax, .055, .055, caption, size=9, color=MUTED)
    save(fig, f"{stem}-zh.png" if chinese else f"{stem}.png", 180 if chinese else 175)
    return n


def first_camera_pair(episode):
    frame = episode["first_frame"]
    command = ["ffmpeg", "-v", "error", "-i", str(RESULTS / episode["views"]),
               "-vf", f"select=eq(n\\,{frame})", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    pixels = subprocess.check_output(command)
    return np.frombuffer(pixels, dtype=np.uint8).reshape(256, 512, 3)


# Line breaks keep all six tasks legible on one categorical x-axis.
TASK_TICKS = {
    "Both moka pots on stove": ("Moka pots\non stove", "双壶\n上炉"),
    "Bowl in top drawer": ("Bowl in\ndrawer", "碗入\n抽屉"),
    "Specified bowl on plate": ("Target bowl\non plate", "指定碗\n上盘"),
    "Plate to stove front": ("Push plate\nto stove", "推盘至\n炉前"),
    "Mug in, door closed": ("Mug in,\nclose door", "杯入炉\n并关门"),
    "Soup in basket": ("Soup can\nin basket", "汤罐\n入篮"),
}


SCENE_TITLES_ZH = {
    "Both moka pots on stove": "把两把摩卡壶放上炉灶",
    "Bowl in top drawer": "把碗放进上层抽屉",
    "Specified bowl on plate": "把指定的黑碗放到盘上",
    "Plate to stove front": "把盘子推到炉灶前",
    "Mug in, door closed": "把杯子放进微波炉并关门",
    "Soup in basket": "把汤罐放进篮子",
}


def plot_task_views(scenes, frames, by_task, chinese):
    fig, page = canvas(6, 8) if chinese else canvas(8.7, 6.1)
    heading = "GPT-GEAR 里，模型看到了什么？" if chinese else "What the model saw through GPT-GEAR"
    views = ("六个任务的第一帧。每组左图是外部相机，右图是腕部相机。"
             if chinese else "First observations from six tasks. External camera on the left, wrist camera on the right.")
    label(page, .05, .94, heading, size=20, bold=True)
    label(page, .05, .86, views, size=10, color=MUTED)
    rows, columns = (3, 2) if chinese else (2, 3)
    grid = fig.add_gridspec(rows, columns, left=.05, right=.95, top=.79, bottom=.07,
                            wspace=.09, hspace=.40)
    for i, scene in enumerate(scenes):
        task = scene["task"]
        photo = fig.add_subplot(grid[i])
        photo.imshow(frames[task])
        photo.set_axis_off()
        title = SCENE_TITLES_ZH[scene["label"]] if chinese else scene["label"]
        photo.set_title(title, loc="left", color=INK, fontsize=11, weight="bold", pad=6)
        counts = by_task[task]
        provided = "给出标识" if chinese else "IDs provided"
        hidden = "隐藏标识" if chinese else "IDs hidden"
        label(photo, 0, -.12, f"{provided}  {counts['provided']}/4", size=9.5, color=TEAL,
              bold=True, transform=photo.transAxes)
        label(photo, 1, -.12, f"{hidden}  {counts['hidden']}/4", size=9.5, color=RUST,
              bold=True, ha="right", transform=photo.transAxes)
    save(fig, "gpt-gear-scenes-zh.png" if chinese else "gpt-gear-scenes.png", 180 if chinese else 175)


def plot_manipulation(chinese=False):
    scenes = json.loads((ROOT / "experiments/libero-plus.json").read_text())["tasks"]
    episodes = records("episodes.jsonl")
    outcomes = {(r["task"], r["pair"], r["metadata"]): r["official_success"] for r in episodes}
    assert len(outcomes) == len(episodes) == 48 and all(r["valid"] for r in episodes)
    totals = Counter()
    pairs = Counter()
    by_task = {}
    for scene in scenes:
        task = scene["task"]
        task_counts = Counter()
        for pair in range(1, 5):
            provided = outcomes[task, pair, "provided"]
            hidden = outcomes[task, pair, "hidden"]
            pairs[provided, hidden] += 1
            task_counts.update(provided=int(provided), hidden=int(hidden))
        by_task[task] = task_counts
        totals.update(task_counts)
    denominator = sum(pairs.values())
    assert denominator == 24

    fig, page = canvas(6, 8) if chinese else canvas(9, 5.8)
    gap = 100 * (totals["provided"] - totals["hidden"]) / denominator
    label(page, .055, .941, "LIBERO-Plus   /   gpt-6.1-sol", size=12, color=MUTED)
    heading = ("给 Robot Use Agent 看任务标识" if chinese else "When the robot agent sees the task IDs")
    label(page, .055, .87, heading, size=21 if chinese else 23, bold=True)
    subhead = (f"成功率相差 {gap:.1f} 个百分点" if chinese else
               f"A {gap:.1f} percentage point gap in success rate")
    label(page, .055, .801, subhead, size=17, bold=True, color=TEAL)

    axis_left, axis_width = (.155, .81) if chinese else (.105, .86)
    chart = fig.add_axes([axis_left, .294, axis_width, .385])
    chart.set_facecolor(PAPER)
    chart.set_axisbelow(True)
    chart.grid(axis="y", color=LINE, linewidth=.85)
    chart.spines[["top", "right", "left"]].set_visible(False)
    chart.spines["bottom"].set_color(MUTED)
    chart.tick_params(axis="both", length=0, labelcolor=INK, labelsize=10, pad=5)
    chart.set_xlim(-.5, len(scenes) - .5)
    chart.set_ylim(0, 113)
    chart.set_yticks([0, 25, 50, 75, 100], labels=["0%", "25%", "50%", "75%", "100%"])
    chart.set_ylabel("成功率" if chinese else "Success rate", color=INK, fontsize=11)
    chart.set_xticks(range(len(scenes)), labels=[TASK_TICKS[s["label"]][int(chinese)] for s in scenes])
    groups = (("provided", "给出标识" if chinese else "IDs provided", TEAL),
              ("hidden", "隐藏标识" if chinese else "IDs hidden", RUST))
    for j, (group, title, color) in enumerate(groups):
        legend_x = .063 + j * .48
        page.scatter(legend_x, .719, s=105, marker="s", color=color)
        total = totals[group]
        label(page, legend_x + .024, .719, f"{title}  {total}/{denominator} ({total/denominator:.1%})",
              size=10.6 if chinese else 12, color=color, bold=True)
        counts = [by_task[s["task"]][group] for s in scenes]
        x = np.arange(len(scenes)) + (j - .5) * .36
        bars = chart.bar(x, np.array(counts) * 25, width=.33, color=color, zorder=3)
        chart.bar_label(bars, labels=[f"{n}/4" for n in counts], padding=2,
                        fontsize=8 if chinese else 9, color=color)

    # Use the external camera from the actual first observation as a visual key for each task.
    width = axis_width / len(scenes)
    frames = {}
    for i, scene in enumerate(scenes):
        source = next(row for row in episodes if row["task"] == scene["task"])
        frames[scene["task"]] = first_camera_pair(source)
        external, wrist = np.hsplit(frames[scene["task"]], 2)
        if any(view.max() == view.min() for view in (external, wrist)):
            raise ValueError(f"{scene['task']} has a blank first camera view")
        photo = fig.add_axes([axis_left + i * width + width*.03, .091, width*.94, .118])
        photo.imshow(external)
        photo.set_axis_off()
    note = ("每题每组 4 次。柱顶为通过次数 / 尝试次数，下图为各任务的真实首帧。"
            if chinese else "Four episodes per task and group. Labels give passes / attempts. Images show each starting scene.")
    label(page, .055, .044, note, size=9, color=MUTED)
    save(fig, "manipulation-zh.png" if chinese else "manipulation.png", 180 if chinese else 175)
    plot_task_views(scenes, frames, by_task, chinese)
    return totals, pairs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zh", action="store_true", help="Generate the Chinese portrait figures")
    chinese = parser.parse_args().zh
    if chinese:
        fonts = {font.name for font in font_manager.fontManager.ttflist}
        candidates = ("PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC", "Heiti SC", "Microsoft YaHei")
        available = next((f for f in candidates if f in fonts), None)
        if available is None:
            parser.error("Install a Simplified Chinese font such as Noto Sans CJK SC")
        matplotlib.rcParams["font.family"] = [available, "DejaVu Sans"]
    FIGURES.mkdir(exist_ok=True)
    query = plot_queries(chinese)
    groups, pairs = plot_manipulation(chinese)
    print(f"Exact fields out of 36: {query}. Passes out of 24: {dict(groups)}.")
    print(f"Matched pairs: both passed {pairs[True, True]}, only with IDs {pairs[True, False]}, "
          f"only hidden {pairs[False, True]}, both failed {pairs[False, False]}.")
