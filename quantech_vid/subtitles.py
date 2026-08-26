from __future__ import annotations

import re
from pathlib import Path


def _timestamp(seconds: float, vtt: bool = False) -> str:
    milliseconds = int(round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    separator = "." if vtt else ","
    return f"{hours:02}:{minutes:02}:{secs:02}{separator}{milliseconds:03}"


def cues(text: str, duration: float) -> list[tuple[float, float, str]]:
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", text.strip()) if item.strip()]
    if not sentences:
        return []
    weights = [max(1, len(sentence.split())) for sentence in sentences]
    total = sum(weights)
    output: list[tuple[float, float, str]] = []
    cursor = 0.0
    for index, (sentence, weight) in enumerate(zip(sentences, weights)):
        end = duration if index == len(sentences) - 1 else cursor + duration * weight / total
        output.append((cursor, end, sentence))
        cursor = end
    return output


def write_subtitles(text: str, duration: float, srt_path: Path, vtt_path: Path) -> None:
    items = cues(text, duration)
    srt_lines: list[str] = []
    vtt_lines = ["WEBVTT", ""]
    for index, (start, end, sentence) in enumerate(items, 1):
        srt_lines.extend([str(index), f"{_timestamp(start)} --> {_timestamp(end)}", sentence, ""])
        vtt_lines.extend([f"{_timestamp(start, True)} --> {_timestamp(end, True)}", sentence, ""])
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
    vtt_path.write_text("\n".join(vtt_lines), encoding="utf-8")
