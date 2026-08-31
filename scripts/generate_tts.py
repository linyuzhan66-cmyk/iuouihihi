#!/usr/bin/env python3
"""Generate a Traditional Chinese investment-research narration with ElevenLabs."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"
DEFAULT_OUTPUT = Path("output/investment_research_zh-TW.mp3")
API_URL_TEMPLATE = (
    "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"
)
MAX_ATTEMPTS = 3

NARRATION = (
    "本週投資研究聚焦於市場成長與評價之間的平衡。"
    "觀察企業營收、毛利率與自由現金流的變化，能協助判斷成長是否具備持續性。"
    "同時，利率走勢、匯率與產業競爭也可能影響估值。"
    "研究時，可比較多個情境，留意假設與風險，而非只看單一指標。"
    "以上內容僅供教育與研究參考，不構成任何投資建議；"
    "投資前請依自身目標、風險承受度與專業意見審慎評估。"
)


class TtsRequestError(RuntimeError):
    """A safe-to-display description of a failed TTS request."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--voice-id",
        default=DEFAULT_VOICE_ID,
        help="ElevenLabs voice ID (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="MP3 destination (default: %(default)s)",
    )
    return parser.parse_args()


def get_api_key() -> str:
    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise TtsRequestError("ELEVENLABS_API_KEY is not configured.")
    return api_key


def is_mp3(audio: bytes) -> bool:
    """Accept an ID3-tagged MP3 or a raw MPEG audio frame."""
    return audio.startswith(b"ID3") or (
        len(audio) >= 2 and audio[0] == 0xFF and (audio[1] & 0xE0) == 0xE0
    )


def request_audio(api_key: str, voice_id: str) -> bytes:
    payload = json.dumps(
        {
            "text": NARRATION,
            "model_id": "eleven_multilingual_v2",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        API_URL_TEMPLATE.format(voice_id=quote(voice_id, safe="")),
        data=payload,
        headers={
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": api_key,
        },
        method="POST",
    )

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urlopen(request, timeout=90) as response:
                audio = response.read()
            if not audio or not is_mp3(audio):
                raise TtsRequestError("ElevenLabs returned an invalid MP3 response.")
            return audio
        except HTTPError as error:
            retryable = error.code == 429 or 500 <= error.code <= 599
            if not retryable or attempt == MAX_ATTEMPTS:
                raise TtsRequestError(
                    f"ElevenLabs request failed with HTTP status {error.code}."
                ) from None
        except (URLError, TimeoutError):
            if attempt == MAX_ATTEMPTS:
                raise TtsRequestError("ElevenLabs request failed because of a network error.") from None

        time.sleep(2 ** (attempt - 1))

    raise AssertionError("unreachable")


def write_audio(output_path: Path, audio: bytes) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    try:
        temporary_path.write_bytes(audio)
        temporary_path.replace(output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main() -> int:
    args = parse_args()
    try:
        audio = request_audio(get_api_key(), args.voice_id)
        write_audio(args.output, audio)
    except TtsRequestError as error:
        print(f"TTS generation failed: {error}", file=sys.stderr)
        return 1

    print(f"Generated MP3: {args.output} ({len(audio)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
  
