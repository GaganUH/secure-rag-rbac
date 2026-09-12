"""Compare identical synthetic vector searches with and without RBAC filters.

This isolates the Qdrant filter cost. It does not measure login, SQLite checks,
embedding, cache behavior, HTTP, Gemini, or end-to-end production performance.
"""

import argparse
import json
import random
from datetime import datetime, timezone
from math import ceil
from statistics import median
from time import perf_counter

from qdrant_client import QdrantClient, models

from app.config import PROJECT_ROOT


COLLECTION = "synthetic_rbac_benchmark"
VECTOR_SIZE = 8
POINTS_PER_DOCUMENT = 5


def _summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "median": round(median(ordered), 3),
        "p95_nearest_rank": round(ordered[ceil(0.95 * len(ordered)) - 1], 3),
    }


def benchmark(
    point_count: int = 1000,
    repeats: int = 20,
    warmups: int = 3,
    top_k: int = 10,
) -> dict:
    """Time only local vector search; vary the filter and nothing else."""
    if point_count < 20 or point_count % POINTS_PER_DOCUMENT:
        raise ValueError("--points must be a multiple of 5 and at least 20.")
    if repeats < 1 or warmups < 0 or not 1 <= top_k <= 10:
        raise ValueError("Use repeats >= 1, warmups >= 0, and top_k from 1 to 10.")

    rng = random.Random(20260912)
    vectors = [
        [rng.random() for _ in range(VECTOR_SIZE)]
        for _ in range(point_count)
    ]
    document_count = point_count // POINTS_PER_DOCUMENT
    permitted_ids = list(range(2, document_count + 1, 2))
    access_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="allowed_roles", match=models.MatchValue(value="Employee")
            ),
            models.FieldCondition(
                key="document_id", match=models.MatchAny(any=permitted_ids)
            ),
        ]
    )
    queries = [vectors[5], vectors[point_count // 2], vectors[-1]]
    samples: dict[str, list[float]] = {"unfiltered": [], "rbac_filtered": []}
    unauthorized_filtered_results = 0
    short_result_sets = 0

    client = QdrantClient(":memory:")
    try:
        client.create_collection(
            COLLECTION,
            vectors_config=models.VectorParams(
                size=VECTOR_SIZE, distance=models.Distance.COSINE
            ),
        )
        for start in range(0, point_count, 100):
            client.upsert(
                collection_name=COLLECTION,
                points=[
                    models.PointStruct(
                        id=index + 1,
                        vector=vectors[index],
                        payload={
                            "document_id": index // POINTS_PER_DOCUMENT + 1,
                            "allowed_roles": (
                                ["Employee", "Admin"]
                                if (index // POINTS_PER_DOCUMENT + 1) % 2 == 0
                                else ["Admin"]
                            ),
                        },
                    )
                    for index in range(start, min(start + 100, point_count))
                ],
            )

        def search(vector: list[float], filtered: bool) -> list[int]:
            response = client.query_points(
                collection_name=COLLECTION,
                query=vector,
                query_filter=access_filter if filtered else None,
                limit=top_k,
                with_payload=False,
                with_vectors=False,
            )
            return [int(point.id) for point in response.points]

        for _ in range(warmups):
            for vector in queries:
                search(vector, False)
                search(vector, True)

        # Alternate order so whichever query runs second does not always benefit
        # from the first query warming the local search machinery.
        for repeat in range(repeats):
            for query_index, vector in enumerate(queries):
                for filtered in ((False, True) if (repeat + query_index) % 2 == 0 else (True, False)):
                    started = perf_counter()
                    ids = search(vector, filtered)
                    elapsed_ms = (perf_counter() - started) * 1000
                    samples["rbac_filtered" if filtered else "unfiltered"].append(elapsed_ms)
                    short_result_sets += len(ids) != top_k
                    if filtered:
                        unauthorized_filtered_results += sum(
                            ((point_id - 1) // POINTS_PER_DOCUMENT + 1) not in permitted_ids
                            for point_id in ids
                        )
    finally:
        client.close()

    unfiltered = _summary(samples["unfiltered"])
    filtered = _summary(samples["rbac_filtered"])
    delta = filtered["median"] - unfiltered["median"]
    return {
        "benchmark": "synthetic local Qdrant vector filter comparison",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "point_count": point_count,
        "document_count": document_count,
        "authorized_document_count": len(permitted_ids),
        "query_count": len(queries),
        "repeats_per_query": repeats,
        "warmups_per_query": warmups,
        "top_k": top_k,
        "gemini_calls": 0,
        "unfiltered_search_ms": unfiltered,
        "rbac_filtered_search_ms": filtered,
        "filter_minus_unfiltered_median_ms": round(delta, 3),
        "filter_overhead_percent_of_unfiltered_median": round(
            100 * delta / unfiltered["median"], 2
        ) if unfiltered["median"] else None,
        "unauthorized_filtered_results": unauthorized_filtered_results,
        "short_result_sets": short_result_sets,
        "passed": unauthorized_filtered_results == 0 and short_result_sets == 0,
        "limitations": [
            "Invented vectors and roles only; no user documents or Gemini calls.",
            "Only Qdrant search is timed, with equal top_k and no payload in both arms.",
            "Embedding, SQLite permission lookup/recheck, cache, HTTP, and answer generation are excluded.",
            "Single in-memory local run; compare repeated runs before drawing conclusions.",
            "Unfiltered search is an unsafe baseline for measurement only, never used by the RAG API.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--points", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=20)
    args = parser.parse_args()
    report = benchmark(point_count=args.points, repeats=args.repeats)
    report_dir = PROJECT_ROOT / "reports"
    report_dir.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    report_path = report_dir / f"rbac_filter_benchmark_{timestamp}.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Result: {'PASS' if report['passed'] else 'FAIL'}")
    print(f"Synthetic points: {report['point_count']}; searches per arm: {report['query_count'] * report['repeats_per_query']}")
    print(f"Unfiltered median: {report['unfiltered_search_ms']['median']} ms")
    print(f"RBAC-filtered median: {report['rbac_filtered_search_ms']['median']} ms")
    print(f"Filter minus unfiltered median: {report['filter_minus_unfiltered_median_ms']} ms")
    print(f"Unauthorized filtered results: {report['unauthorized_filtered_results']}")
    print(f"Report: {report_path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
