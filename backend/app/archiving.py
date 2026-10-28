"""Consent-gated durable outbox. A successful write is not a verified archive."""

import hashlib
import json
import os
from datetime import timedelta
import httpx
from psycopg.types.json import Jsonb
from app.training_sessions import server_now


def canonical(payload):
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def digest(payload):
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def enqueue(conn, user_id, paper_id):
    consent = conn.execute(
        "SELECT enabled FROM archive_consent WHERE user_id=%s", (user_id,)
    ).fetchone()
    if not consent or not consent["enabled"]:
        return
    from app.quiz_v2 import result

    payload = result(conn, user_id, paper_id)
    paper = conn.execute(
        "SELECT paper_date FROM papers_v2 WHERE id=%s", (paper_id,)
    ).fetchone()
    payload["paper_date"] = paper["paper_date"].isoformat()
    payload["knowledge_mastery_change"] = mastery_change(conn, user_id, paper_id)
    payload["reflections"] = [
        r["data"]
        for r in conn.execute(
            "SELECT to_jsonb(r) AS data FROM answer_reflections r WHERE r.attempt_id=%s ORDER BY r.id",
            (payload["attempt_id"],),
        ).fetchall()
    ]
    payload["weekly_chapters"] = conn.execute(
        "SELECT i.snapshot->>'subject' AS subject,i.snapshot->'evidence'->0->>'chapter' AS chapter,SUM(a.points) AS graded_points,COUNT(*) FILTER(WHERE a.points IS NULL) AS pending FROM answers_v2 a JOIN attempts_v2 t ON t.id=a.attempt_id JOIN papers_v2 p ON p.id=t.paper_id JOIN paper_items_v2 i ON i.paper_id=p.id AND i.question_id=a.question_id WHERE p.user_id=%s AND t.submitted_at>=clock_timestamp()-INTERVAL '7 days' GROUP BY 1,2 ORDER BY 1,2",
        (user_id,),
    ).fetchall()
    payload["fsrs"] = conn.execute(
        "SELECT question_id,card,version FROM fsrs_cards WHERE user_id=%s ORDER BY question_id",
        (user_id,),
    ).fetchall()
    conn.execute(
        "INSERT INTO archive_jobs(attempt_id,payload,digest) VALUES (%s,%s,%s) "
        "ON CONFLICT(attempt_id) DO UPDATE SET payload=EXCLUDED.payload,digest=EXCLUDED.digest,state='pending',next_retry=clock_timestamp(),error=NULL",
        (payload["attempt_id"], Jsonb(payload), digest(payload)),
    )


def mastery_change(conn, user_id, paper_id):
    """Observed estimate changes since preparation, never attributed solely to this paper."""
    from app.selection import snapshot

    run = conn.execute(
        "SELECT context,as_of,algorithm_version,policy_id,config FROM selection_runs WHERE paper_id=%s",
        (paper_id,),
    ).fetchone()
    if not run:
        return {"status": "historical_trace_not_recorded"}
    questions = [
        r["snapshot"]
        for r in conn.execute(
            "SELECT snapshot FROM paper_items_v2 WHERE paper_id=%s ORDER BY position",
            (paper_id,),
        ).fetchall()
    ]
    current, policy, _ = snapshot(conn, user_id, questions)
    changes = []
    for subject, tags in current["topics"].items():
        for tag, signals in tags.items():
            previous = run["context"]["topics"].get(subject, {}).get(tag)
            before = previous["mastery"] if previous else None
            after = signals["mastery"]
            changes.append(
                {
                    "subject": subject,
                    "knowledge_tag": tag,
                    "before": before,
                    "after": after,
                    "gap_delta": after["gap"] - before["gap"] if before else None,
                    "answer_samples_delta": after["answer_samples"]
                    - before["answer_samples"]
                    if before
                    else None,
                }
            )
    return {
        "status": "recorded_estimates",
        "algorithm_version": current["algorithm_version"],
        "before_algorithm_version": run["algorithm_version"],
        "before_policy_id": run["policy_id"],
        "after_policy_id": current["policy_id"],
        "before_config": run["config"],
        "after_config": policy.model_dump(mode="json"),
        "before_as_of": run["as_of"].isoformat(),
        "after_as_of": current["as_of"],
        "meaning": "estimate_change_since_preparation_includes_other_training_time_decay_and_policy_changes_not_causal_gain",
        "topics": changes,
    }


class NotionAdapter:
    """Configured private database, explicit 2022-06-28 API contract."""

    def __init__(self, token, database_id, transport=None):
        self.database_id = database_id
        self.client = httpx.Client(
            base_url="https://api.notion.com/v1/",
            timeout=20,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": "2022-06-28",
            },
        )

    def call(self, method, path, body=None):
        response = self.client.request(method, path, json=body)
        response.raise_for_status()
        return response.json()

    def write(self, key, payload, remote_id=None):
        if not remote_id:
            found = self.call(
                "POST",
                f"databases/{self.database_id}/query",
                {"filter": {"property": "External ID", "rich_text": {"equals": key}}},
            )
            if len(found["results"]) > 1:
                raise ValueError(
                    "Duplicate archive identity; manual reconciliation required"
                )
            if found["results"]:
                remote_id = found["results"][0]["id"]
            else:
                page = self.call(
                    "POST",
                    "pages",
                    {
                        "parent": {"database_id": self.database_id},
                        "properties": {
                            "Name": {"title": [{"text": {"content": key}}]},
                            "External ID": {"rich_text": [{"text": {"content": key}}]},
                        },
                    },
                )
                remote_id = page["id"]
        # Append revision chunks; never delete blocks that a user may have edited.
        text = canonical(payload)
        revision = digest(payload)
        pieces = [text[n : n + 600] for n in range(0, len(text), 600)]
        existing = set()
        for block in self.children(remote_id):
            raw = "".join(
                t.get("plain_text", t.get("text", {}).get("content", ""))
                for t in block.get("paragraph", {}).get("rich_text", [])
            )
            try:
                envelope = json.loads(raw)
                if envelope.get("quiz_revision") == revision:
                    existing.add(envelope["index"])
            except (ValueError, KeyError, AttributeError):
                pass
        blocks = []
        for i, piece in enumerate(pieces):
            if i not in existing:
                envelope = canonical(
                    {
                        "quiz_revision": revision,
                        "index": i,
                        "count": len(pieces),
                        "chunk": piece,
                    }
                )
                # Unicode escaped by the outer HTTP layer; each envelope remains bounded.
                blocks.append(
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {"type": "text", "text": {"content": envelope}}
                            ]
                        },
                    }
                )
        for n in range(0, len(blocks), 100):
            self.call(
                "PATCH",
                f"blocks/{remote_id}/children",
                {"children": blocks[n : n + 100]},
            )
        return remote_id

    def children(self, remote_id):
        found = []
        cursor = None
        seen_cursors = set()
        while True:
            path = f"blocks/{remote_id}/children?page_size=100" + (
                f"&start_cursor={cursor}" if cursor else ""
            )
            result = self.call("GET", path)
            found.extend(result["results"])
            if not result.get("has_more"):
                return found
            cursor = result["next_cursor"]
            if not cursor or cursor in seen_cursors or len(seen_cursors) > 100:
                raise ValueError("Invalid archive pagination")
            seen_cursors.add(cursor)

    def read(self, remote_id):
        revisions = {}
        for block in self.children(remote_id):
            text = "".join(
                t.get("plain_text", t.get("text", {}).get("content", ""))
                for t in block.get("paragraph", {}).get("rich_text", [])
            )
            try:
                envelope = json.loads(text)
                revision = envelope["quiz_revision"]
                if (
                    not isinstance(revision, str)
                    or len(revision) != 64
                    or type(envelope.get("count")) is not int
                    or not 1 <= envelope["count"] <= 10000
                    or type(envelope.get("index")) is not int
                    or not 0 <= envelope["index"] < envelope["count"]
                    or not isinstance(envelope.get("chunk"), str)
                ):
                    continue
                group = revisions.setdefault(
                    revision, {"count": envelope["count"], "chunks": {}}
                )
                group["chunks"][envelope["index"]] = envelope["chunk"]
            except (ValueError, KeyError, TypeError):
                continue
        for revision, group in reversed(list(revisions.items())):
            if set(group["chunks"]) == set(range(group["count"])):
                payload = json.loads(
                    "".join(group["chunks"][i] for i in range(group["count"]))
                )
                if digest(payload) == revision:
                    return payload
        raise ValueError("No complete verified archive revision")

    def close(self):
        self.client.close()


def process_one(conn, adapter):
    row = conn.execute(
        "SELECT j.* FROM archive_jobs j JOIN attempts_v2 a ON a.id=j.attempt_id JOIN papers_v2 p ON p.id=a.paper_id "
        "JOIN archive_consent c ON c.user_id=p.user_id AND c.enabled "
        "WHERE j.state<>'verified' AND j.next_retry<=clock_timestamp() ORDER BY j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1"
    ).fetchone()
    if not row:
        return False
    try:
        remote = adapter.write(
            f"quiz-attempt:{row['attempt_id']}", row["payload"], row["remote_id"]
        )
        conn.execute(
            "UPDATE archive_jobs SET state='synced',remote_id=%s WHERE id=%s",
            (remote, row["id"]),
        )
        if digest(adapter.read(remote)) != row["digest"]:
            raise ValueError("Readback digest mismatch")
        conn.execute(
            "UPDATE archive_jobs SET state='verified',verified_at=clock_timestamp(),error=NULL WHERE id=%s",
            (row["id"],),
        )
    except (httpx.HTTPError, ValueError, KeyError):
        # Do not persist provider responses, private payloads or credentials in errors.
        conn.execute(
            "UPDATE archive_jobs SET state='failed',attempts=attempts+1,error='archive_write_or_readback_failed',next_retry=%s WHERE id=%s",
            (
                server_now(conn)
                + timedelta(seconds=min(3600, 30 * 2 ** min(row["attempts"], 7))),
                row["id"],
            ),
        )
    return True


def configured_adapter():
    if os.environ.get("NOTION_SYNC_ENABLED") != "1":
        return None
    token = os.environ.get("NOTION_TOKEN")
    database = os.environ.get("NOTION_DATABASE_ID")
    return NotionAdapter(token, database) if token and database else None
