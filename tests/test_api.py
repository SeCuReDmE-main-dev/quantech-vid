from pathlib import Path

from fastapi.testclient import TestClient

from quantech_vid.api import app


client = TestClient(app)
PROJECT_ROOT = Path(__file__).parents[1]


def test_health() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["loopback"] is True


def test_validate_synthia_project() -> None:
    response = client.post(
        "/api/v1/projects/validate",
        json={"manifest_path": str(PROJECT_ROOT / "projects" / "synthia-promo" / "project.json")},
    )
    assert response.status_code == 200
    assert response.json()["duration"] == 45


def test_missing_job_is_404() -> None:
    assert client.get("/api/v1/renders/missing").status_code == 404
