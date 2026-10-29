"""The user's six-file collection is the sole active source allowlist, not an access grant."""

import json
from pathlib import Path

COLLECTION = json.loads(Path(__file__).with_name("ocr_collection.json").read_text())
FILES = {f["file_id"]: f for f in COLLECTION["files"]}


def pinned_source_bytes(file_id, source_sha256):
    """True only for this collection's exact six-owner-source byte revisions.

    A valid Drive file ID, current source_documents row or self-consistent
    database checksum is not sufficient. Fail closed on a missing/mismatched
    lock file. This gate is intended for NEW automatic generation; historical
    snapshots must remain readable even after a source revision changes.
    """
    try:
        lock = json.loads(
            Path(__file__).with_name("ocr_source_sha256_lock.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError, TypeError):
        return False
    item = FILES.get(file_id)
    if not item or lock.get("collection_version") != COLLECTION["version"]:
        return False
    expected = lock.get("files", {}).get(item["key"], {})
    return (
        type(source_sha256) is str
        and len(source_sha256) == 64
        and expected.get("sha256") == source_sha256
    )



def current_source_sql(alias="d", *, kind=None, at_sql=None):
    ids = [f["file_id"] for f in FILES.values() if kind is None or f["kind"] == kind]
    literals = ",".join(
        "'" + v + "'" for v in ids
    )  # Fixed, checked-in identifiers only.
    cutoff = f" AND newer.imported_at<={at_sql}" if at_sql else ""
    return f"({alias}.file_id IN ({literals}) AND NOT EXISTS(SELECT 1 FROM source_documents newer WHERE newer.file_id={alias}.file_id AND newer.id>{alias}.id{cutoff}))"


def active_document(conn, doc_id):
    return bool(
        conn.execute(
            f"SELECT 1 FROM source_documents d WHERE d.id=%s AND {current_source_sql()}",
            (doc_id,),
        ).fetchone()
    )


def entry(metadata, path=None):
    item = FILES.get(metadata.get("file_id"))
    if (
        not item
        or metadata.get("folder_id") != COLLECTION["folder_id"]
        or metadata.get("collection_version") != COLLECTION["version"]
    ):
        raise ValueError("Only the current six-file OCR collection is allowed")
    if metadata.get("subject") != item["subject"]:
        raise ValueError("Source subject differs from the six-file manifest")
    if path is not None and Path(path).name != item["filename"]:
        raise ValueError(
            "Local filename differs from the declared current source identity"
        )
    if item["kind"] == "textbook":
        if (
            metadata.get("expected_pages") != item["expected_pages"]
            or metadata.get("volume_offsets", [0]) != [0]
            or metadata.get("volume_pages", [item["expected_pages"]])
            != [item["expected_pages"]]
        ):
            raise ValueError(
                "Corrected textbooks require continuous whole-book OCR page counts"
            )
    return item


def handoff(directory):
    root = Path(directory).resolve()
    files = []
    for item in FILES.values():
        path = root / item["relative_path"]
        if not path.resolve().is_relative_to(root):
            raise ValueError(
                "Six-file handoff paths must remain inside the supplied OCR folder"
            )
        meta = dict(
            source_mode="ocr_only",
            file_id=item["file_id"],
            folder_id=COLLECTION["folder_id"],
            collection_version=COLLECTION["version"],
            subject=item["subject"],
            title=item["filename"].rsplit(".", 1)[0],
            edition="待核查",
            authors=None,
            material_status="private_pending",
        )
        if item["kind"] == "textbook":
            meta.update(
                expected_pages=item["expected_pages"],
                volume_offsets=[0],
                volume_pages=[item["expected_pages"]],
                page_label=item["page_label"],
            )
        files.append(
            {
                **item,
                "local_path": str(path),
                "metadata": meta,
                "user_reported_located": True,
                "codex_located_local_file": path.is_file(),
                "file_read": False,
                "preflight_passed": False,
                "database_imported": False,
                "human_text_reviewed": False,
                "scientific_reviewed": False,
                "questions_published": 0,
            }
        )
    return {
        "collection_version": COLLECTION["version"],
        "folder_id": COLLECTION["folder_id"],
        "files": files,
        "expected_textbook_markers": 1915,
        "authorization_note": "IDs and user location reports are not Codex Drive access; local files remain private",
    }
