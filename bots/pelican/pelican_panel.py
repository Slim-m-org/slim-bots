"""The Game servers panel a connector install shows beside the chat: one row per server, drawn as a slim-m scene."""

from slimbots import scene

import pelican_core as core

WIDTH = 100
ROW = 16
TOP = 4
REFRESH_MS = 30_000
STATE_COLORS = {"running": "accent", "starting": "muted", "stopping": "muted", "offline": "danger"}


def row_detail(server, stats):
    """The second line of a row: the state, with CPU and RAM while it runs."""
    state = core.state_of(server, stats)
    resources = (stats or {}).get("resources")
    if resources and state != "offline":
        return f"{state} - cpu {resources.get('cpu_absolute', 0):.0f}%, {core.format_bytes(resources.get('memory_bytes', 0))}"
    return state


def servers_scene(rows):
    """Every (server, stats) pair as a row; a panel with no servers says so instead of drawing nothing."""
    if not rows:
        ops = [{"op": "text", "x": WIDTH / 2, "y": 12, "s": "No servers to show", "fill": "muted", "size": 5, "align": "center"}]
        return scene(WIDTH, 25, ops, status="The panel lists no servers this bot may show.", controls=["Refresh"])
    ops = []
    for index, (server, stats) in enumerate(rows):
        middle = TOP + index * ROW + ROW / 2
        if index:
            ops.append({"op": "line", "x1": 2, "y1": middle - ROW / 2, "x2": WIDTH - 2, "y2": middle - ROW / 2, "stroke": "border", "sw": 0.3})
        color = STATE_COLORS.get(core.state_of(server, stats), "border")
        ops.append({"op": "circle", "cx": 5, "cy": middle - 3, "r": 1.6, "fill": color})
        ops.append({"op": "text", "x": 10, "y": middle - 3, "s": server["name"], "fill": "text", "size": 4.5, "align": "left"})
        ops.append({"op": "text", "x": 10, "y": middle + 3.5, "s": row_detail(server, stats), "fill": "muted", "size": 3.6, "align": "left"})
    running = sum(1 for server, stats in rows if core.state_of(server, stats) == "running")
    height = max(25, TOP * 2 + ROW * len(rows))
    return scene(WIDTH, height, ops, status=f"{running} of {len(rows)} running", controls=["Refresh"], refresh_ms=REFRESH_MS)
