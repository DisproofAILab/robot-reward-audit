"""Collect and render one Codex turn at message and tool-call granularity."""

from collections import defaultdict
import json


class Transcript:
    def __init__(self):
        self.items = {}  # Insertion order is the order each item was first observed.
        self.fragments = defaultdict(lambda: defaultdict(list))
        self.tool_requests = {}
        self.token_usage = None

    def record_item(self, item, completed):
        if item["type"] not in ("agentMessage", "reasoning", "dynamicToolCall", "webSearch", "mcpToolCall"):
            return
        entry = self.items.setdefault(item["id"], {})
        entry.update({key: value for key, value in item.items() if value is not None})
        if completed:
            entry["completed"] = True

    def observe(self, direction, message):
        method = message.get("method")
        params = message.get("params", {})
        if direction == "client":
            call_id = self.tool_requests.get(message.get("id"))
            if call_id is not None and "result" in message:
                self.items[call_id].update(message["result"])
            return

        if method in ("item/started", "item/completed"):
            self.record_item(params["item"], method == "item/completed")
        elif method in ("item/agentMessage/delta", "item/reasoning/summaryTextDelta"):
            item_id = params["itemId"]
            kind = "agentMessage" if method == "item/agentMessage/delta" else "reasoning"
            self.items.setdefault(item_id, {"type": kind, "id": item_id})
            index = params.get("summaryIndex", 0)
            self.fragments[item_id][index].append(params["delta"])
        elif method == "item/tool/call":
            call_id = params["callId"]
            self.tool_requests[message["id"]] = call_id
            self.record_item({"type": "dynamicToolCall", "id": call_id, "tool": params["tool"],
                              "arguments": params["arguments"]}, False)
        elif method == "thread/tokenUsage/updated":
            self.token_usage = params["tokenUsage"]["total"]
        elif method == "turn/completed" and params["turn"]["status"] == "completed":
            for item in params["turn"].get("items", []):
                # The turn may contain a shortened copy of an already completed item.
                if not self.items.get(item["id"], {}).get("completed"):
                    self.record_item(item, True)

    def text_blocks(self, item):
        # Completed text is authoritative. Use joined fragments only for missing
        # text, including a summary section or answer interrupted mid-stream.
        if item["type"] == "reasoning":
            blocks = dict(enumerate(item.get("summary", [])))
        else:
            blocks = {0: item.get("text", "")}
        for index, fragments in self.fragments.get(item["id"], {}).items():
            if not blocks.get(index):
                blocks[index] = "".join(fragments)
        return [blocks[index] for index in sorted(blocks) if blocks[index].strip()]

    def render(self, workspace_text):
        final_answer = ""
        steps = []
        has_summary = False
        for item in self.items.values():
            kind = item["type"]
            if kind == "dynamicToolCall":
                steps.append(format_tool_call(item, workspace_text))
            elif kind == "webSearch":
                action = item.get("action") or {"query": item.get("query", "")}
                steps.append("### 网页检索\n\n" + json.dumps(action, ensure_ascii=False))
            elif kind == "mcpToolCall":
                name = str(item.get("server", "")) + "/" + str(item.get("tool", ""))
                steps.append("### MCP 调用\n\n`" + name + "` · " + str(item.get("status", "unknown")))
            elif kind == "reasoning":
                for block in self.text_blocks(item):
                    title = "推理摘要" if item.get("completed") else "推理摘要（未完成）"
                    steps.append("### " + title + "\n\n" + block)
                    has_summary = True
            elif kind == "agentMessage" and item.get("phase") in (None, "final_answer"):
                text = "\n\n".join(self.text_blocks(item))
                if text and item.get("completed"):
                    final_answer = text
                elif text:
                    steps.append("### 模型输出（未完成）\n\n" + text)
        if not has_summary:
            steps.append("本轮未返回可见推理摘要。")
        body = ["## 最终回答", final_answer or "未收到完整的最终回答。", "## 执行过程", *steps]
        return "\n\n".join(body) + "\n", final_answer


def format_tool_call(item, workspace_text):
    args = json.dumps(item["arguments"], ensure_ascii=False)
    status = {True: "成功", False: "失败"}.get(item.get("success"), "未收到结果")
    parts = ["### 工具调用", "`" + item["tool"] + "(" + args + ")` · " + status]
    for content in item.get("contentItems", []):
        text = content.get("text")
        if text is not None and text == workspace_text and item["tool"] == "read_workspace_file":
            parts.append("返回了[本轮题面](../workspace/AGENTS.md)。")
        else:
            parts.append(text if text is not None else json.dumps(content, ensure_ascii=False))
    return "\n\n".join(parts)
