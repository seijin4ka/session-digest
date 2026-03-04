import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Segment:
    start: float
    end: float
    text: str


def merge_transcripts(
    results: list[dict],
    chunk_duration: int = 600,
    overlap: int = 30,
) -> list[Segment]:
    all_segments: list[Segment] = []

    for chunk_index, result in enumerate(results):
        if result.get("error"):
            offset = chunk_index * chunk_duration
            all_segments.append(Segment(
                start=offset,
                end=offset + chunk_duration,
                text=result["error"],
            ))
            continue

        offset = chunk_index * chunk_duration
        segments = result.get("segments", [])

        for seg in segments:
            seg_start = seg.get("start", 0) + offset
            seg_end = seg.get("end", 0) + offset
            text = seg.get("text", "").strip()

            if not text:
                continue

            # Skip overlap region from previous chunk
            if chunk_index > 0 and seg.get("start", 0) < overlap:
                continue

            all_segments.append(Segment(start=seg_start, end=seg_end, text=text))

    all_segments.sort(key=lambda s: s.start)
    return all_segments


def format_transcript(segments: list[Segment]) -> str:
    lines: list[str] = []
    for seg in segments:
        timestamp = _format_time(seg.start)
        lines.append(f"[{timestamp}] {seg.text}")
    return "\n\n".join(lines)


def _format_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
