"""Regression for Railway Docker COPY keeping editorial source ledger inside image."""
from pathlib import Path


def test_source_review_ledger_is_packaged_in_running_backend_image():
    backend = Path(__file__).resolve().parents[1]
    docker = (backend / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY backend/app ./app" in docker
    assert (
        "COPY backend/data/qa_editorial_12_source_answers_20261030.json "
        "./data/qa_editorial_12_source_answers_20261030.json"
    ) in docker
    assert (
        backend / "data" / "qa_editorial_12_source_answers_20261030.json"
    ).is_file()
