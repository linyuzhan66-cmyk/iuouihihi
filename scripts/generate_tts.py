#!/usr/bin/env python3
"""Generate a Traditional Chinese investment-research narration with ElevenLabs."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
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
VOICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

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
        action="append",
        dest="voice_ids",
        help="ElevenLabs voice ID. Repeat for multiple voices.",
    )
    parser.add_argument(
        "--text",
        help="Narration text. Defaults to the built-in Traditional Chinese sample.",
    )
    parser.add_argument(
        "--issue-event-file",
        type=Path,
        help="GitHub Issues event JSON file containing text and voice_ids fields.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="MP3 destination (default: %(default)s)",
    )
    return parser.parse_args()


def clean_block(value: str) -> str:
    """Normalize an inline value or a YAML-style multiline value."""
    lines = value.splitlines()
    if lines and lines[0].strip() in {"|", ">"}:
        lines = lines[1:]
    return textwrap.dedent("
".join(lines)).strip()


def parse_colon_fields(body: str) -> dict[str, str]:
    matches = list(re.finditer(r"(?im)^\s*(text|voice_ids)\s*:\s*(.*)$", body))
    fields: dict[str, str] = {}
    for index, match in enumerate(matches):
        field_name = match.group(1).lower()
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        fields[field_name] = clean_block(match.group(2) + body[match.end() : next_start])
    return fields


def parse_heading_fields(body: str) -> dict[str, str]:
    matches = list(
        re.finditer(r"(?im)^#{2,6}\s*(text|voice[ _-]?ids)\s*$", body)
    )
    fields: dict[str, str] = {}
    for index, match in enumerate(matches):
        field_name = re.sub(r"[ _-]", "_", match.group(1).lower())
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        fields[field_name] = clean_block(body[match.end() : next_start])
    return fields


def parse_voice_ids(value: str) -> list[str]:
    voice_ids: list[str] = []
    for line in value.splitlines():
        item = line.strip()
        if item.startswith("-"):
            item = item[1:].strip()
        for candidate in item.split(","):
            voice_id = candidate.strip()
            if not voice_id:
                continue
            if not VOICE_ID_PATTERN.fullmatch(voice_id):
                raise TtsRequestError("The Issue contains an invalid voice ID.")
            if voice_id not in voice_ids:
                voice_ids.append(voice_id)
    return voice_ids


def parse_issue_request(body: str) -> tuple[str, list[str]]:
    fields = parse_colon_fields(body)
    if "text" not in fields or "voice_ids" not in fields:
        fields = parse_heading_fields(body)

    text = fields.get("text", "").strip()
    voice_ids = parse_voice_ids(fields.get("voice_ids", ""))
    if not text or not voice_ids:
        raise TtsRequestError(
            "Issue body must include non-empty text and voice_ids fields."
        )
    return text, voice_ids


def load_issue_request(event_path: Path) -> tuple[str, list[str]]:
    try:
        event = json.loads(event_path.read_text(encoding="utf-8"))
        body = event.get("issue", {}).get("body", "")
    except (OSError, json.JSONDecodeError, AttributeError):
        raise TtsRequestError("GitHub Issue event data could not be read.") from None

    if not isinstance(body, str):
        raise TtsRequestError("GitHub Issue body is missing.")
    return parse_issue_request(body)


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


def request_audio(api_key: str, voice_id: str, text: str) -> bytes:
    payload = json.dumps(
        {
            "text": text,
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
        if args.issue_event_file:
            text, voice_ids = load_issue_request(args.issue_event_file)
            output_paths = [
                args.output.parent / f"tts_{voice_id}.mp3" for voice_id in voice_ids
            ]
        else:
            text = args.text or NARRATION
            voice_ids = args.voice_ids or [DEFAULT_VOICE_ID]
            output_paths = [
                args.output
                if len(voice_ids) == 1
                else args.output.with_name(
                    f"{args.output.stem}_{voice_id}{args.output.suffix}"
                )
                for voice_id in voice_ids
            ]

        api_key = get_api_key()
        for voice_id, output_path in zip(voice_ids, output_paths, strict=True):
            audio = request_audio(api_key, voice_id, text)
            write_audio(output_path, audio)
            print(f"Generated MP3: {output_path} ({len(audio)} bytes)")
    except TtsRequestError as error:
        print(f"TTS generation failed: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
    
