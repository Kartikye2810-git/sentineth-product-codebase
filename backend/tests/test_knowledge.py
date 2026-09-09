"""Critical Phase 4 path: extraction proposals become reviewable cross-source facts."""

import asyncio
import json
import re

from app.knowledge_worker import run_once as run_knowledge_once
from app.providers.llm.base import LLMProvider
from tests.pdf_builder import build_pdf


class KnowledgeExtractor(LLMProvider):
    def __init__(self):
        self.calls = []

    async def generate(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        source = messages[1]["content"]
        chunk_id = re.search(r"CHUNK ([0-9a-f-]+)", source).group(1)
        if "Morgan" in source:
            result = {
                "entities": [
                    {"chunk_id": chunk_id, "mention": "Morgan", "name": "Morgan",
                     "type": "person", "confidence": 0.95},
                    {"chunk_id": chunk_id, "mention": "Atlas", "name": "Atlas",
                     "type": "project", "confidence": 0.96},
                ],
                "relationships": [
                    {"chunk_id": chunk_id, "subject_name": "Morgan", "subject_type": "person",
                     "predicate": "leads", "object_name": "Atlas", "object_type": "project",
                     "confidence": 0.91}
                ],
            }
        else:
            result = {
                "entities": [
                    {"chunk_id": chunk_id, "mention": "Atlas", "name": "Atlas",
                     "type": "project", "confidence": 0.94},
                    {"chunk_id": chunk_id, "mention": "Aurora", "name": "Decision Aurora",
                     "type": "decision", "confidence": 0.93},
                ],
                "relationships": [
                    {"chunk_id": chunk_id, "subject_name": "Atlas", "subject_type": "project",
                     "predicate": "adopted", "object_name": "Decision Aurora",
                     "object_type": "decision", "confidence": 0.9,
                     "valid_from": "2026-07-01T00:00:00Z"}
                ],
            }
        return json.dumps(result)


def upload_and_process(client, org, filename, sentence):
    uploaded = client.post(
        f"/organizations/{org}/documents",
        files={"file": (filename, build_pdf([sentence]), "application/pdf")},
    )
    assert uploaded.status_code == 202
    assert client.process_one()
    return uploaded.json()


def accept(client, root, proposal, **selection):
    reviewed = client.patch(
        f"{root}/knowledge/proposals/{proposal['id']}",
        json={"decision": "accept", **selection},
    )
    assert reviewed.status_code == 200, reviewed.text
    return reviewed.json()


def test_reviewed_two_source_graph_answers_and_reindex_withdraws_stale_fact(
    client, organization, db_session_factory, llm_provider
):
    org = organization()
    root = f"/organizations/{org}"
    first = upload_and_process(client, org, "ownership.pdf", "Morgan leads Project Atlas.")
    second = upload_and_process(
        client, org, "decision.pdf", "Project Atlas adopted Decision Aurora on July 1, 2026."
    )

    extractor = KnowledgeExtractor()
    assert asyncio.run(run_knowledge_once(db_session_factory, extractor))
    assert asyncio.run(run_knowledge_once(db_session_factory, extractor))
    assert all("untrusted source text" in call[0][0]["content"] for call in extractor.calls)

    proposals = client.get(root + "/knowledge/proposals").json()
    assert len(proposals) == 6

    # Extraction confidence never makes model output authoritative on its own.
    before = client.post(root + "/query", json={"query": "What decision is connected to Morgan?"})
    assert before.status_code == 200
    assert "REVIEWED FACT" not in llm_provider.calls[-1][1]["content"]

    entity_proposals = [item for item in proposals if item["kind"] == "entity"]
    relationship_proposals = [item for item in proposals if item["kind"] == "relationship"]
    leads = next(item for item in relationship_proposals if item["payload"]["predicate"] == "LEADS")
    adopted = next(item for item in relationship_proposals
                   if item["payload"]["predicate"] == "ADOPTED")
    morgan = accept(client, root, next(item for item in entity_proposals
        if item["payload"]["name"] == "Morgan"))
    atlas_first = accept(client, root, next(item for item in entity_proposals
        if item["payload"]["name"] == "Atlas" and item["chunk_id"] == leads["chunk_id"])).get(
            "resolved_entity_id")
    # Resolve the second Atlas mention to the canonical project instead of merging by name.
    accept(client, root, next(item for item in entity_proposals
        if item["payload"]["name"] == "Atlas" and item["chunk_id"] == adopted["chunk_id"]),
        entity_id=atlas_first)
    aurora = accept(client, root, next(item for item in entity_proposals
        if item["payload"]["name"] == "Decision Aurora"))

    morgan_id = morgan["resolved_entity_id"]
    aurora_id = aurora["resolved_entity_id"]
    accept(client, root, leads, subject_id=morgan_id, object_id=atlas_first)
    accept(client, root, adopted, subject_id=atlas_first, object_id=aurora_id)

    answer = client.post(root + "/query", json={
        "query": "What decision is connected to Morgan through Atlas?",
        "as_of": "2026-09-01T00:00:00Z",
    })
    assert answer.status_code == 200, answer.text
    relationship_sources = [item for item in answer.json()["sources"]
                            if item["kind"] == "relationship"]
    assert {item["source_id"] for item in relationship_sources} == {
        first["source_id"], second["source_id"]
    }
    prompt = llm_provider.calls[-1][1]["content"]
    assert "Morgan --LEADS--> Atlas" in prompt
    assert "Atlas --ADOPTED--> Decision Aurora" in prompt

    # Reindexing the supporting source removes its old proposals/evidence and
    # withdraws the now-unsupported relationship before fresh extraction runs.
    queued = client.post(f"{root}/documents/{second['id']}/reindex")
    assert queued.status_code == 202
    assert client.process_one()
    active = client.get(root + "/knowledge/relationships").json()
    assert [item["predicate"] for item in active] == ["LEADS"]
    history = client.get(root + "/knowledge/relationships", params={"include_inactive": True}).json()
    assert {item["status"] for item in history} == {"CONFIRMED", "WITHDRAWN"}
