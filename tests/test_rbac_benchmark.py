import pytest

from app.benchmark_rbac import benchmark


def test_synthetic_benchmark_compares_equal_searches_without_leaking_text() -> None:
    report = benchmark(point_count=100, repeats=2, warmups=1, top_k=5)

    assert report["passed"] is True
    assert report["point_count"] == 100
    assert report["query_count"] == 3
    assert report["repeats_per_query"] == 2
    assert report["authorized_document_count"] == 10
    assert report["unauthorized_filtered_results"] == 0
    assert report["short_result_sets"] == 0
    assert report["gemini_calls"] == 0
    assert "720000" not in str(report)


def test_benchmark_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="multiple of 5"):
        benchmark(point_count=21)
    with pytest.raises(ValueError, match="repeats"):
        benchmark(point_count=100, repeats=0)
