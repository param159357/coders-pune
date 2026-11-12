"""Knowledge relationship map tests (Round 3, improvement 3).

The commitment was an evidence-backed map where "a relationship with no
source support will not be drawn". A line between two boxes asserts that one
item bears on another, which is a claim about the project, so it is held to
the same standard as any other claim.
"""

from __future__ import annotations

import sqlite3

import pytest

from tests.conftest import OWNER, principal
from trace_app.ai.schemas import ReconstructionDraft
from trace_app.relationship_map import build_map, legend, render_svg
from trace_app.security.authz import AccessDenied
from trace_app.services.document_service import DocumentService
from trace_app.services.reconstruction_service import ReconstructionService, _merge_drafts
from trace_app.services.review_service import ReviewService

BRIEF = (
    "Email notification testing is blocked by the absence of a configured "
    "test email service.\n\n"
    "Registration validation remains unfinished.\n\n"
    "The release depends on approval of the final event data format."
)


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, payload):
        self.payload = payload

    def generate_json(self, system_prompt, user_prompt):
        return self.payload


@pytest.fixture()
def seeded(connection: sqlite3.Connection, isolated_storage, project: dict):
    del isolated_storage
    source = DocumentService(connection, OWNER).upload(
        project["id"], "brief.txt", BRIEF.encode()
    ).source
    return {
        "connection": connection,
        "project": project,
        "source_id": source["id"],
        "service": ReconstructionService(connection, OWNER),
    }


def _payload(source_id: str, link_excerpt: str, relation: str = "blocks") -> dict:
    return {
        "items": [
            {
                "ref": "i1",
                "category": "blocker",
                "text": "Email notification testing is blocked.",
                "evidence_status": "supported",
                "evidence": [
                    {
                        "source_id": source_id,
                        "location": "paragraph 1",
                        "excerpt": "Email notification testing is blocked by the absence",
                    }
                ],
            },
            {
                "ref": "i2",
                "category": "pending_work",
                "text": "Registration validation remains unfinished.",
                "evidence_status": "supported",
                "evidence": [
                    {
                        "source_id": source_id,
                        "location": "paragraph 2",
                        "excerpt": "Registration validation remains unfinished.",
                    }
                ],
            },
        ],
        "relationships": [
            {
                "from_ref": "i1",
                "to_ref": "i2",
                "relation": relation,
                "evidence": {
                    "source_id": source_id,
                    "location": "paragraph 1",
                    "excerpt": link_excerpt,
                },
            }
        ],
        "conflicts": [],
        "missing_information": [],
    }


# --------------------------------------------------------------------------
# A link is only drawn when a source states it
# --------------------------------------------------------------------------

def test_a_supported_link_is_stored_and_drawable(seeded) -> None:
    outcome = seeded["service"].run(
        seeded["project"],
        FakeProvider(
            _payload(
                seeded["source_id"],
                "Email notification testing is blocked by the absence",
            )
        ),
    )
    assert outcome.ok
    edges = seeded["service"].relationships(outcome.reconstruction_id)
    assert len(edges) == 1
    assert edges[0]["relation"] == "blocks"
    assert edges[0]["from_text"].startswith("Email notification")
    assert edges[0]["to_text"].startswith("Registration validation")


def test_an_unsupported_link_is_never_drawn(seeded) -> None:
    """The central promise: no quotation, no line."""
    outcome = seeded["service"].run(
        seeded["project"],
        FakeProvider(
            _payload(seeded["source_id"], "A sentence that is nowhere in the source")
        ),
    )
    assert seeded["service"].relationships(outcome.reconstruction_id) == []

    drawn, proposed = seeded["service"].relationship_counts(outcome.reconstruction_id)
    assert (drawn, proposed) == (0, 1)
    assert any("discarded" in note for note in outcome.notes)


def test_a_link_citing_a_fabricated_source_is_discarded(seeded) -> None:
    payload = _payload(seeded["source_id"], "ignored")
    payload["relationships"][0]["evidence"]["source_id"] = "src_fabricated"
    outcome = seeded["service"].run(seeded["project"], FakeProvider(payload))
    assert seeded["service"].relationships(outcome.reconstruction_id) == []


def test_a_link_to_an_unknown_item_is_dropped(seeded) -> None:
    """A ref the model never defined has no endpoint to attach to."""
    payload = _payload(
        seeded["source_id"], "Email notification testing is blocked by the absence"
    )
    payload["relationships"][0]["to_ref"] = "i_never_defined"
    outcome = seeded["service"].run(seeded["project"], FakeProvider(payload))
    assert seeded["service"].relationships(outcome.reconstruction_id) == []


def test_a_self_link_is_dropped(seeded) -> None:
    payload = _payload(
        seeded["source_id"], "Email notification testing is blocked by the absence"
    )
    payload["relationships"][0]["to_ref"] = "i1"
    outcome = seeded["service"].run(seeded["project"], FakeProvider(payload))
    assert seeded["service"].relationships(outcome.reconstruction_id) == []


def test_an_unknown_relation_becomes_the_weakest_one(seeded) -> None:
    outcome = seeded["service"].run(
        seeded["project"],
        FakeProvider(
            _payload(
                seeded["source_id"],
                "Email notification testing is blocked by the absence",
                relation="causes_somehow",
            )
        ),
    )
    edges = seeded["service"].relationships(outcome.reconstruction_id)
    assert edges[0]["relation"] == "relates_to"


def test_the_verification_counts_include_relationships(seeded) -> None:
    outcome = seeded["service"].run(
        seeded["project"],
        FakeProvider(
            _payload(
                seeded["source_id"],
                "Email notification testing is blocked by the absence",
            )
        ),
    )
    latest = seeded["service"].latest(seeded["project"]["id"])
    counts = seeded["service"].verification_counts(latest)
    assert counts["relationships_proposed"] == 1
    assert counts["relationships_verified"] == 1
    assert counts["relationships_discarded"] == 0


# --------------------------------------------------------------------------
# Refs are per-reply, and must not collide across batches
# --------------------------------------------------------------------------

def test_refs_are_namespaced_when_batches_are_merged() -> None:
    """Two batches both calling an item "i1" must not be conflated."""
    first = ReconstructionDraft.model_validate(
        {
            "items": [{"ref": "i1", "category": "blocker", "text": "Alpha blocker."}],
            "relationships": [],
        }
    )
    second = ReconstructionDraft.model_validate(
        {
            "items": [{"ref": "i1", "category": "risk", "text": "Beta risk."}],
            "relationships": [
                {
                    "from_ref": "i1",
                    "to_ref": "i1",
                    "relation": "relates_to",
                    "evidence": {"source_id": "src_a", "excerpt": "something here"},
                }
            ],
        }
    )
    merged = _merge_drafts([first, second])

    refs = [item.ref for item in merged.items]
    assert refs == ["b0:i1", "b1:i1"]
    assert merged.relationships[0].from_ref == "b1:i1"


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------

def _edge(from_id, to_id, relation, from_cat, to_cat) -> dict:
    return {
        "id": f"{from_id}->{to_id}",
        "from_item_id": from_id,
        "to_item_id": to_id,
        "relation": relation,
        "from_text": f"Item {from_id}",
        "to_text": f"Item {to_id}",
        "from_category": from_cat,
        "to_category": to_cat,
        "from_review_status": "approved",
        "to_review_status": "proposed",
    }


def test_the_map_places_items_in_category_lanes() -> None:
    data = build_map(
        [
            _edge("a", "b", "resulted_in", "decision", "pending_work"),
            _edge("c", "b", "blocks", "blocker", "pending_work"),
        ]
    )
    assert len(data.nodes) == 3
    assert len(data.edges) == 2
    assert data.lanes == ["decision", "pending_work", "blocker"]
    lanes = {n.item_id: n.lane for n in data.nodes}
    assert lanes["a"] == 0 and lanes["b"] == 1 and lanes["c"] == 2


def test_the_same_input_always_draws_the_same_map() -> None:
    edges = [
        _edge("a", "b", "resulted_in", "decision", "pending_work"),
        _edge("c", "b", "blocks", "blocker", "pending_work"),
    ]
    assert render_svg(build_map(edges)) == render_svg(build_map(edges))


def test_the_map_contains_no_navigating_links() -> None:
    """An anchor would reload the page and reset the session, signing the
    reader out. Focus is handled in session state instead."""
    svg = render_svg(build_map([_edge("a", "b", "blocks", "blocker", "pending_work")]))
    assert "<a " not in svg
    assert "href=" not in svg


def test_every_node_is_identifiable_for_focusing() -> None:
    svg = render_svg(build_map([_edge("a", "b", "blocks", "blocker", "pending_work")]))
    assert 'data-item="a"' in svg and 'data-item="b"' in svg


def test_an_empty_map_renders_nothing() -> None:
    data = build_map([])
    assert data.is_empty
    assert render_svg(data) == ""
    assert legend(set()) == ""


def test_node_text_is_escaped() -> None:
    edge = _edge("a", "b", "blocks", "blocker", "pending_work")
    edge["from_text"] = '<script>alert("x")</script>'
    svg = render_svg(build_map([edge]))
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


def test_focusing_dims_everything_else() -> None:
    edges = [
        _edge("a", "b", "resulted_in", "decision", "pending_work"),
        _edge("c", "d", "blocks", "blocker", "risk"),
    ]
    focused = render_svg(build_map(edges), focus_id="a")
    assert 'opacity="0.4"' in focused  # the unrelated nodes
    assert 'opacity="1.0"' in focused  # the focused one


# --------------------------------------------------------------------------
# Access control
# --------------------------------------------------------------------------

def test_a_viewer_may_see_the_map(seeded) -> None:
    """The map is part of the handover, so a viewer can read it."""
    outcome = seeded["service"].run(
        seeded["project"],
        FakeProvider(
            _payload(
                seeded["source_id"],
                "Email notification testing is blocked by the absence",
            )
        ),
    )
    viewer = ReconstructionService(
        seeded["connection"], principal("viewer", seeded["project"]["id"])
    )
    assert len(viewer.relationships(outcome.reconstruction_id)) == 1


def test_an_anonymous_caller_cannot_read_the_map(seeded) -> None:
    from trace_app.security.authz import Principal

    outcome = seeded["service"].run(
        seeded["project"],
        FakeProvider(
            _payload(
                seeded["source_id"],
                "Email notification testing is blocked by the absence",
            )
        ),
    )
    nobody = ReconstructionService(
        seeded["connection"],
        Principal(user_id="u", email="u@x", role="", project_id=seeded["project"]["id"]),
    )
    with pytest.raises(AccessDenied):
        nobody.relationships(outcome.reconstruction_id)
