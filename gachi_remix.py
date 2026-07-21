"""
Gachi Remix — автоматический gachi-ремикс любой песни.
Основа: faster-whisper + LLM (локальный/AI) + FFmpeg.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("gachi_remix")

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class Placement:
    start: float
    sound: str

@dataclass
class Word:
    text: str
    start: float
    end: float

@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)

# ---------------------------------------------------------------------------
# Sound library
# ---------------------------------------------------------------------------

class SoundLibrary:
    def __init__(self, sounds_dir: str | Path):
        self.dir = Path(sounds_dir)
        self.by_keyword: dict[str, list[str]] = {}
        self.all_sounds: list[str] = []
        self._load()

    def _load(self) -> None:
        if not self.dir.exists():
            log.warning("Sound directory not found: %s", self.dir)
            return
        for f in sorted(self.dir.iterdir()):
            if f.suffix.lower() not in (".mp3", ".wav", ".ogg", ".flac", ".m4a"):
                continue
            self.all_sounds.append(str(f))
            kws = self._extract_keywords(f)
            for kw in kws:
                self.by_keyword.setdefault(kw, []).append(str(f))

    @staticmethod
    def _extract_keywords(path: Path) -> list[str]:
        name = path.stem.lower()
        name = re.sub(r"voicy\d*", "", name)
        name = re.sub(r"\d+", "", name)
        name = re.sub(r"[_\-\s]+", " ", name).strip()
        return [w for w in name.split() if len(w) > 1]

    def random(self) -> str:
        return random.choice(self.all_sounds) if self.all_sounds else ""

    def match_by_rules(self, word_text: str, used: dict[str, int]) -> str | None:
        wt = word_text.lower().strip(".,!?;:'\"")
        if not wt:
            return None
        candidates: list[str] = []
        for kw, files in self.by_keyword.items():
            if kw in wt or wt in kw:
                candidates.extend(files)
        if not candidates:
            return None
        fresh = [f for f in candidates if used.get(f, 0) < 2]
        return random.choice(fresh) if fresh else random.choice(candidates)

    def lookup(self, name: str) -> str | None:
        stem = Path(name).stem.lower()
        for p in self.all_sounds:
            if Path(p).stem.lower() == stem:
                return p
        return None

# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def get_duration(path: str | Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0

def check_ffmpeg() -> None:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
    except FileNotFoundError:
        log.error("FFmpeg not found. Install: https://ffmpeg.org/download.html")
        sys.exit(1)

# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

_whisper_model_cache: dict[str, Any] = {}

def transcribe(audio_path: str | Path, model_name: str, language: str | None,
               device: str) -> list[Segment]:
    from faster_whisper import WhisperModel

    if model_name not in _whisper_model_cache:
        log.info("Loading whisper model '%s' on %s...", model_name, device)
        _whisper_model_cache[model_name] = WhisperModel(
            model_name, device=device, compute_type="float32",
        )
    model = _whisper_model_cache[model_name]

    log.info("Transcribing...")
    t0 = time.time()
    segments_gen, info = model.transcribe(
        str(audio_path), language=language or None, word_timestamps=True,
    )
    segments: list[Segment] = []
    for seg in segments_gen:
        words = []
        for w in seg.words or []:
            words.append(Word(text=w.word.strip(), start=w.start, end=w.end))
        segments.append(Segment(
            start=seg.start, end=seg.end,
            text=(seg.text or "").strip(), words=words,
        ))
    log.info("  Lang: %s (%.0f%%) | %d seg | %.1fs",
             info.language, info.language_probability * 100,
             len(segments), time.time() - t0)
    return segments

# ---------------------------------------------------------------------------
# LLM matcher (generic OpenAI-compatible API)
# ---------------------------------------------------------------------------

LLM_SYSTEM = """You are a music remix engineer. Given song lyrics with timestamps,
suggest where to insert gachi sound effects for maximum comedic/rhythmic effect.

Available sounds (keyword -> filename):
{sound_list}

Rules:
- Match sounds to contextual words or nearby words in the lyrics
- Place the sound RIGHT BEFORE the relevant word (not on it)
- 2-5 inserts per minute for natural feel
- Prioritize: cum/fuck/yeah/ah/oh/slap sounds at lewd/excited moments
- Be creative — think like a DJ making a mashup

Return ONLY a JSON array of {{"start": <seconds>, "sound": "<filename>"}}.
No markdown, no explanation."""

def _build_llm_prompt(segments: list[Segment], library: SoundLibrary) -> str:
    sound_lines = sorted(set(
        f"{kw:12s} -> {Path(v).stem}"
        for kw, vals in library.by_keyword.items()
        for v in vals[:2]
    ))
    sound_list = "\n".join(sound_lines[:60])

    lyrics_lines = []
    for seg in segments:
        for w in seg.words:
            lyrics_lines.append(f"[{w.start:7.2f}] {w.text}")
    if not lyrics_lines:
        lyrics_lines = [f"[{seg.start:.2f}] {seg.text}" for seg in segments]
    lyrics = "\n".join(lyrics_lines)

    prompt = LLM_SYSTEM.format(sound_list=sound_list)
    prompt += f"\n\nLyrics:\n{lyrics}"
    return prompt

def _parse_llm_response(text: str, library: SoundLibrary) -> list[Placement]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    placements = []
    for item in data:
        spath = library.lookup(item.get("sound", ""))
        if spath:
            placements.append(Placement(
                start=float(item["start"]), sound=spath,
            ))
    return placements


def llm_match_openai(segments: list[Segment], library: SoundLibrary,
                     api_url: str, model: str, api_key: str = "ollama") -> list[Placement]:
    from openai import OpenAI

    client = OpenAI(base_url=api_url, api_key=api_key)
    prompt = _build_llm_prompt(segments, library)

    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a precise JSON generator."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=4096,
            )
            text = resp.choices[0].message.content or ""
            return _parse_llm_response(text, library)
        except json.JSONDecodeError:
            log.warning("  LLM: malformed JSON (attempt %d)", attempt + 1)
        except Exception as exc:
            log.warning("  LLM: %s (attempt %d)", exc, attempt + 1)
        time.sleep(1)
    return []


def llm_match_gemini(segments: list[Segment], library: SoundLibrary,
                     api_key: str) -> list[Placement]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = _build_llm_prompt(segments, library)

    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7, max_output_tokens=4096,
                ),
            )
            return _parse_llm_response(resp.text, library)
        except json.JSONDecodeError:
            log.warning("  Gemini: malformed JSON (attempt %d)", attempt + 1)
        except Exception as exc:
            log.warning("  Gemini: %s (attempt %d)", exc, attempt + 1)
        time.sleep(1)
    return []

# ---------------------------------------------------------------------------
# Rules matcher
# ---------------------------------------------------------------------------

def find_pauses(segments: list[Segment], min_gap: float = 0.5) -> list[dict]:
    words = [w for seg in segments for w in seg.words]
    pauses = []
    for i in range(len(words) - 1):
        gap = words[i + 1].start - words[i].end
        if gap >= min_gap:
            pauses.append({"start": words[i].end, "end": words[i + 1].start})
    return pauses

# ---------------------------------------------------------------------------
# FFmpeg mixer
# ---------------------------------------------------------------------------

def mix(original_path: str | Path, placements: list[Placement],
        output_path: str | Path, volume_pct: float) -> None:
    dur = get_duration(original_path)
    dur_ms = int(dur * 1000) if dur > 0 else 999_999_999

    delay_inputs: list[str] = []
    filters: list[str] = []
    valid: list[Placement] = []
    vol = max(0.01, volume_pct / 100)

    for i, p in enumerate(placements):
        spath = p.sound
        if not os.path.isfile(spath):
            log.warning("  Missing sound: %s", spath)
            continue
        delay_ms = int(p.start * 1000)
        if delay_ms > dur_ms or delay_ms < 0:
            continue
        valid.append(p)
        label = f"s{i}"
        delay_inputs.extend(["-i", spath])
        filters.append(f"[{i+1}:a]volume={vol}[a{i}];")
        filters.append(f"[a{i}]adelay={delay_ms}|{delay_ms}[{label}];")

    if not valid:
        log.warning("No valid sounds — copying original")
        shutil.copy2(str(original_path), str(output_path))
        return

    mix_inputs = "[0:a]" + "".join(f"[s{j}]" for j in range(len(valid)))
    filters.append(f"{mix_inputs}amix=inputs={len(valid)+1}:duration=first")

    log.info("Mixing %d sounds via FFmpeg...", len(valid))
    cmd = [
        "ffmpeg", "-y",
        "-i", str(original_path),
        *delay_inputs,
        "-filter_complex", "".join(filters),
        "-ac", "2", "-b:a", "192k",
        str(output_path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            for line in r.stderr.split("\n"):
                if any(w in line.lower() for w in ["error", "invalid", "cannot"]):
                    log.error("  FFmpeg: %s", line.strip()[:120])
            log.warning("  FFmpeg failed — copying original")
            shutil.copy2(str(original_path), str(output_path))
        else:
            log.info("Saved: %s", output_path)
    except subprocess.TimeoutExpired:
        log.error("FFmpeg timed out")
        shutil.copy2(str(original_path), str(output_path))

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    p = argparse.ArgumentParser(
        description="Gachi Remix — gachi-ремикс любой песни через AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  %(prog)s track.mp3\n"
            "  %(prog)s track.mp3 --backend ollama --llm-model qwen2.5:7b\n"
            "  %(prog)s track.mp3 --backend gemini --api-key KEY\n"
            "  %(prog)s track.mp3 --dry-run --save-placements plan.json\n"
            "  %(prog)s папка/ --output-dir remixes/\n"
            "\n"
            "Установка Ollama (бесплатно, без ключей):\n"
            "  1. https://ollama.com/download\n"
            "  2. ollama pull qwen2.5:7b  (или llama3.2:3b для слабых ПК)\n"
            "  3. Запустите скрипт с --backend ollama\n"
        ),
    )
    p.add_argument("input", help="Файл или папка с треками")
    p.add_argument("--backend", default="ollama",
                   choices=["ollama", "openai", "gemini", "none"],
                   help="Бэкенд для AI (def: ollama — локальный, без ключей)")
    p.add_argument("--api-key", help="API ключ (для gemini/openai)")
    p.add_argument("--llm-url", default="http://localhost:11434/v1",
                   help="URL для OpenAI-совместимого API (def: http://localhost:11434/v1)")
    p.add_argument("--llm-model", default="qwen2.5:7b",
                   help="Модель LLM (def: qwen2.5:7b)")
    p.add_argument("--sounds", default=str(Path(__file__).parent / "sounds"),
                   help="Папка со звуками (def: ./sounds)")
    p.add_argument("--output", "-o", help="Выходной файл")
    p.add_argument("--output-dir", help="Папка для результатов (при batch)")
    p.add_argument("--model", default="small",
                   choices=["tiny", "base", "small", "medium", "large", "large-v3"],
                   help="Модель whisper (def: small)")
    p.add_argument("--lang", help="Язык (по умолчанию автоопределение)")
    p.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    p.add_argument("--volume", type=float, default=85,
                   help="Громкость вставок в %% (def: 85)")
    p.add_argument("--random-chance", type=float, default=0.15,
                   help="Шанс случайной вставки 0-1 (def: 0.15)")
    p.add_argument("--min-pause", type=float, default=0.8,
                   help="Мин. пауза для случайной вставки, сек (def: 0.8)")
    p.add_argument("--no-fallback", action="store_true",
                   help="Не использовать rules-based, только AI")
    p.add_argument("--dry-run", action="store_true",
                   help="Не создавать файл, только показать placements")
    p.add_argument("--save-placements", help="Сохранить placements в JSON")
    p.add_argument("--load-placements", help="Загрузить placements из JSON")
    p.add_argument("--verbose", "-v", action="store_true", help="Подробные логи")
    p.add_argument("--debug", action="store_true", help="Debug-логи")

    args = p.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.verbose:
        logging.getLogger().setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore", "google", "huggingface_hub",
                  "urllib3", "fsspec", "PIL", "ctranslate2", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    check_ffmpeg()
    library = SoundLibrary(args.sounds)
    if not library.all_sounds:
        log.error("No sounds found in %s", args.sounds)
        sys.exit(1)
    log.info("Sounds: %d files, %d keywords", len(library.all_sounds),
             len(library.by_keyword))

    input_path = Path(args.input)
    files: list[Path] = []
    if input_path.is_dir():
        files = sorted(
            f for f in input_path.iterdir()
            if f.suffix.lower() in (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".mp4", ".mov", ".avi")
        )
        if not files:
            log.error("No audio/video files in %s", input_path)
            sys.exit(1)
        log.info("Batch: %d files", len(files))
    else:
        files = [input_path]

    for idx, fpath in enumerate(files, 1):
        if len(files) > 1:
            log.info("\n--- [%d/%d] %s ---", idx, len(files), fpath.name)

        output_path = args.output
        if not output_path:
            if args.output_dir:
                outdir = Path(args.output_dir)
                outdir.mkdir(parents=True, exist_ok=True)
                output_path = str(outdir / f"{fpath.stem}_gachi_remix.mp3")
            else:
                output_path = str(fpath.with_name(fpath.stem + "_gachi_remix.mp3"))

        try:
            _process_file(fpath, output_path, library, args)
        except KeyboardInterrupt:
            log.info("\nAborted")
            sys.exit(130)
        except Exception as exc:
            log.error("Failed: %s", exc)
            if args.debug:
                import traceback; traceback.print_exc()


def _process_file(input_path: Path, output_path: str,
                  library: SoundLibrary, args: argparse.Namespace) -> None:
    has_video = input_path.suffix.lower() in (".mp4", ".mov", ".avi")
    audio_path = _extract_audio(input_path) if has_video else input_path

    segments = transcribe(audio_path, args.model, args.lang, args.device)
    has_words = any(seg.words for seg in segments)

    placements: list[Placement] = []
    used: dict[str, int] = {}

    # 1. Load pre-defined placements
    if args.load_placements:
        try:
            with open(args.load_placements) as f:
                data = json.load(f)
            placements = [Placement(**item) for item in data]
            log.info("Loaded %d placements from %s", len(placements),
                     args.load_placements)
        except Exception as exc:
            log.error("Can't load placements: %s", exc)

    # 2. LLM / AI matching
    if not args.load_placements and has_words:
        if args.backend == "gemini":
            if not args.api_key:
                log.warning("--backend gemini requires --api-key")
            else:
                log.info("Gemini matching...")
                ai_p = llm_match_gemini(segments, library, args.api_key)
                for pl in ai_p:
                    placements.append(pl)
                    used[pl.sound] = used.get(pl.sound, 0) + 1
                log.info("  Gemini: %d placements", len(ai_p))
        elif args.backend == "ollama":
            log.info("Ollama matching (%s)...", args.llm_model)
            ai_p = llm_match_openai(segments, library, args.llm_url,
                                    args.llm_model)
            for pl in ai_p:
                placements.append(pl)
                used[pl.sound] = used.get(pl.sound, 0) + 1
            log.info("  Ollama: %d placements", len(ai_p))
        elif args.backend == "openai":
            log.info("OpenAI matching (%s)...", args.llm_model)
            key = args.api_key or os.environ.get("OPENAI_API_KEY", "")
            if not key:
                log.warning("OPENAI_API_KEY not set")
            else:
                ai_p = llm_match_openai(segments, library, args.llm_url,
                                        args.llm_model, key)
                for pl in ai_p:
                    placements.append(pl)
                    used[pl.sound] = used.get(pl.sound, 0) + 1
                log.info("  OpenAI: %d placements", len(ai_p))

    # 3. Rules-based fallback (when AI fails or disabled)
    if not args.no_fallback and has_words and not args.load_placements:
        count = 0
        for seg in segments:
            for w in seg.words:
                match = library.match_by_rules(w.text, used)
                if match:
                    placements.append(Placement(start=w.start, sound=match))
                    used[match] = used.get(match, 0) + 1
                    count += 1
        if count:
            log.info("  Rules:  %d placements", count)

    # 4. Random on pauses
    if args.random_chance > 0 and has_words and not args.load_placements:
        pauses = find_pauses(segments, args.min_pause)
        count = 0
        for p in pauses:
            if random.random() < args.random_chance:
                placements.append(Placement(
                    start=p["start"], sound=library.random(),
                ))
                count += 1
        if count:
            log.info("  Random: %d placements", count)

    # 5. Fallback for instrumental / empty
    if not placements and not has_words and not args.load_placements:
        log.info("No vocals — evenly spaced random")
        dur = get_duration(audio_path) or 30
        for t in range(2, int(dur) - 1, 3):
            placements.append(Placement(start=float(t), sound=library.random()))
        log.info("  Random: %d placements", len(placements))

    if not placements:
        log.warning("No placements — copying original")
        shutil.copy2(str(audio_path), output_path)
        return

    placements.sort(key=lambda x: x.start)

    # Deduplicate (same second)
    deduped: list[Placement] = []
    last = -99
    for pl in placements:
        t = int(pl.start)
        if t != last:
            deduped.append(pl)
            last = t
        elif len(deduped) >= 2 and deduped[-2].sound == pl.sound:
            deduped.append(pl)
            last = t
    placements = deduped

    log.info("Total: %d unique placements", len(placements))
    for pl in placements[:8]:
        log.info("  [%6.2f] %s", pl.start, Path(pl.sound).name)
    if len(placements) > 8:
        log.info("  ... and %d more", len(placements) - 8)

    if args.save_placements:
        with open(args.save_placements, "w", encoding="utf-8") as f:
            json.dump([asdict(p) for p in placements], f,
                      ensure_ascii=False, indent=2)
        log.info("Placements saved: %s", args.save_placements)

    if args.dry_run:
        log.info("Dry-run: no file created")
        return

    mix(audio_path if not has_video else input_path,
        placements, output_path, args.volume)


def _extract_audio(video_path: Path) -> Path:
    tmp = Path(tempfile.mkdtemp()) / f"{video_path.stem}_audio.mp3"
    log.info("Extracting audio from %s...", video_path.name)
    subprocess.run(["ffmpeg", "-y", "-i", str(video_path), "-q:a", "0",
                    "-map", "a", str(tmp)],
                   capture_output=True, check=True, timeout=120)
    return tmp


if __name__ == "__main__":
    main()
