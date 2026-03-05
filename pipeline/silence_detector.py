import asyncio
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# 無音判定: 平均音量と最大音量の両方が閾値以下の場合のみ無音と判定
# ハンズオン等では静かな区間が長くても短い発話があれば有効
SILENCE_MEAN_THRESHOLD_DB = -55.0
SILENCE_MAX_THRESHOLD_DB = -35.0

# Whisperが無音区間で生成しがちな定型フレーズ（YouTube字幕系）
# 通常の会話で頻出するフレーズ（「お願いします」等）は含めない
HALLUCINATION_PHRASES = [
    "ご視聴ありがとうございました",
    "チャンネル登録よろしくお願いします",
    "高評価よろしくお願いします",
    "最後までご覧いただきありがとうございました",
    "チャンネル登録といいねボタン",
    "家族と一緒に家に帰ることを願っています",
    "Amara.org",
    "字幕は字幕設定から",
    "MoizMedia",
    "Thanks for watching",
    "Please subscribe",
]

REPETITION_THRESHOLD = 0.6


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
        "ffmpeg",
        "-i",
        str(chunk_path),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    output = stderr.decode()

    mean_volume = _parse_volume(output, "mean_volume")
    max_volume = _parse_volume(output, "max_volume")

    # 平均音量と最大音量の両方が閾値以下の場合のみ無音と判定
    # max_volumeが高ければ、静かな区間が長くても発話が含まれている
    is_silent = mean_volume < SILENCE_MEAN_THRESHOLD_DB and max_volume < SILENCE_MAX_THRESHOLD_DB

    logger.info(
        f"Chunk {index} audio: mean={mean_volume:.1f}dB, max={max_volume:.1f}dB, silent={is_silent}"
    )

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
                    reason=(
                        f"繰り返しパターンを検出: 「{most_common_text}」が"
                        f"{most_common_count}/{len(texts)}セグメントで出現"
                    ),
                )

    # テキスト密度チェック（10分チャンクで極端にテキストが少ない場合）
    # ハンズオンでは無言の作業時間が多いため、閾値は非常に低く設定
    if segments:
        seg_ends = [seg.get("end", 0) for seg in segments]
        seg_starts = [seg.get("start", 0) for seg in segments]
        total_audio_len = max(seg_ends) - min(seg_starts)
        if total_audio_len > 120 and len(text) < 10:
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
