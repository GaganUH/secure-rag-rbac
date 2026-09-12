from sqlalchemy import select

import app.retrieval_routes as retrieval_routes
from app.models import DocumentPermission, Role
from app.retrieval_cache import CacheKey, RetrievalCache, semantic_retrieval_cache
from tests.test_permission_aware_retrieval import bearer, login, retrieval_test_app


def test_cache_copies_values_and_bounds_size() -> None:
    cache = RetrievalCache(max_entries=1, ttl_seconds=300)
    first_key = CacheKey(1, 1, "Admin", ((1, 1),), "salary", 3)
    second_key = CacheKey(1, 1, "Admin", ((1, 1),), "leave", 3)
    original = [{"document_id": 1, "text": "public"}]

    cache.put(first_key, original)
    original[0]["text"] = "changed"
    assert cache.get(first_key) == [{"document_id": 1, "text": "public"}]

    cache.put(second_key, [])
    assert cache.get(first_key) is None
    assert cache.get(second_key) == []


def test_semantic_cache_hit_and_database_revocation(
    retrieval_test_app, monkeypatch
) -> None:
    client, session = retrieval_test_app
    semantic_retrieval_cache.clear()
    calls = 0

    def fake_semantic_search(
        session, query, role_name, top_k, authorized_document_ids
    ):
        nonlocal calls
        calls += 1
        if 2 not in authorized_document_ids:
            return []
        return [
            {
                "document_id": 2,
                "document_title": "Salary Records",
                "chunk_id": 2,
                "chunk_index": 0,
                "page_number": 1,
                "text": "Alice has a fictional salary of 720000 rupees.",
                "score": 0.9,
            }
        ]

    monkeypatch.setattr(retrieval_routes, "semantic_search", fake_semantic_search)
    token = login(client, "admin", "Admin-password-123")
    request = {"query": "Alice compensation", "top_k": 3}

    first = client.post(
        "/retrieval/semantic-search", headers=bearer(token), json=request
    )
    second = client.post(
        "/retrieval/semantic-search", headers=bearer(token), json=request
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is True
    assert calls == 1

    admin_role = session.scalar(select(Role).where(Role.name == "Admin"))
    assert admin_role is not None
    permission = session.scalar(
        select(DocumentPermission).where(
            DocumentPermission.document_id == 2,
            DocumentPermission.role_id == admin_role.id,
        )
    )
    assert permission is not None
    session.delete(permission)
    session.commit()

    # Deliberately do not clear the cache here. The changed SQLite fingerprint
    # alone must prevent reuse of the old confidential response.
    after_revocation = client.post(
        "/retrieval/semantic-search", headers=bearer(token), json=request
    )
    assert after_revocation.status_code == 200
    assert after_revocation.json()["cache_hit"] is False
    assert after_revocation.json()["results"] == []
    assert calls == 2
    semantic_retrieval_cache.clear()
