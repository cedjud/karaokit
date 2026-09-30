"""Lyrics verification: Whisper transcript + LRCLIB reference lyrics -> corrected, timed segments.

1. Look the song up on LRCLIB (plain and, when available, line-synced lyrics).
2. An LLM maps each Whisper segment to the reference lines it covers; the reference supplies the text.
3. Reference words are aligned to Whisper's words to take their timestamps; words Whisper missed are
   interpolated within the segment, words only Whisper heard are dropped.
4. With synced lyrics, lines whose Whisper timing disagrees with the LRC (after a global offset)
   get their word timings re-inferred from the LRC window, and lines Whisper skipped are added.

Local run (needs OPENAI_API_KEY and the openai package):
  uv run --with openai --env-file .env worker/lyrics.py public/transcripts/<id>.json public/meta/<id>.json
"""

import difflib
import json
import os
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request

LRCLIB = "https://lrclib.net/api"
USER_AGENT = "music-wheel (https://github.com/cedjud/music-wheel)"
MODEL = os.environ.get("LYRICS_MODEL", "gpt-5-mini")

# Max difference between our audio's and an LRCLIB record's duration (stricter than /get's own ±2s).
DURATION_TOLERANCE = 1
# A line whose Whisper start is further than this from its (offset) LRC start gets LRC-inferred timings.
MAX_DRIFT = 1.5
# Offset estimates need this many matched lines, agreeing within MAX_SPREAD seconds, to be trusted.
MIN_MATCHES = 3
MAX_SPREAD = 3.0
# Inferred lines don't stretch over the whole gap to the next line: at most this long per syllable.
SYLLABLE_SECONDS = 0.4
# Lines shorter than this per word have timings too compressed to fill word by word.
MIN_WORD_SECONDS = 0.1


# --- LRCLIB ---

LRC_LINE = re.compile(r"^\[(\d+):(\d+(?:\.\d+)?)\][ \t]*(.*)$", re.M)


def _get(path: str, **params) -> dict | list | None:
    url = f"{LRCLIB}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    # LRCLIB returns the odd 5xx under load.
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                return json.load(res)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code < 500 or attempt == 2:
                raise
        time.sleep(2**attempt)


# Whether an LRCLIB record is the same version (edit, remix, live...) as our audio: its duration must
# match, and synced lyrics can't run past the end of our track.
def same_version(record: dict, duration: float) -> bool:
    if abs((record.get("duration") or 0) - duration) > DURATION_TOLERANCE:
        return False
    if not (record.get("syncedLyrics") or record.get("plainLyrics") or record.get("instrumental")):
        return False
    stamps = LRC_LINE.findall(record.get("syncedLyrics") or "")
    return not stamps or int(stamps[-1][0]) * 60 + float(stamps[-1][1]) <= duration


# Exact match first, then the closest-duration search hit. Records of other versions are never used.
def lookup(artist: str, title: str, duration: float) -> dict | None:
    # Without an artist (title-only songs) /get is a 400: search by title alone.
    if artist:
        hit = _get("get", artist_name=artist, track_name=title, duration=round(duration))
        if hit and same_version(hit, duration):
            return hit
    results = _get("search", artist_name=artist, track_name=title) or [] if artist else []
    if not results:
        results = _get("search", q=f"{artist} {title}".strip()) or []
    matches = [r for r in results if same_version(r, duration)]
    # Prefer synced lyrics, then the nearest duration.
    matches.sort(key=lambda r: (not r.get("syncedLyrics"), abs(r["duration"] - duration)))
    return matches[0] if matches else None



# "[01:23.45] text" -> {start, end, text}; empty LRC lines only mark where the previous line ends.
def parse_synced(lrc: str, duration: float) -> list[dict]:
    stamps = []
    for raw in lrc.splitlines():
        m = LRC_LINE.match(raw.strip())
        if m:
            stamps.append((int(m[1]) * 60 + float(m[2]), m[3].strip()))
    lines = []
    for i, (start, text) in enumerate(stamps):
        if not text:
            continue
        end = stamps[i + 1][0] if i + 1 < len(stamps) else min(start + 5, max(duration, start))
        lines.append({"start": start, "end": end, "text": text})
    return lines


def parse_plain(text: str) -> list[dict]:
    return [{"text": t.strip()} for t in text.splitlines() if t.strip()]


# --- Whisper transcript ---


# Same heuristics as isNoise in src/transcript.ts.
def is_noise(s: dict) -> bool:
    return (
        s["end"] - s["start"] < 0.25
        or s["avg_logprob"] < -1.5
        or re.search(r"thanks for watching|subscribe", s["text"], re.I) is not None
    )


# Groups Whisper's flat word list into its segments by start time, as toSegments does, minus noise.
def whisper_segments(transcript: dict) -> list[dict]:
    segments = [
        {"id": i, "start": s["start"], "end": s["end"], "text": s["text"].strip(), "words": [], "noise": is_noise(s)}
        for i, s in enumerate(transcript["segments"])
    ]
    i = 0
    for word in transcript["words"]:
        while i < len(segments) - 1 and word["start"] >= segments[i + 1]["start"]:
            i += 1
        segments[i]["words"].append(word)
    return [s for s in segments if not s["noise"] and s["words"]]


# --- LLM matching ---

PROMPT = """You verify song lyrics transcribed by Whisper against reference lyrics.

You get the reference lines (numbered, with start times when synced) and the Whisper segments in the
order they were sung (with their start/end times). Whisper mishears words, so match on meaning and
sound, not exact text. For every Whisper segment, list the ids of the reference lines it sings, in order:
- A segment can span several reference lines, fully or partly. Include every line it touches, even when
  Whisper only heard part of it.
- A line can also be split over consecutive segments: then set `continues` on the later segment, so its
  first line is treated as the rest of the previous segment's last line rather than a repeat.
- Repeated lines (choruses): when the reference lists each occurrence, use the occurrence that fits the
  order (and times, for synced lyrics). Otherwise reuse the same id.
- Return an empty list for segments that aren't in the reference: hallucinations, repeated lyrics over
  an instrumental part (for synced lyrics, times far from any plausible line; allow for a constant offset
  between the two timelines), and vocalizations (oh, la, na na) the reference doesn't have."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["segments"],
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["segment_id", "line_ids", "continues"],
                "properties": {
                    "segment_id": {"type": "integer"},
                    "line_ids": {"type": "array", "items": {"type": "integer"}},
                    "continues": {"type": "boolean"},
                },
            },
        },
    },
}


def match_lines(reference: list[dict], segments: list[dict]) -> dict:
    from openai import OpenAI

    ref = "\n".join(
        f"{i}. [{r['start']:.2f}] {r['text']}" if "start" in r else f"{i}. {r['text']}"
        for i, r in enumerate(reference)
    )
    whisper = "\n".join(f"{s['id']}. [{s['start']:.2f}-{s['end']:.2f}] {s['text']}" for s in segments)
    response = OpenAI().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": f"Reference lines:\n{ref}\n\nWhisper segments:\n{whisper}"},
        ],
        response_format={"type": "json_schema", "json_schema": {"name": "lyrics", "strict": True, "schema": SCHEMA}},
    )
    return json.loads(response.choices[0].message.content)


# --- alignment and timing ---


def normalize(word: str) -> str:
    return re.sub(r"[^\w]", "", word.lower())


# Rough syllable count: vowel groups, at least one.
def syllables(word: str) -> int:
    return max(1, len(re.findall(r"[aeiouy]+", word.lower())))


# Spreads words over [start, end] proportionally to their syllables.
def spread(words: list[str], start: float, end: float) -> list[dict]:
    weights = [syllables(w) for w in words]
    total = sum(weights) or 1
    out, t = [], start
    for word, weight in zip(words, weights):
        d = (end - start) * weight / total
        out.append({"word": word, "start": round(t, 3), "end": round(t + d, 3), "inferred": True})
        t += d
    return out


# Gives corrected words Whisper's timing where they line up; words Whisper missed are spread over
# the gap between their timed neighbours (or the segment bounds).
def align(corrected: list[str], heard: list[dict], seg_start: float, seg_end: float) -> list[dict]:
    matcher = difflib.SequenceMatcher(
        a=[normalize(w) for w in corrected], b=[normalize(w["word"]) for w in heard], autojunk=False
    )
    timed: list[dict | None] = [None] * len(corrected)
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op == "equal" or (op == "replace" and a1 - a0 == b1 - b0):
            for k in range(a1 - a0):
                timed[a0 + k] = {"word": corrected[a0 + k], "start": heard[b0 + k]["start"], "end": heard[b0 + k]["end"]}
        elif op == "replace" and b1 > b0:
            # Uneven replacement: spread the new words over the replaced words' span.
            span = spread(corrected[a0:a1], heard[b0]["start"], heard[b1 - 1]["end"])
            for k, w in enumerate(span):
                del w["inferred"]
                timed[a0 + k] = w
    out, i = [], 0
    while i < len(timed):
        if timed[i]:
            out.append(timed[i])
            i += 1
            continue
        j = i
        while j < len(timed) and not timed[j]:
            j += 1
        start = out[-1]["end"] if out else seg_start
        end = timed[j]["start"] if j < len(timed) else max(seg_end, start)
        out.extend(spread(corrected[i:j], start, max(end, start)))
        i = j
    return out


# Median gap between Whisper's and the LRC's line starts, if enough lines agree on it.
def estimate_offset(lines: list[dict], reference: list[dict]) -> float | None:
    diffs = [
        l["words"][0]["start"] - reference[l["line_id"]]["start"]
        for l in lines
        if "start" in reference[l["line_id"]] and not l["words"][0].get("inferred")
    ]
    if len(diffs) < MIN_MATCHES:
        return None
    offset = statistics.median(diffs)
    close = [d for d in diffs if abs(d - offset) <= MAX_DRIFT]
    if len(close) < MIN_MATCHES or statistics.pstdev(close) > MAX_SPREAD:
        return None
    return offset


# The window a reference line is sung in, on Whisper's timeline, shortened to a plausible length.
def lrc_window(ref: dict, offset: float, words: list[str]) -> tuple[float, float]:
    start = ref["start"] + offset
    end = min(ref["end"] + offset, start + SYLLABLE_SECONDS * sum(syllables(w) for w in words) + 0.3)
    return start, max(end, start + 0.3)


def verify(transcript: dict, meta: dict) -> dict | None:
    duration = transcript.get("duration") or 0
    hit = lookup(meta.get("artist", ""), meta["title"], duration)
    if not hit:
        return None
    base = {"source": "lrclib", "lrclibId": hit["id"]}
    if hit.get("instrumental"):
        return base | {"synced": False, "segments": []}

    synced = bool(hit.get("syncedLyrics"))
    reference = parse_synced(hit["syncedLyrics"], duration) if synced else parse_plain(hit.get("plainLyrics") or "")
    if not reference:
        return None
    segments = whisper_segments(transcript)
    matched = match_lines(reference, segments)
    by_id = {s["id"]: s for s in segments}

    # The reference is the text: Whisper's words only provide timings. Consecutive segments continuing
    # the same line are merged into one block, so the line's words are aligned once across both.
    blocks = []
    for m in matched["segments"]:
        seg = by_id.get(m["segment_id"])
        ids = [i for i in m["line_ids"] if 0 <= i < len(reference)]
        if not seg or not ids:
            continue
        if m["continues"] and blocks and blocks[-1]["line_ids"][-1] == ids[0]:
            prev = blocks[-1]
            prev["line_ids"] += ids[1:]
            prev["words"] += seg["words"]
            prev["end"] = seg["end"]
        else:
            blocks.append({"line_ids": ids, "words": list(seg["words"]), "start": seg["start"], "end": seg["end"]})

    # One output line per reference line in each block.
    lines = []
    for block in blocks:
        texts = [reference[i]["text"].split() for i in block["line_ids"]]
        words = align([w for t in texts for w in t], block["words"], block["start"], block["end"])
        k = 0
        for i, t in zip(block["line_ids"], texts):
            lines.append({"line_id": i, "words": words[k : k + len(t)], "end": block["end"]})
            k += len(t)

    offset = estimate_offset(lines, reference) if synced else None
    if offset is not None:
        for line in lines:
            ref = reference[line["line_id"]]
            start, end = lrc_window(ref, offset, [w["word"] for w in line["words"]])
            first, last = line["words"][0]["start"], line["words"][-1]["end"]
            # Off from the LRC, or squeezed: words Whisper missed at the end of its segment get no time.
            squeezed = last - first < MIN_WORD_SECONDS * len(line["words"])
            if abs(first - start) > MAX_DRIFT or last > ref["end"] + offset + MAX_DRIFT or squeezed:
                line["words"] = spread([w["word"] for w in line["words"]], start, end)
                line["end"] = end
        # Every synced line is sung (each occurrence has its own timestamp), so lines Whisper missed, or
        # the LLM didn't map, are added at their LRC time.
        heard = {line["line_id"] for line in lines}
        for i in range(len(reference)):
            if i in heard:
                continue
            words = reference[i]["text"].split()
            if words:
                start, end = lrc_window(reference[i], offset, words)
                lines.append({"line_id": i, "words": spread(words, start, end), "end": end})

    lines.sort(key=lambda l: l["words"][0]["start"])
    out = []
    for i, line in enumerate(lines):
        start = line["words"][0]["start"]
        # A line lasts until the next one starts, or its Whisper segment / last word ends.
        end = max(line["words"][-1]["end"], min(line["end"], lines[i + 1]["words"][0]["start"]) if i + 1 < len(lines) else line["end"])
        out.append({"start": start, "end": end, "words": line["words"]})
    # Inferred missing lines can land on top of heard ones; keep the heard one.
    kept = []
    for seg in out:
        overlaps = kept and seg["start"] < kept[-1]["end"] - 0.1
        if overlaps and all(w.get("inferred") for w in seg["words"]):
            continue
        if overlaps and all(w.get("inferred") for w in kept[-1]["words"]):
            kept.pop()
        kept.append(seg)
    return base | {"synced": offset is not None, "offset": offset, "segments": kept}


if __name__ == "__main__":
    import sys

    with open(sys.argv[1]) as f:
        transcript = json.load(f)
    with open(sys.argv[2]) as f:
        meta = json.load(f)
    print(json.dumps(verify(transcript, meta), ensure_ascii=False, indent=2))
