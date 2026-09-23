from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

import edge_tts

VOICE_PRIORITY = [
    "es-ES-TristanMultilingualNeural",
    "es-ES-XimenaMultilingualNeural",
    "es-ES-AlvaroNeural",
    "es-ES-ElviraNeural",
]


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "audio"


def load_request(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload.get("block"), str) or not payload["block"].strip():
        raise ValueError("request.block is required")
    chapters = payload.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise ValueError("request.chapters must be a non-empty list")
    for idx, chapter in enumerate(chapters, start=1):
        if not isinstance(chapter.get("title"), str) or not chapter["title"].strip():
            raise ValueError(f"chapter {idx}: title is required")
        if not isinstance(chapter.get("text"), str) or not chapter["text"].strip():
            raise ValueError(f"chapter {idx}: text is required")
    return payload


async def choose_voice(locale: str) -> str:
    voices = await edge_tts.list_voices()
    available = {v["ShortName"] for v in voices if v.get("Locale") == locale}
    for candidate in VOICE_PRIORITY:
        if candidate in available:
            return candidate
    if not available:
        raise RuntimeError(f"No edge-tts voice available for locale {locale}")
    return sorted(available)[0]


async def synthesize(text: str, output: Path, voice: str, rate: str) -> None:
    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
    await communicate.save(str(output))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload = load_request(args.request)
    locale = payload.get("locale", "es-ES")
    rate = payload.get("rate", "+10%")
    block = payload["block"].strip()

    out_dir = Path("dist") / block
    out_dir.mkdir(parents=True, exist_ok=True)

    voice = "DRY_RUN"
    if not args.dry_run:
        voice = await choose_voice(locale)

    manifest = {
        "block": block,
        "locale": locale,
        "rate": rate,
        "voice": voice,
        "chapters": [],
    }

    for index, chapter in enumerate(payload["chapters"], start=1):
        title = chapter["title"].strip()
        text = chapter["text"].strip()
        filename = f"{index:02d}-{slugify(title)}.mp3"
        txt_name = f"{index:02d}-{slugify(title)}.txt"

        (out_dir / txt_name).write_text(text + "\n", encoding="utf-8")

        if not args.dry_run:
            await synthesize(text, out_dir / filename, voice, rate)

        manifest["chapters"].append(
            {
                "index": index,
                "title": title,
                "audio": None if args.dry_run else filename,
                "script": txt_name,
            }
        )

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
