/* The question grid: twelve cases, three questions each, one tab per arm.
   Data comes from assets/qa.json, written by runners/blog_data.py. */

(() => {
  const mount = document.getElementById("qa-figure");
  if (!mount) return;

  const VERDICT_LABEL = {
    match: "exact match",
    close: "close",
    wrong: "wrong",
    unknown: "unknown",
  };
  const CONDITION_LABEL = { offline: "Search off", web: "Search on" };
  const CONDITION_NOTE = {
    offline: "A clean folder, no network, no search.",
    web: "The same sheet, asked again with a search tool.",
  };

  let data = null;
  let condition = new URLSearchParams(location.search).get("arm") === "web" ? "web" : "offline";
  let selected = null;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function tally() {
    const totals = data.totals[condition];
    const verdicts = totals.verdicts;
    const matched = verdicts.match || 0;
    const asked = data.cases.length * 3;
    return `${matched} of ${asked} exact matches, in ${totals.cases_with_a_match} of ${data.cases.length} cases`;
  }

  function buildGrid() {
    const grid = el("div", "qa-grid");
    grid.setAttribute("role", "grid");

    data.cases.forEach((item) => {
      const row = el("div", "qa-row-label");
      const name = el("b", null, item.label);
      const bench = el("span", null, "  " + item.benchmark);
      row.append(name, bench);
      row.title = `${item.benchmark} · ${item.task}`;
      grid.append(row);

      const verdicts = item[condition].verdicts;
      verdicts.forEach((verdict, index) => {
        const cell = el("button", "qa-cell");
        cell.type = "button";
        cell.dataset.verdict = verdict;
        cell.dataset.case = item.id;
        cell.setAttribute("role", "gridcell");
        cell.setAttribute("aria-pressed", String(selected === item.id));
        cell.setAttribute(
          "aria-label",
          `${item.label}, question ${index + 1}: ${VERDICT_LABEL[verdict]}`
        );
        cell.title = `${item.label} · Q${index + 1} · ${VERDICT_LABEL[verdict]}`;
        cell.addEventListener("click", () => select(item.id));
        grid.append(cell);
      });

      const hits = verdicts.filter((v) => v === "match").length;
      const count = el("div", "qa-count", `${hits}/3`);
      count.title = `${hits} of 3 exact`;
      grid.append(count);

      if (selected === item.id) row.classList.add("qa-row-on");
    });

    return grid;
  }

  function buildDetail() {
    const item = data.cases.find((entry) => entry.id === selected);
    const panel = el("div", "qa-detail");
    panel.setAttribute("aria-live", "polite");

    if (!item) {
      panel.append(el("p", "qa-detail-sub",
        "Click any square to see the question, the answer key and the verdict."));
      return panel;
    }

    const arm = item[condition];
    panel.append(el("h4", null, `${item.benchmark} · ${item.label}`));
    panel.append(el("p", "qa-taskpath", item.task));
    panel.append(el("p", "qa-detail-sub", CONDITION_NOTE[condition]));

    const list = el("ol");
    item.questions.forEach((question, index) => {
      const verdict = arm.verdicts[index];
      const row = el("li");

      const heading = el("div", "qa-q", question);
      const chip = el("span", "qa-verdict", VERDICT_LABEL[verdict]);
      chip.dataset.verdict = verdict;
      heading.prepend(chip);

      const key = el("div", "qa-k");
      key.append(document.createTextNode("Key: "));
      const code = el("code", null, item.key_en[index]);
      key.append(code);

      row.append(heading, key);
      list.append(row);
    });
    panel.append(list);

    if (arm.note) panel.append(el("p", "qa-note", arm.note));

    // A case the model could not answer at all has nothing worth quoting.
    if (arm.excerpt && arm.verdicts.some((v) => v !== "unknown")) {
      const raw = el("details", "qa-raw");
      const summary = el("summary", null,
        `The answer, verbatim${condition === "offline" ? "" : " (searched)"}`);
      const body = el("p", null, arm.excerpt);
      body.setAttribute("lang", "zh-Hans");
      raw.append(summary, body);
      panel.append(raw);
    }

    return panel;
  }

  function render() {
    mount.replaceChildren();

    const box = el("div", "qa");

    const bar = el("div", "qa-bar");
    const toggle = el("div", "qa-toggle");
    toggle.setAttribute("role", "tablist");
    Object.entries(CONDITION_LABEL).forEach(([key, label]) => {
      const button = el("button", null, label);
      button.type = "button";
      button.setAttribute("role", "tab");
      button.setAttribute("aria-selected", String(key === condition));
      button.addEventListener("click", () => {
        if (condition === key) return;
        condition = key;
        render();
      });
      toggle.append(button);
    });
    const counts = el("div", "qa-tally");
    counts.innerHTML = tally().replace(/(\d+ of \d+)/g, "<b>$1</b>");
    bar.append(toggle, counts);
    box.append(bar);

    const legend = el("div", "qa-legend");
    [["match", "exact"], ["close", "close"], ["wrong", "wrong"], ["unknown", "unknown"]]
      .forEach(([key, label]) => {
        const span = el("span");
        const swatch = el("span", "qa-key");
        swatch.dataset.verdict = key;
        span.append(swatch, document.createTextNode(label));
        legend.append(span);
      });
    box.append(legend);

    box.append(buildGrid());
    box.append(buildDetail());
    mount.append(box);
  }

  function select(id) {
    selected = id;
    render();
  }

  fetch("assets/qa.json")
    .then((response) => {
      if (!response.ok) throw new Error(response.status);
      return response.json();
    })
    .then((json) => {
      data = json;
      // Linkable: ?case=lib2 opens that row. Otherwise start on the mug scene, which is the one
      // the model answered from memory and then got wrong with the source open.
      const asked = new URLSearchParams(location.search).get("case");
      selected = json.cases.some((c) => c.id === asked) ? asked : "lib2";
      render();
    })
    .catch(() => {
      mount.append(el("p", "qa-detail-sub",
        "The grid needs assets/qa.json. Run python3 runners/blog_data.py and open the page over HTTP."));
    });
})();
