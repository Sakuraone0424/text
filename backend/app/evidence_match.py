"""Layered matching of a quoted excerpt against the paragraphs that were sent with one request.

An OCR paragraph has line breaks, full-width marks and stray spaces; the model, quoting it, tidies them up.
Requiring a byte-for-byte quote therefore threw away good questions as "invented". `locate` tries, from the
strictest to the loosest, and stops at the first layer that finds ONE place:

  1 exact                        the quote as written
  2 normalized                   no whitespace, full/half width, one punctuation family, super/subscripts,
                                 line-end hyphenation removed
  3 letters_only                 only letters, digits and Chinese characters are compared
  4 ellipsis                     "A ... B": every part found in the same paragraph, in order
  5 adjacent_paragraphs          one quote spanning two neighbouring paragraphs sent with the request
  6 strict_fuzzy                 similarity >= 0.95, quote >= 8 characters, and every number, English term, Greek
                                 letter, formula and polarity word (有/无, 增/减, 升/降 ...) identical to the source
                                 (a changed fact never passes);
                                 the OCR confusion table is only used here

Rules that never bend: only the paragraphs handed to `locate` are searched (never the rest of the library);
the match must be unique (several different places = no match); and what is stored is always the database
text itself, never the model's wording.
"""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

LAYER_NAMES = {1: "exact", 2: "normalized", 3: "letters_only", 4: "ellipsis", 5: "adjacent_paragraphs", 6: "strict_fuzzy"}

FUZZY_THRESHOLD = 0.95
FUZZY_MIN_LENGTH = 8
ELLIPSIS_MIN_PART = 4
ADJACENT_MIN_LENGTH = 8

_PUNCT = {}
for chars, canon in (
    ("。．｡", "."), ("，、", ","), ("；", ";"), ("：", ":"), ("（", "("), ("）", ")"),
    ("「」『』“”＂", '"'), ("‘’＇`´", "'"),
    ("—–‐‑‒―─－﹣−", "-"), ("～〜∼", "~"),
):
    for ch in chars:
        _PUNCT[ch] = canon
# OCR look-alikes (layer 6 only). Both sides are mapped, so a mix-up on either side still lines up.
_CONFUSION = {"一": "-", "O": "0", "o": "0", "l": "1", "I": "1", "|": "1", "囗": "口", "己": "已", "巳": "已", "曰": "日", "末": "未", "士": "土"}

_HYPHEN_BREAK = re.compile(r"(?<=[A-Za-z])[-‐‑]\s*\n\s*(?=[a-z])")
_ELLIPSIS_SPLIT = re.compile(r"(?:…+|⋯+|\.{3,}|。{2,}|·{3,})")
_TOKEN = re.compile(r"\d+(?:\.\d+)?|[A-Za-z][A-Za-z0-9]*\+?|[Α-Ωα-ω]")


@dataclass
class Piece:
    unit_id: int
    text: str  # the database's own words


@dataclass
class Match:
    ok: bool
    layer: int | None = None
    pieces: list = field(default_factory=list)
    reason: str | None = None  # no_match | ambiguous | too_short | empty

    @property
    def layer_name(self):
        return LAYER_NAMES.get(self.layer)


def fold(text, level):
    """(normalized string, origin index of each normalized character). level 2 or 3."""
    skip = set()
    for m in _HYPHEN_BREAK.finditer(text):
        skip.update(range(m.start(), m.end()))
    out, origin = [], []
    for i, ch in enumerate(text):
        if i in skip:
            continue
        for c in unicodedata.normalize("NFKC", ch):
            if c.isspace() or c in "​﻿­":
                continue
            c = _PUNCT.get(c, c)
            if level >= 3 and not c.isalnum():
                continue
            out.append(c)
            origin.append(i)
    if level == 2:
        # "...", "……" and "…" are one ellipsis
        out2, origin2, k = [], [], 0
        while k < len(out):
            if out[k] in ".…":
                j = k
                while j < len(out) and out[j] in ".…":
                    j += 1
                run = out[k:j]
                if "…" in run or len(run) >= 3:
                    out2.append("…")
                    origin2.append(origin[k])
                else:
                    out2.extend(run)
                    origin2.extend(origin[k:j])
                k = j
            else:
                out2.append(out[k])
                origin2.append(origin[k])
                k += 1
        out, origin = out2, origin2
    return "".join(out), origin


def _slice(text, origin, start, end):
    """The original text covered by normalized characters [start, end)."""
    return text[origin[start]: origin[end - 1] + 1].strip()


def _occurrences(hay, needle):
    found, at = [], hay.find(needle)
    while at != -1:
        found.append(at)
        at = hay.find(needle, at + 1)
    return found


def _unique(candidates):
    """candidates: list of (key, pieces). One distinct key = unique; none = no_match; several = ambiguous."""
    keys = {k for k, _ in candidates}
    if not candidates:
        return None, "no_match"
    if len(keys) > 1:
        return None, "ambiguous"
    return candidates[0][1], None


def _by_scope(units, cited_unit_id):
    """The cited paragraph first (when it is one of the request's); the whole request only if that finds nothing."""
    ids = {u["unit_id"] for u in units}
    scopes = []
    if cited_unit_id in ids:
        scopes.append([u for u in units if u["unit_id"] == cited_unit_id])
    scopes.append(units)
    return scopes


# ---- layers --------------------------------------------------------------------------------------------

def _layer_text(quote, units, level):
    q = quote.strip() if level == 1 else fold(quote, level)[0]
    if not q:
        return []
    candidates = []
    for u in units:
        if level == 1:
            hay, origin = u["text"], None
            for at in _occurrences(hay, q):
                candidates.append(((u["unit_id"], q), [Piece(u["unit_id"], q)]))
        else:
            hay, origin = fold(u["text"], level)
            for at in _occurrences(hay, q):
                piece = _slice(u["text"], origin, at, at + len(q))
                candidates.append(((u["unit_id"], piece), [Piece(u["unit_id"], piece)]))
    return candidates


def _layer_ellipsis(quote, units):
    parts = [p for p in _ELLIPSIS_SPLIT.split(quote) if p.strip()]
    if len(parts) < 2:
        return [], False
    for level in (2, 3):
        folded = [fold(p, level)[0] for p in parts]
        if any(len(f) < ELLIPSIS_MIN_PART for f in folded):
            return [], True
        candidates = []
        for u in units:
            hay, origin = fold(u["text"], level)
            for first in _occurrences(hay, folded[0]):
                pos, spans, ok = first, [(first, first + len(folded[0]))], True
                pos = first + len(folded[0])
                for f in folded[1:]:
                    at = hay.find(f, pos)
                    if at == -1:
                        ok = False
                        break
                    spans.append((at, at + len(f)))
                    pos = at + len(f)
                if ok:
                    pieces = [Piece(u["unit_id"], _slice(u["text"], origin, a, b)) for a, b in spans]
                    candidates.append(((u["unit_id"], tuple(p.text for p in pieces)), pieces))
        if candidates:
            return candidates, True
    return [], True


def _adjacent_pairs(units):
    ordered = sorted(units, key=lambda u: u["unit_id"])
    for a, b in zip(ordered, ordered[1:]):
        if b["unit_id"] == a["unit_id"] + 1 and a.get("file_id") == b.get("file_id") and a.get("source_sha256") == b.get("source_sha256"):
            yield a, b


def _layer_adjacent(quote, units):
    for level in (2, 3):
        q = fold(quote, level)[0]
        if len(q) < ADJACENT_MIN_LENGTH:
            return []
        candidates = []
        for a, b in _adjacent_pairs(units):
            ha, oa = fold(a["text"], level)
            hb, ob = fold(b["text"], level)
            joined = ha + hb
            for at in _occurrences(joined, q):
                end = at + len(q)
                if at < len(ha) < end:  # it really runs across the break (otherwise an earlier layer would have it)
                    pa = _slice(a["text"], oa, at, len(ha))
                    pb = _slice(b["text"], ob, 0, end - len(ha))
                    candidates.append(((a["unit_id"], b["unit_id"], pa, pb), [Piece(a["unit_id"], pa), Piece(b["unit_id"], pb)]))
        if candidates:
            return candidates
    return []


# Words whose swap reverses a scientific statement (有/无, 增/减, 升/降, 抑制/促进, 上/下 ...): a change in any of
# them is a changed fact, not an OCR slip, so the fuzzy layer treats them like numbers and terms.
_POLARITY = set("无有不非未没否增减升降抑促正负高低多少大小上下内外前后正反吸放")


def _tokens(text):
    folded = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text))
    counts = Counter(_TOKEN.findall(folded))
    counts.update("字:" + c for c in folded if c in _POLARITY)
    return counts


def _confuse(s):
    return "".join(_CONFUSION.get(c, c) for c in s)


def _layer_fuzzy(quote, units):
    q = fold(quote, 2)[0]
    if len(q) < FUZZY_MIN_LENGTH:
        return [], True
    qc = _confuse(q)
    wanted = _tokens(quote)
    candidates = []
    for u in units:
        hay, origin = fold(u["text"], 2)
        hc = _confuse(hay)
        blocks = [b for b in SequenceMatcher(None, qc, hc, autojunk=False).get_matching_blocks() if b.size >= 3]
        seen = []
        for blk in blocks:
            start = blk.b - blk.a
            for length in (len(qc), len(qc) - 1, len(qc) + 1, len(qc) - 2, len(qc) + 2):
                lo, hi = max(0, start), min(len(hc), start + length)
                if hi - lo < FUZZY_MIN_LENGTH:
                    continue
                ratio = SequenceMatcher(None, qc, hc[lo:hi], autojunk=False).ratio()
                if ratio >= FUZZY_THRESHOLD:
                    seen.append((ratio, lo, hi))
        # overlapping windows are one place: keep the best of each cluster
        seen.sort(key=lambda t: (-t[0], t[1]))
        kept = []
        for ratio, lo, hi in seen:
            if all(hi <= klo or lo >= khi for _, klo, khi in kept):
                kept.append((ratio, lo, hi))
        for ratio, lo, hi in kept:
            piece = _slice(u["text"], origin, lo, hi)
            if _tokens(piece) != wanted:  # a number, term, Greek letter or formula differs: never a match
                continue
            candidates.append(((u["unit_id"], piece), [Piece(u["unit_id"], piece)]))
    return candidates, True


def locate(quote, units, cited_unit_id=None):
    """Find `quote` among `units` ([{unit_id, text, file_id, source_sha256}], exactly those sent to the model)."""
    if not isinstance(quote, str) or not quote.strip():
        return Match(False, reason="empty")
    quote = quote.strip()
    outcome = Match(False, reason="no_match")
    for scope in _by_scope(units, cited_unit_id):
        for layer in (1, 2, 3, 4, 5, 6):
            if layer <= 3:
                candidates = _layer_text(quote, scope, layer)
            elif layer == 4:
                candidates, _ = _layer_ellipsis(quote, scope)
            elif layer == 5:
                if scope is not units:
                    continue  # a neighbour is, by definition, outside the single cited paragraph
                candidates = _layer_adjacent(quote, scope)
            else:
                candidates, _ = _layer_fuzzy(quote, scope)
            if not candidates:
                continue
            pieces, why = _unique(candidates)
            if pieces:
                return Match(True, layer, pieces)
            outcome = Match(False, reason=why)  # several places: stop loosening, report it
            break
        if outcome.reason == "ambiguous":
            break
    return outcome
