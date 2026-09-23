from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import edge_tts

EDGE_VOICE_PRIORITY = [
    "es-ES-XimenaMultilingualNeural",
    "es-ES-IsidoraMultilingualNeural",
    "es-ES-TristanMultilingualNeural",
    "es-ES-AlvaroNeural",
    "es-ES-ElviraNeural",
]

DEFAULT_AZURE_VOICE = "es-ES-TristanMultilingualNeural"
DEFAULT_GLOSSARY = Path("config/technical_terms.json")


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


def load_glossary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"english_terms": [], "aliases": {}, "edge_aliases": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "english_terms": sorted(
            [str(x) for x in data.get("english_terms", [])],
            key=len,
            reverse=True,
        ),
        "aliases": {str(k): str(v) for k, v in data.get("aliases", {}).items()},
        "edge_aliases": {
            str(k): str(v) for k, v in data.get("edge_aliases", {}).items()
        },
    }


def render_mixed_language_text(
    text: str,
    *,
    english_terms: list[str],
    aliases: dict[str, str],
    english_locale: str,
) -> str:
    tokens: list[tuple[str, str]] = []
    for source, alias in aliases.items():
        tokens.append((source, "alias"))
    for term in english_terms:
        tokens.append((term, "english"))

    tokens.sort(key=lambda item: len(item[0]), reverse=True)
    if not tokens:
        return html.escape(text)

    pattern = re.compile(
        "|".join(
            rf"(?<!\w){re.escape(token)}(?!\w)"
            for token, _ in tokens
        ),
        flags=re.IGNORECASE,
    )

    token_map = {token.casefold(): kind for token, kind in tokens}
    alias_map = {source.casefold(): alias for source, alias in aliases.items()}

    out: list[str] = []
    last = 0
    for match in pattern.finditer(text):
        out.append(html.escape(text[last:match.start()]))
        raw = match.group(0)
        kind = token_map.get(raw.casefold(), "english")
        if kind == "alias":
            alias = html.escape(alias_map[raw.casefold()], quote=True)
            out.append(f'<sub alias="{alias}">{html.escape(raw)}</sub>')
        else:
            out.append(
                f'<lang xml:lang="{english_locale}">{html.escape(raw)}</lang>'
            )
        last = match.end()

    out.append(html.escape(text[last:]))
    return "".join(out)


def text_to_ssml(
    text: str,
    *,
    voice: str,
    locale: str,
    english_locale: str,
    rate: str,
    paragraph_pause_ms: int,
    glossary: dict[str, Any],
) -> str:
    paragraphs = [
        p.strip()
        for p in re.split(r"\n\s*\n+", text.strip())
        if p.strip()
    ]

    rendered_paragraphs: list[str] = []
    for paragraph in paragraphs:
        sentences = [
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+", paragraph)
            if s.strip()
        ]
        sentence_xml = "".join(
            "<s>"
            + render_mixed_language_text(
                sentence,
                english_terms=glossary["english_terms"],
                aliases=glossary["aliases"],
                english_locale=english_locale,
            )
            + "</s>"
            for sentence in sentences
        )
        rendered_paragraphs.append(f"<p>{sentence_xml}</p>")

    pause = f'<break time="{paragraph_pause_ms}ms"/>'
    body = pause.join(rendered_paragraphs)

    return (
        '<speak version="1.0" '
        'xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="https://www.w3.org/2001/mstts" '
        f'xml:lang="{locale}">'
        f'<voice name="{html.escape(voice, quote=True)}">'
        f'<prosody rate="{html.escape(rate, quote=True)}">'
        f"{body}"
        "</prosody>"
        "</voice>"
        "</speak>"
    )


def synthesize_azure(
    ssml: str,
    output: Path,
    *,
    speech_key: str,
    speech_region: str,
    output_format: str,
) -> None:
    url = (
        f"https://{speech_region}.tts.speech.microsoft.com/"
        "cognitiveservices/v1"
    )
    request = urllib.request.Request(
        url,
        data=ssml.encode("utf-8"),
        method="POST",
        headers={
            "Ocp-Apim-Subscription-Key": speech_key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": output_format,
            "User-Agent": "archia-audio",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            output.write_bytes(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Azure Speech failed with HTTP {exc.code}: {detail}"
        ) from exc



def apply_edge_aliases(text: str, aliases: dict[str, str]) -> str:
    result = text
    for source in sorted(aliases, key=len, reverse=True):
        replacement = aliases[source]
        pattern = re.compile(
            rf"(?<!\w){re.escape(source)}(?!\w)",
            flags=re.IGNORECASE,
        )
        result = pattern.sub(replacement, result)
    return result


async def choose_edge_voice(locale: str, preferred: str | None = None) -> str:
    voices = await edge_tts.list_voices()
    available = {v["ShortName"] for v in voices if v.get("Locale") == locale}
    if preferred and preferred in available:
        return preferred
    for candidate in EDGE_VOICE_PRIORITY:
        if candidate in available:
            return candidate
    if not available:
        raise RuntimeError(f"No edge-tts voice available for locale {locale}")
    return sorted(available)[0]


async def synthesize_edge(
    text: str,
    output: Path,
    *,
    voice: str,
    rate: str,
) -> None:
    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
    await communicate.save(str(output))


def postprocess_audio(source: Path, destination: Path) -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        shutil.copyfile(source, destination)
        return "copy_no_ffmpeg"

    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-af",
        "loudnorm=I=-16:LRA=7:TP=-1.5",
        "-ar",
        "48000",
        "-ac",
        "1",
        "-b:a",
        "192k",
        str(destination),
    ]
    subprocess.run(command, check=True)
    return "ffmpeg_loudnorm_48khz_192kbps"



def create_complete_audio(chapter_files: list[Path], destination: Path) -> str:
    if not chapter_files:
        raise ValueError("No chapter audio files to concatenate")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to create the complete block audio")

    concat_file = destination.parent / ".concat.txt"
    concat_file.write_text(
        "".join(
            f"file '{p.resolve().as_posix()}'\n"
            for p in chapter_files
        ),
        encoding="utf-8",
    )

    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "1",
        str(destination),
    ]
    subprocess.run(command, check=True)
    concat_file.unlink(missing_ok=True)
    return "ffmpeg_concat_48khz_192kbps"


def select_provider(payload: dict[str, Any]) -> tuple[str, bool]:
    requested = str(payload.get("provider", "auto")).lower()
    has_azure = bool(
        os.getenv("AZURE_SPEECH_KEY") and os.getenv("AZURE_SPEECH_REGION")
    )
    if requested == "azure":
        return ("azure" if has_azure else "edge"), not has_azure
    if requested == "edge":
        return "edge", False
    if requested != "auto":
        raise ValueError("provider must be one of: auto, azure, edge")
    return ("azure" if has_azure else "edge"), not has_azure


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload = load_request(args.request)
    glossary = load_glossary(
        Path(payload.get("glossary", str(DEFAULT_GLOSSARY)))
    )

    locale = payload.get("locale", "es-ES")
    english_locale = payload.get("english_locale", "en-GB")
    rate = payload.get("rate", "+4%")
    block = payload["block"].strip()
    paragraph_pause_ms = int(payload.get("paragraph_pause_ms", 180))
    requested_provider = str(payload.get("provider", "auto")).lower()
    actual_provider, fallback_used = select_provider(payload)

    azure_voice = payload.get("azure_voice", DEFAULT_AZURE_VOICE)
    preferred_edge_voice = payload.get("edge_voice")
    edge_voice = "DRY_RUN"
    if not args.dry_run and actual_provider == "edge":
        edge_voice = await choose_edge_voice(locale, preferred_edge_voice)

    out_dir = Path("dist") / block
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "block": block,
        "locale": locale,
        "english_locale": english_locale,
        "rate": rate,
        "paragraph_pause_ms": paragraph_pause_ms,
        "requested_provider": requested_provider,
        "provider": actual_provider if not args.dry_run else "DRY_RUN",
        "fallback_used": fallback_used,
        "azure_voice": azure_voice,
        "preferred_edge_voice": preferred_edge_voice,
        "edge_voice": edge_voice,
        "chapters": [],
    }

    azure_key = os.getenv("AZURE_SPEECH_KEY", "")
    azure_region = os.getenv("AZURE_SPEECH_REGION", "")
    azure_output_format = payload.get(
        "azure_output_format",
        "audio-48khz-192kbitrate-mono-mp3",
    )

    chapter_audio_files: list[Path] = []

    for index, chapter in enumerate(payload["chapters"], start=1):
        title = chapter["title"].strip()
        text = chapter["text"].strip()
        base = f"{index:02d}-{slugify(title)}"
        filename = f"{base}.mp3"
        txt_name = f"{base}.txt"
        ssml_name = f"{base}.ssml"
        raw_name = f".raw-{filename}"

        (out_dir / txt_name).write_text(text + "\n", encoding="utf-8")

        ssml = text_to_ssml(
            text,
            voice=azure_voice,
            locale=locale,
            english_locale=english_locale,
            rate=rate,
            paragraph_pause_ms=paragraph_pause_ms,
            glossary=glossary,
        )
        (out_dir / ssml_name).write_text(ssml + "\n", encoding="utf-8")

        postprocess = None
        if not args.dry_run:
            raw_path = out_dir / raw_name
            final_path = out_dir / filename

            if actual_provider == "azure":
                await asyncio.to_thread(
                    synthesize_azure,
                    ssml,
                    raw_path,
                    speech_key=azure_key,
                    speech_region=azure_region,
                    output_format=azure_output_format,
                )
            else:
                edge_text = apply_edge_aliases(
                    text,
                    glossary.get("edge_aliases", {}),
                )
                await synthesize_edge(
                    edge_text,
                    raw_path,
                    voice=edge_voice,
                    rate=rate,
                )

            postprocess = postprocess_audio(raw_path, final_path)
            raw_path.unlink(missing_ok=True)
            chapter_audio_files.append(final_path)

        manifest["chapters"].append(
            {
                "index": index,
                "title": title,
                "audio": None if args.dry_run else filename,
                "script": txt_name,
                "ssml": ssml_name,
                "postprocess": postprocess,
            }
        )

    complete_audio = None
    complete_method = None
    if not args.dry_run:
        complete_path = out_dir / f"{block}-completo.mp3"
        complete_method = create_complete_audio(chapter_audio_files, complete_path)
        complete_audio = complete_path.name

    manifest["complete_audio"] = complete_audio
    manifest["complete_method"] = complete_method

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
