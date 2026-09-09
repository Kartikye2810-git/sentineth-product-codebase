"""Source identity must survive document processing, retries and deletion."""

from uuid import uuid4

from tests.pdf_builder import build_pdf


def test_source_tracks_payload_lifecycle_and_keeps_deleted_provenance(client, organization):
    org = organization()
    root = f"/organizations/{org}"
    uploaded = client.post(
        root + "/documents",
        files={"file": ("policy.pdf", build_pdf(["Morgan owns Atlas."]), "application/pdf")},
    )
    assert uploaded.status_code == 202
    document = uploaded.json()
    source_id = document["source_id"]
    source_url = root + "/sources/" + source_id
    source = client.get(source_url).json()
    assert source["sync_state"] == "PENDING"
    assert source["document_id"] == document["id"]
    assert source["created_by_user_id"] == client.owner_id
    assert source["uri"] == uploaded.headers["Location"]
    client.process_one()
    source = client.get(source_url).json()
    assert source["sync_state"] == "SYNCED" and source["last_synced_at"]
    reindex = client.post(uploaded.headers["Location"] + "/reindex")
    assert reindex.status_code == 202 and reindex.json()["source_id"] == source_id
    assert client.get(source_url).json()["sync_state"] == "PENDING"
    client.process_one()
    assert (
        client.get(root + "/sources", params={"origin": "upload", "sync_state": "SYNCED"}).json()[
            "total"
        ]
        == 1
    )
    assert client.delete(uploaded.headers["Location"]).status_code == 202
    assert client.get(source_url).json()["sync_state"] == "DELETING"
    client.process_one()
    deleted = client.get(source_url).json()
    assert deleted["sync_state"] == "DELETED" and deleted["deleted_at"]
    assert deleted["document_id"] is None
    assert client.get(root + "/sources").json()["total"] == 0
    assert (
        client.get(root + "/sources", params={"include_deleted": True}).json()["items"][0]["id"]
        == source_id
    )
    assert client.get(uploaded.headers["Location"]).status_code == 404


def test_source_failures_and_tenant_boundary(client, organization):
    org = organization()
    root = f"/organizations/{org}"
    uploaded = client.post(
        root + "/documents", files={"file": ("invalid.pdf", b"not a PDF", "application/pdf")}
    )
    assert uploaded.status_code == 202
    source_id = uploaded.json()["source_id"]
    client.process_one()
    source = client.get(root + "/sources/" + source_id).json()
    assert source["sync_state"] == "FAILED"
    assert source["error_code"] == "EXTRACTION_FAILED"
    assert source["last_synced_at"] is None
    other = client.post("/organizations", json={"name": "Other tenant"}).json()
    headers = {"Authorization": "Bearer " + other["api_key"]}
    assert client.get(root + "/sources", headers=headers).status_code == 403
    assert client.get(root + "/sources/" + source_id, headers=headers).status_code == 403
    assert (
        client.get(f"/organizations/{other['id']}/sources/{source_id}", headers=headers).status_code
        == 404
    )
    assert client.get(root + "/sources/" + str(uuid4())).status_code == 404
