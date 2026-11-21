"""Draw the figures that are not photographs.

The page inlines these SVGs, so the text inside them picks up the page's fonts through the
classes defined here and in docs/css/style.css. Widths are in one 760-wide column, the same
as the body text.

    python3 runners/blog_figures.py
"""
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"
ASSETS = DOCS / "assets"

SANS = "'Source Sans 3',Lato,Verdana,sans-serif"
MONO = "'JetBrains Mono','Source Code Pro',Menlo,monospace"
STEP = 19
PAD = 26        # left padding of the code inside its box
LEAD = 27       # first baseline below the top of the box


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def code(rows, top):
    """Rows are (text, class). Returns the markup and the number of lines drawn."""
    body = []
    for index, (text, klass) in enumerate(rows):
        attr = f' class="{klass}"' if klass else ""
        body.append(f'<text x="{PAD}" y="{top + LEAD + index * STEP}"{attr}'
                    f' xml:space="preserve">{esc(text)}</text>')
    return body


def interface():
    hidden_rows = [
        ("{", None),
        ('  "observation_id": 0,', None),
        ('  "instruction": "open the top layer of the drawer and put the bowl inside",', "k"),
        ('  "cameras": {"external": {...}, "wrist": {...}},', "q"),
        ('  "robot": {"ee_pose": [...], "joint_positions": [...], ...},', "q"),
        ('  "status": {"success": false, "terminal": false,', "q"),
        ('             "decision_steps": 0, "remaining_steps": 1200}', "q"),
        ("}", None),
    ]
    shown_rows = [
        ("{", None),
        ('  "observation_id": 0,', None),
        ('  "instruction": "open the top layer of the drawer and put the bowl inside",', "k"),
        ('  "cameras": {"external": {...}, "wrist": {...}},', "q"),
        ('  "robot": {"ee_pose": [...], "joint_positions": [...], ...},', "q"),
        ('  "status": {"success": false, "terminal": false,', "q"),
        ('             "decision_steps": 0, "remaining_steps": 1200,', "q"),
        ('             "task_metadata": {', "q"),
        ('               "benchmark": "LIBERO",', "a"),
        ('               "task": "libero_goal/2365",', "a"),
        ('               "suite": "libero_goal",', "a"),
        ('               "task_id": 2365,', "a"),
        ('               "task_name": "open the top layer of the drawer and put the bowl inside",', "a"),
        ('               "seed": 0,', "a"),
        ('               "init_state_index": 0}}}', "a"),
        ("}", None),
    ]

    def height(rows):
        return LEAD + (len(rows) - 1) * STEP + 18

    first_title = 14
    first_box = first_title + 30
    first_height = height(hidden_rows)
    second_title = first_box + first_height + 46
    second_box = second_title + 30
    second_height = height(shown_rows)
    total = second_box + second_height + 4

    first = code(hidden_rows, first_box)
    second = code(shown_rows, second_box)
    mark_top = second_box + LEAD + 7 * STEP - 13

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 {total}" role="img"
     aria-label="The same observation reply twice. With the identifiers hidden the agent reads the instruction and the robot state. With them provided it also reads the benchmark, the suite, a numeric task ID, the full task name, the seed and the initial-state index.">
  <style>
    text {{ font-family: {MONO}; font-size: 13px; fill: #3a4a4d; white-space: pre; }}
    .k {{ fill: #086d67; }}
    .q {{ fill: #8a9a9c; }}
    .a {{ fill: #118659; }}
    .t {{ font-family: {SANS}; font-size: 15px; font-weight: 600; fill: #172e33; }}
    .s {{ font-family: {SANS}; font-size: 13px; fill: #74868a; }}
    .box {{ fill: #fcfcfb; stroke: #e4e8e5; }}
    .box-on {{ fill: #f6fbf8; stroke: #bcdcc8; }}
  </style>

  <text class="t" x="0" y="{first_title}">IDs hidden</text>
  <text class="s" x="0" y="{first_title + 18}">The agent reads the instruction and the robot state.</text>
  <rect class="box" x="0" y="{first_box}" width="760" height="{first_height}" rx="5"/>
{chr(10).join('  ' + line for line in first)}

  <text class="t" x="0" y="{second_title}">IDs provided</text>
  <text class="s" x="0" y="{second_title + 18}">The same sentence, plus six fields the benchmark already knows.</text>
  <rect class="box-on" x="0" y="{second_box}" width="760" height="{second_height}" rx="5"/>
  <rect x="10" y="{mark_top}" width="3" height="{7 * STEP}" fill="#2e996a" rx="1"/>
{chr(10).join('  ' + line for line in second)}
</svg>
"""


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "fig-interface.svg").write_text(interface())
    print("fig-interface.svg written")


if __name__ == "__main__":
    main()
