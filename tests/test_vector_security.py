from qdrant_client import QdrantClient, models

import app.vector_service as vector_service


def build_test_vector_store() -> QdrantClient:
    client = QdrantClient(":memory:")
    client.create_collection(
        collection_name=vector_service.VECTOR_COLLECTION_NAME,
        vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE),
    )
    client.upsert(
        collection_name=vector_service.VECTOR_COLLECTION_NAME,
        points=[
            models.PointStruct(
                id=1,
                vector=[1.0, 0.0],
                payload={
                    "chunk_id": 1,
                    "document_id": 1,
                    "document_title": "Employee Handbook",
                    "chunk_index": 0,
                    "page_number": 1,
                    "text": "Staff may telecommute twice weekly.",
                    "allowed_roles": ["Employee", "HR", "Admin"],
                },
            ),
            models.PointStruct(
                id=2,
                vector=[1.0, 0.0],
                payload={
                    "chunk_id": 2,
                    "document_id": 2,
                    "document_title": "Salary Records",
                    "chunk_index": 0,
                    "page_number": 1,
                    "text": "Alice earns 720000 rupees.",
                    "allowed_roles": ["HR", "Admin"],
                },
            ),
        ],
    )
    return client


def test_vector_search_filters_employee_before_results() -> None:
    client = build_test_vector_store()

    results = vector_service.query_authorized_vectors(
        client=client,
        query=[1.0, 0.0],
        role_name="Employee",
        authorized_document_ids=[1],
        top_k=10,
    )

    assert [result["document_title"] for result in results] == [
        "Employee Handbook"
    ]
    assert "720000" not in str(results)


def test_vector_search_allows_admin_confidential_result() -> None:
    client = build_test_vector_store()

    results = vector_service.query_authorized_vectors(
        client=client,
        query=[1.0, 0.0],
        role_name="Admin",
        authorized_document_ids=[1, 2],
        top_k=10,
    )

    assert {result["document_title"] for result in results} == {
        "Employee Handbook",
        "Salary Records",
    }


def test_stale_vector_role_cannot_override_database_revocation() -> None:
    client = build_test_vector_store()

    results = vector_service.query_authorized_vectors(
        client=client,
        query=[1.0, 0.0],
        role_name="HR",
        authorized_document_ids=[],
        top_k=10,
    )

    assert results == []
