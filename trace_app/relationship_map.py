"""Drawing the knowledge relationship map as inline SVG.

The map is not decoration. A line between two boxes asserts that one item
bears on another, which is a factual claim about the project, so only
relationships whose quotation verified against a source are passed in here.
Nothing is drawn that the sources did not state.

Layout is deterministic: items are placed in lanes by category in the
specification's order, left to right, and stacked within a lane. The same
reconstruction always produces the same picture, which matters when someone
is comparing two runs.

Nodes are drawn as plain shapes rather than links. An anchor would trigger a
full browser navigation, which resets Streamlit's session state and signs the
reader out; focus is therefore handled in session state by a control beside
the map, and the focused node is highlighted here.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any

from trace_app.models import CATEGORY_ORDER, category_label, relation_label

NODE_WIDTH = 196
LINE_HEIGHT = 15
NODE_PADDING = 11
MAX_LINES = 3
CHARS_PER_LINE = 27
LANE_GAP = 268
ROW_GAP = 20
MARGIN_X = 18
MARGIN_TOP = 72
MARGIN_BOTTOM = 18

#: One colour per relation, so a reader can tell a blocker from a dependency
#: without following every label.
RELATION_COLOURS: dict[str, str] = {
    "blocks": "#C2410C",
    "depends_on": "#7A5CC4",
    "resulted_in": "#1E9E68",
    "addresses": "#2C83BC",
    "owned_by": "#6B7A8C",
    "relates_to": "#94A3B8",
}

_CATEGORY_TONES: dict[str, str] = {
    "project_overview": "#17567E",
    "decision": "#1E9E68",
    "completed_work": "#1E9E68",
    "pending_work": "#D08A16",
    "blocker": "#C2410C",
    "dependency": "#7A5CC4",
    "risk": "#D04545",
    "open_question": "#2C83BC",
    "responsibility": "#6B7A8C",
    "next_action": "#17567E",
}


@dataclass
class _Node:
    item_id: str
    category: str
    lines: list[str]
    approved: bool
    lane: int = 0
    row: int = 0
    x: float = 0.0
    y: float = 0.0
    height: float = 0.0
    degree: int = 0


@dataclass
class MapData:
    """What the map needs, already filtered to verified relationships."""

    nodes: list[_Node] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0
    lanes: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.edges


def _wrap(text: str) -> list[str]:
    """Break a statement into at most ``MAX_LINES`` short lines."""
    words = str(text or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= CHARS_PER_LINE:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) == MAX_LINES:
            break
    if current and len(lines) < MAX_LINES:
        lines.append(current)
    if not lines:
        return ["(empty)"]
    if len(lines) == MAX_LINES and len(" ".join(words)) > sum(len(x) for x in lines):
        lines[-1] = lines[-1][: CHARS_PER_LINE - 1].rstrip() + "…"
    return lines


def build_map(relationships: list[dict[str, Any]]) -> MapData:
    """Lay out the nodes and edges for a set of verified relationships."""
    data = MapData()
    if not relationships:
        return data

    nodes: dict[str, _Node] = {}

    def ensure(item_id: str, text: str, category: str, status: str) -> _Node:
        if item_id not in nodes:
            nodes[item_id] = _Node(
                item_id=item_id,
                category=str(category or ""),
                lines=_wrap(text),
                approved=str(status) == "approved",
            )
        nodes[item_id].degree += 1
        return nodes[item_id]

    for row in relationships:
        ensure(
            str(row["from_item_id"]), row.get("from_text", ""),
            row.get("from_category", ""), row.get("from_review_status", ""),
        )
        ensure(
            str(row["to_item_id"]), row.get("to_text", ""),
            row.get("to_category", ""), row.get("to_review_status", ""),
        )

    # Lanes follow the specification's category order, keeping only the
    # categories that actually appear, so there are no empty columns.
    present = [c for c in CATEGORY_ORDER if any(n.category == c for n in nodes.values())]
    present += sorted({n.category for n in nodes.values()} - set(present))
    lane_of = {category: position for position, category in enumerate(present)}

    by_lane: dict[int, list[_Node]] = {}
    for node in nodes.values():
        node.lane = lane_of.get(node.category, len(present))
        by_lane.setdefault(node.lane, []).append(node)

    for lane, members in by_lane.items():
        # Busiest items first: the hubs of the graph sit at the top, where a
        # reader looks before anywhere else.
        members.sort(key=lambda n: (-n.degree, n.lines[0]))
        cursor = float(MARGIN_TOP)
        for row_index, node in enumerate(members):
            node.row = row_index
            node.height = len(node.lines) * LINE_HEIGHT + NODE_PADDING * 2
            node.x = MARGIN_X + lane * LANE_GAP
            node.y = cursor
            cursor += node.height + ROW_GAP

    data.nodes = list(nodes.values())
    data.lanes = present
    data.edges = [
        {
            "from": nodes[str(r["from_item_id"])],
            "to": nodes[str(r["to_item_id"])],
            "relation": str(r.get("relation") or "relates_to"),
            "id": str(r.get("id") or ""),
        }
        for r in relationships
        if str(r["from_item_id"]) in nodes and str(r["to_item_id"]) in nodes
    ]
    data.width = MARGIN_X * 2 + max(1, len(present)) * LANE_GAP
    data.height = (
        max((n.y + n.height for n in data.nodes), default=MARGIN_TOP) + MARGIN_BOTTOM
    )
    return data


def _edge_path(edge: dict[str, Any]) -> str:
    """A curve from the right edge of one node to the left edge of another."""
    a, b = edge["from"], edge["to"]
    x1, y1 = a.x + NODE_WIDTH, a.y + a.height / 2
    x2, y2 = b.x, b.y + b.height / 2

    if b.lane <= a.lane:
        # Backwards or same-lane: loop out to the right and come back, so the
        # line is not hidden underneath the boxes.
        bulge = 46 + abs(a.row - b.row) * 8
        return (
            f"M {x1:.1f} {y1:.1f} C {x1 + bulge:.1f} {y1:.1f}, "
            f"{x2 + NODE_WIDTH + bulge:.1f} {y2:.1f}, "
            f"{b.x + NODE_WIDTH:.1f} {y2:.1f}"
        )
    midpoint = (x1 + x2) / 2
    skipped = b.lane - a.lane - 1
    if skipped <= 0:
        return (
            f"M {x1:.1f} {y1:.1f} C {midpoint:.1f} {y1:.1f}, "
            f"{midpoint:.1f} {y2:.1f}, {x2 - 6:.1f} {y2:.1f}"
        )

    # The edge jumps over one or more lanes. Routed straight it would pass
    # behind the boxes in between and look like it starts at one of them, so
    # arc it above instead.
    lift = 26 + skipped * 10
    peak = min(y1, y2) - lift
    return (
        f"M {x1:.1f} {y1:.1f} C {x1 + 40:.1f} {peak:.1f}, "
        f"{x2 - 40:.1f} {peak:.1f}, {x2 - 6:.1f} {y2:.1f}"
    )


def render_svg(data: MapData, focus_id: str | None = None) -> str:
    """Render the map. Each node links to ``?focus=<item id>``."""
    if data.is_empty:
        return ""

    parts: list[str] = [
        f'<svg viewBox="0 0 {data.width:.0f} {data.height:.0f}" '
        f'width="{data.width:.0f}" height="{data.height:.0f}" '
        'xmlns="http://www.w3.org/2000/svg" role="img" '
        'aria-label="Knowledge relationship map">',
        "<defs>",
    ]
    for relation, colour in RELATION_COLOURS.items():
        parts.append(
            f'<marker id="arrow-{relation}" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
            f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{colour}"/></marker>'
        )
    parts.append("</defs>")

    # Lane headings
    for position, category in enumerate(data.lanes):
        parts.append(
            f'<text x="{MARGIN_X + position * LANE_GAP}" y="26" '
            'font-size="10.5" font-weight="700" letter-spacing="0.08em" '
            f'fill="#7A8794">{html.escape(category_label(category).upper())}</text>'
        )

    # Edges first, so nodes sit on top of them
    for edge in data.edges:
        colour = RELATION_COLOURS.get(edge["relation"], RELATION_COLOURS["relates_to"])
        dimmed = focus_id is not None and focus_id not in (
            edge["from"].item_id,
            edge["to"].item_id,
        )
        parts.append(
            f'<path d="{_edge_path(edge)}" fill="none" stroke="{colour}" '
            f'stroke-width="{1.4 if dimmed else 2.1}" '
            f'opacity="{0.22 if dimmed else 0.85}" '
            f'marker-end="url(#arrow-{edge["relation"]})"/>'
        )

    # Nodes
    for node in data.nodes:
        accent = _CATEGORY_TONES.get(node.category, "#94A3B8")
        focused = focus_id == node.item_id
        dimmed = focus_id is not None and not focused
        opacity = 0.4 if dimmed else 1.0
        stroke = accent if focused else "#DDE3EB"
        parts.append(
            f'<g opacity="{opacity}" data-item="{html.escape(node.item_id)}">'
            f'<rect x="{node.x:.1f}" y="{node.y:.1f}" width="{NODE_WIDTH}" '
            f'height="{node.height:.1f}" rx="9" fill="#FFFFFF" '
            f'stroke="{stroke}" stroke-width="{2 if focused else 1}"/>'
            f'<rect x="{node.x:.1f}" y="{node.y:.1f}" width="4" '
            f'height="{node.height:.1f}" rx="2" fill="{accent}"/>'
        )
        for line_index, line in enumerate(node.lines):
            baseline = node.y + NODE_PADDING + 11 + line_index * LINE_HEIGHT
            parts.append(
                f'<text x="{node.x + 14:.1f}" y="{baseline:.1f}" font-size="11.3" '
                f'fill="#1A2027">{html.escape(line)}</text>'
            )
        if not node.approved:
            parts.append(
                f'<circle cx="{node.x + NODE_WIDTH - 11:.1f}" '
                f'cy="{node.y + 11:.1f}" r="3.4" fill="#2C83BC" '
                '><title>Not yet approved</title></circle>'
            )
        parts.append("</g>")

    parts.append("</svg>")
    return "".join(parts)


def legend(relations_present: set[str]) -> str:
    """A key for the relation colours actually used."""
    items = []
    for relation in RELATION_COLOURS:
        if relation not in relations_present:
            continue
        colour = RELATION_COLOURS[relation]
        items.append(
            '<span class="tr-legend-i">'
            f'<span style="display:inline-block;width:16px;height:3px;'
            f'background:{colour};border-radius:2px"></span>'
            f"{html.escape(relation_label(relation))}</span>"
        )
    return f'<div class="tr-legend">{"".join(items)}</div>' if items else ""
