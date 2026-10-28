"""Strict private six-file ZIP handoff verification, never a database import."""

import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile
from app.source_files import sha, read_bounded, MAX_OCR_BYTES
from app.source_policy import COLLECTION, FILES
from app.source_intake import private_json

MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024


def unpack(archive, destination):
    archive = Path(archive)
    destination = Path(destination)
    if destination.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError("Private handoff must stay outside the Git checkout")
    if destination.exists():
        raise ValueError(
            "Private handoff destination must be new; never overwrite evidence"
        )
    # Read into a bounded immutable snapshot so hash and extraction use identical bytes.
    raw = read_bounded(archive, MAX_ARCHIVE_BYTES)
    import io

    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        infos = bundle.infolist()
        names = [i.filename for i in infos]
        if (
            len(infos) > 16
            or len(set(names)) != len(names)
            or sum(i.file_size for i in infos) > MAX_EXPANDED_BYTES
        ):
            raise ValueError("ZIP entry count, duplicates or expansion limit rejected")
        for info in infos:
            path = PurePosixPath(info.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in info.filename
                or info.is_dir()
                or stat.S_ISLNK(info.external_attr >> 16)
                or info.flag_bits & 1
            ):
                raise ValueError(
                    "Unsafe path, symlink, directory or encrypted ZIP entry"
                )
            if (
                info.file_size > MAX_OCR_BYTES
                or info.file_size > max(1, info.compress_size) * 200
            ):
                raise ValueError("ZIP member size or compression ratio exceeds limits")
        if "manifest.json" not in names:
            raise ValueError("Missing handoff manifest")
        manifest_raw = bundle.read("manifest.json")
        if len(manifest_raw) > 1024 * 1024:
            raise ValueError("Manifest too large")
        manifest = json.loads(manifest_raw.decode("utf-8-sig"))
        if (
            manifest.get("schema") != "private-ocr-handoff/v1"
            or manifest.get("source_folder_id") != COLLECTION["folder_id"]
        ):
            raise ValueError("Handoff manifest schema or source folder differs")
        entries = manifest.get("files")
        if not isinstance(entries, list) or len(entries) != 6:
            raise ValueError("Manifest must describe exactly the current six files")
        ids = set()
        expected = {"manifest.json"}
        verified = []
        content = {}
        for entry in entries:
            item = FILES.get(entry.get("google_drive_file_id"))
            if not item or item["file_id"] in ids:
                raise ValueError("Unknown or repeated source file identity")
            member = "files/" + item["relative_path"]
            if (
                entry.get("archive_path") != member
                or entry.get("filename") != item["filename"]
                or member not in names
            ):
                raise ValueError(
                    "Manifest path or filename differs from current six-file collection"
                )
            if type(entry.get("bytes")) is not int or not re.fullmatch(
                "[0-9a-f]{64}", entry.get("sha256", "")
            ):
                raise ValueError("Manifest byte count or SHA-256 is invalid")
            body = bundle.read(member)
            if len(body) != entry["bytes"] or sha(body) != entry["sha256"]:
                raise ValueError(
                    "Handoff checksum or byte length mismatch; no files extracted"
                )
            ids.add(item["file_id"])
            expected.add(member)
            content[member] = body
            verified.append(
                {
                    "key": item["key"],
                    "file_id": item["file_id"],
                    "filename": item["filename"],
                    "archive_path": member,
                    "bytes": len(body),
                    "sha256": sha(body),
                    "manifest_match": True,
                }
            )
        for name in set(names) - expected:
            if (
                len(PurePosixPath(name).parts) != 1
                or not name.startswith("README")
                or not name.endswith((".txt", ".md"))
                or bundle.getinfo(name).file_size > 64 * 1024
            ):
                raise ValueError("Unexpected ZIP file outside the six-file handoff")
        # All identities and checksums verified before any destination is created.
        destination.mkdir(mode=0o700, parents=True)
        for name in names:
            path = destination.joinpath(*PurePosixPath(name).parts)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content[name] if name in content else bundle.read(name))
    report = {
        "archive_sha256": sha(raw),
        "manifest_sha256": sha(manifest_raw),
        "files": verified,
        "hash_matches": 6,
        "source_identity_basis": "user_private_handoff_manifest_not_independent_Drive_access",
        "database_imported": 0,
        "human_verified": 0,
        "published_questions": 0,
    }
    private_json(destination / "handoff-verification.json", report)
    return report


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    result = unpack(args.archive, args.destination)
    print(
        f"Private handoff verified: {result['hash_matches']}/6 checksums; no database import"
    )


if __name__ == "__main__":
    main()
