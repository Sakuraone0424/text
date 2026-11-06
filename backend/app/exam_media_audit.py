"""Read-only DOCX image inventory for user-provided genuine exam transcriptions.

Map media bytes to document paragraph coordinates without treating an image as
transcribed, interpreted or scientifically checked. Never expose image content
or surrounding question text in the report.
"""
from collections import Counter
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
import posixpath
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile

IMAGE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
HDPHOTO_REL = "http://schemas.microsoft.com/office/2007/relationships/hdphoto"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DRAWING_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MEDIA_ROOT = "word/media/"


def _image_relationships(archive):
    rels_name = "word/_rels/document.xml.rels"
    if rels_name not in archive.namelist():
        raise ValueError("document_image_relationships_missing")
    root = ET.fromstring(archive.read(rels_name))
    images = {}
    for relation in root.findall(f"{{{REL_NS}}}Relationship"):
        if relation.get("Type") not in (IMAGE_REL, HDPHOTO_REL):
            continue
        if relation.get("TargetMode") == "External":
            raise ValueError("external_image_relationship_not_accepted")
        target = posixpath.normpath(
            posixpath.join("word", relation.get("Target", ""))
        )
        if not target.startswith(MEDIA_ROOT):
            raise ValueError("invalid_image_target")
        rid = relation.get("Id")
        if not rid or rid in images:
            raise ValueError("duplicate_or_missing_image_relationship_id")
        images[rid] = target
    return images


def audit_bytes(raw):
    """Only identities, formats and ordinal coordinates; no exam text."""
    try:
        with ZipFile(BytesIO(raw)) as archive:
            if archive.testzip():
                raise ValueError("docx_media_archive_crc_failed")
            names = set(archive.namelist())
            if "word/document.xml" not in names:
                raise ValueError("word_document_xml_missing")
            media_names = sorted(
                name for name in names
                if name.startswith(MEDIA_ROOT) and not name.endswith("/")
            )
            relationships = _image_relationships(archive)
            for target in relationships.values():
                if target not in names:
                    raise ValueError("docx_image_relationship_blob_missing")
            usages = {name: [] for name in media_names}
            root = ET.fromstring(archive.read("word/document.xml"))
            paragraphs = list(root.iter(f"{{{WORD_NS}}}p"))
            for ordinal, paragraph in enumerate(paragraphs, start=1):
                for element in paragraph.iter():
                    for attr in (
                        f"{{{DRAWING_REL_NS}}}embed",
                        f"{{{DRAWING_REL_NS}}}id",
                    ):
                        rid = element.get(attr)
                        if rid in relationships:
                            usages[relationships[rid]].append(ordinal)
            rows = []
            for name in media_names:
                raw_image = archive.read(name)
                rows.append({
                    "member": name,
                    "sha256": sha256(raw_image).hexdigest(),
                    "bytes": len(raw_image),
                    "format": PurePosixPath(name).suffix.lower().lstrip("."),
                    "paragraph_ordinals": sorted(set(usages[name])),
                    "references": len(usages[name]),
                    "transcription_verified": False,
                })
    except (BadZipFile, ET.ParseError) as exc:
        raise ValueError("invalid_exam_docx_media_archive") from exc
    formats = Counter(row["format"] for row in rows)
    return {
        "source_sha256": sha256(raw).hexdigest(),
        "media_objects": len(rows),
        "paragraphs_scanned": len(paragraphs),
        "referenced_media_objects": sum(bool(row["references"]) for row in rows),
        "unreferenced_media_objects": [
            row["member"] for row in rows if not row["references"]
        ],
        "media_formats": dict(sorted(formats.items())),
        "images_scientifically_verified": False,
        "question_image_anchors_verified": False,
        "media": rows,
    }
