"""Phase two (off by default): when a paper is handed in and grading is routed to Claude, wake the grading Routine through its API trigger.

CLAUDE_ROUTINE_GRADING_URL and CLAUDE_ROUTINE_GRADING_TOKEN come from the Routine's own "API" trigger. Nothing is sent unless BOTH are set and the
grading route is claude / claude_first; a failure to reach the Routine is swallowed (the tasks simply stay queued and, with 'claude first',
fall back to DeepSeek after the fallback time). The token is never logged. This path has not been exercised against Anthropic's service."""
import os
import threading


def configured():
    return bool(os.environ.get("CLAUDE_ROUTINE_GRADING_URL") and os.environ.get("CLAUDE_ROUTINE_GRADING_TOKEN"))


def _post(url, token, text):
    try:
        import httpx

        httpx.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json={"text": text}, timeout=10)
    except Exception:  # noqa: BLE001  (never stops a hand-in, never logs the token)
        pass


def fire_for_grading(conn, count):
    """Wake the grading Routine. Returns True when a request was started (in a background thread)."""
    from app import external_executor

    if count <= 0 or not configured():
        return False
    if external_executor.policy(conn)["grading"] == "deepseek":
        return False
    text = f"网站有 {count} 个新的判分任务。请按 docs/claude-routines.md 里的“判分”提示词，用 kind=grading 领取并提交。"
    threading.Thread(target=_post, args=(os.environ["CLAUDE_ROUTINE_GRADING_URL"], os.environ["CLAUDE_ROUTINE_GRADING_TOKEN"], text), daemon=True).start()
    return True
