import asyncio
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SILENCE_THRESHOLD_DB = -45.0

HALLUCINATION_PHRASES = [
    "ご視聴ありがとうございました",
    "チャンネル登録",
    "高評価",
    "お願いします",
    "ありがとうございました",
    "次の動画",
    "最後までご覧いただき",
    "ご覧いただきありがとう",
    "チャンネル登録よろしく",
    "いいねボタン",
    "家族と一緒に",
    "Amara.org",
    "字幕は",
]

REPETITION_THRESHOLD = 0.5


@dataclass
class ChunkAnalysis:
    index: int
    mean_volume: float
    max_volume: float
    is_silent: bool


@dataclass
class HallucinationResult:
    is_hallucinated: bool
    reason: str = ""


async def analyze_chunk_audio(chunk_path: Path, index: int) -> ChunkAnalysis:
    """FFmpegのvolumedetectフィルタでチャンクの音量を解析する。"""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", str(chunk_path),
        "-af", "volumedetect",
        "-f", "null", "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    output = stderr.decode()

    mean_volume = _parse_volume(output, "mean_volume")
    max_volume = _parse_volume(output, "max_volume")

    is_silent = mean_volume < SILENCE_THRESHOLD_DB

    if is_silent:
        logger.info(f"Chunk {index} is silent (mean={mean_volume:.1f}dB, max={max_volume:.1f}dB)")

    return ChunkAnalysis(
        index=index,
        mean_volume=mean_volume,
        max_volume=max_volume,
        is_silent=is_silent,
    )


async def analyze_chunks(chunks: list[Path]) -> list[ChunkAnalysis]:
    """全チャンクの音声レベルを並列解析する。"""
    tasks = [analyze_chunk_audio(chunk, i) for i, chunk in enumerate(chunks)]
    return await asyncio.gather(*tasks)


def check_hallucination(result: dict, chunk_duration: float = 600.0) -> HallucinationResult:
    """Whisper出力のハルシネーションを検査する。"""
    if result.get("skipped") or result.get("error"):
        return HallucinationResult(is_hallucinated=False)

    segments = result.get("segments", [])
    text = result.get("text", "")

    if not segments and not text:
        return HallucinationResult(is_hallucinated=False)

    # 既知のハルシネーションフレーズ検出
    for phrase in HALLUCINATION_PHRASES:
        if phrase in text:
            return HallucinationResult(
                is_hallucinated=True,
                reason=f"ハルシネーションフレーズを検出: 「{phrase}」",
            )

    # 繰り返しパターン検出
    if len(segments) >= 3:
        texts = [seg.get("text", "").strip() for seg in segments if seg.get("text", "").strip()]
        if texts:
            counter = Counter(texts)
            most_common_text, most_common_count = counter.most_common(1)[0]
            if most_common_count / len(texts) >= REPETITION_THRESHOLD:
                return HallucinationResult(
                    is_hallucinated=True,
                    reason=f"繰り返しパターンを検出: 「{most_common_text}」が{most_common_count}/{len(texts)}セグメントで出現",
                )

    # テキスト密度チェック（音声長に対して極端にテキストが少ない）
    if segments:
        total_audio_len = max(seg.get("end", 0) for seg in segments) - min(seg.get("start", 0) for seg in segments)
        if total_audio_len > 60 and len(text) < total_audio_len * 0.3:
            return HallucinationResult(
                is_hallucinated=True,
                reason=f"テキスト密度が極端に低い ({len(text)}文字 / {total_audio_len:.0f}秒)",
            )

    return HallucinationResult(is_hallucinated=False)


def assess_overall_quality(
    analyses: list[ChunkAnalysis],
    hallucination_results: list[HallucinationResult],
) -> tuple[set[int], bool]:
    """全チャンクの評価を統合し、無効なチャンクのインデックスと全滅フラグを返す。"""
    invalid_indices: set[int] = set()

    for analysis in analyses:
        if analysis.is_silent:
            invalid_indices.add(analysis.index)

    for i, hr in enumerate(hallucination_results):
        if hr.is_hallucinated:
            invalid_indices.add(i)

    all_invalid = len(invalid_indices) == len(analyses)
    return invalid_indices, all_invalid


def _parse_volume(output: str, key: str) -> float:
    """FFmpegのvolumedetect出力から音量値をパースする。"""
    pattern = rf"{key}:\s*(-?[\d.]+)\s*dB"
    match = re.search(pattern, output)
    if match:
        return float(match.group(1))
    return -91.0  # FFmpegのvolumedetectの最小値
