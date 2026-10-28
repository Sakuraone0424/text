"""Private images kept inside PostgreSQL, with bounded decoding and no public URLs."""

import hashlib
import io
import warnings
from PIL import Image, UnidentifiedImageError
from psycopg.types.json import Jsonb
from app.training_sessions import (
    assert_writable,
    valid_value,
    state,
    server_now,
    finalize,
)


def verify_image(raw):
    if not 0 < len(raw) <= 8 * 1024 * 1024:
        raise ValueError("Image must be between 1 byte and 8 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if (
                    image.format not in ("JPEG", "PNG", "WEBP")
                    or image.width * image.height > 20_000_000
                ):
                    raise ValueError(
                        "Only bounded JPEG, PNG and WebP images are supported"
                    )
                image.verify()
                with Image.open(io.BytesIO(raw)) as decoded:
                    decoded.load()
                return {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[
                    image.format
                ]
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("Invalid or oversized image") from exc


def store_image(conn, user_id, paper_id, question_id, raw, media, expected_version):
    from app.training_sessions import locked_session

    locked_session(conn, user_id, paper_id)
    checksum = hashlib.sha256(raw).hexdigest()
    prior = conn.execute(
        "SELECT i.id,d.value FROM answer_images i JOIN answer_drafts d ON d.paper_id=i.paper_id AND d.question_id=i.question_id WHERE i.paper_id=%s AND i.question_id=%s AND i.sha256=%s",
        (paper_id, question_id, checksum),
    ).fetchone()
    if prior and prior["id"] in prior["value"].get("image_ids", []):
        return {
            "accepted": True,
            "image_id": prior["id"],
            **state(conn, user_id, paper_id),
        }
    session = assert_writable(conn, user_id, paper_id, expected_version)
    if session is None:
        return {"accepted": False, **state(conn, user_id, paper_id)}
    old = conn.execute(
        "SELECT value FROM answer_drafts WHERE paper_id=%s AND question_id=%s",
        (paper_id, question_id),
    ).fetchone()
    value = old["value"] if old else {"text": "", "image_ids": []}
    q = conn.execute(
        "SELECT snapshot FROM paper_items_v2 WHERE paper_id=%s AND question_id=%s",
        (paper_id, question_id),
    ).fetchone()
    if not q or q["snapshot"]["question_type"] in ("single_choice", "fill_blank"):
        raise ValueError("Images require a subjective question belonging to this paper")
    if len(value.get("image_ids", [])) >= 5:
        raise ValueError("At most five images per answer")
    # Bytes have already arrived; upload and decoding time is included in the deadline.
    if server_now(conn) >= session["deadline"]:
        finalize(conn, user_id, paper_id, session, timed_out=True)
        return {"accepted": False, **state(conn, user_id, paper_id)}
    image_id = conn.execute(
        "INSERT INTO answer_images(paper_id,question_id,media_type,content,sha256) VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (paper_id, question_id, media, raw, hashlib.sha256(raw).hexdigest()),
    ).fetchone()["id"]
    value = {**value, "image_ids": value.get("image_ids", []) + [image_id]}
    value = valid_value(conn, paper_id, question_id, value)
    conn.execute(
        "INSERT INTO answer_drafts VALUES (%s,%s,%s) ON CONFLICT(paper_id,question_id) DO UPDATE SET value=EXCLUDED.value",
        (paper_id, question_id, Jsonb(value)),
    )
    conn.execute(
        "UPDATE training_sessions SET version=version+1 WHERE paper_id=%s", (paper_id,)
    )
    return {"accepted": True, "image_id": image_id, **state(conn, user_id, paper_id)}
