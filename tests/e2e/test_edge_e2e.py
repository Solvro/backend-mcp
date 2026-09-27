import pytest

pytestmark = pytest.mark.e2e


def test_liveness_answers_through_the_tls_edge(edge):
    response = edge.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_is_ok_when_every_dependency_is_up(edge):
    response = edge.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok", body
    assert {name: check["status"] for name, check in body["checks"].items()} == {
        "mongo": "up",
        "redis": "up",
        "mcp": "up",
    }
