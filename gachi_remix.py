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

VERSION = "1.0.0"

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

@dataclass
class Pause:
    start: float
    end: float

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

SUPPORTED_AUDIO = (".mp3", ".wav", ".ogg", ".flac", ".m4a")
SUPPORTED_VIDEO = (".mp4", ".mov", ".avi")
SUPPORTED_EXT = SUPPORTED_AUDIO + SUPPORTED_VIDEO

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
- At most {max_p} inserts total, spread across the whole track
- Prioritize: cum/fuck/yeah/ah/oh/slap sounds at lewd/excited moments
- Be creative — think like a DJ making a mashup

Return ONLY a JSON array of {{"start": <seconds>, "sound": "<filename>"}}.
No markdown, no explanation. Max {max_p} items."""

def _build_llm_prompt(segments: list[Segment], library: SoundLibrary,
                      max_p: int = 15) -> str:
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

    prompt = LLM_SYSTEM.format(sound_list=sound_list, max_p=max_p)
    prompt += f"\n\nLyrics:\n{lyrics}"
    return prompt

def _parse_llm_response(text: str, library: SoundLibrary) -> list[Placement]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, list):
        log.warning("  LLM: response is not a list")
        return []
    placements = []
    for item in data:
        try:
            spath = library.lookup(item.get("sound", ""))
            if spath:
                placements.append(Placement(
                    start=float(item["start"]), sound=spath,
                ))
        except (TypeError, ValueError, KeyError):
            continue
    return placements


def llm_match_openai(segments: list[Segment], library: SoundLibrary,
                     api_url: str, model: str, api_key: str = "ollama",
                     max_placements: int = 15) -> list[Placement]:
    from openai import OpenAI, AuthenticationError, RateLimitError, APIStatusError

    client = OpenAI(base_url=api_url, api_key=api_key, max_retries=0)
    prompt = _build_llm_prompt(segments, library, max_placements)

    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a precise JSON generator."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=2048,
            )
            text = resp.choices[0].message.content or ""
            return _parse_llm_response(text, library)
        except AuthenticationError:
            log.error("  LLM: invalid API key")
            return []
        except RateLimitError:
            log.warning("  LLM: rate limited (attempt %d)", attempt + 1)
            if attempt == 0:
                time.sleep(3)
        except APIStatusError as e:
            log.warning("  LLM: API error %s (attempt %d)", e.status_code, attempt + 1)
            if attempt == 0:
                time.sleep(2)
        except json.JSONDecodeError:
            log.warning("  LLM: malformed JSON (attempt %d)", attempt + 1)
        except Exception as exc:
            log.warning("  LLM: %s (no retry)", exc)
            return []
        time.sleep(1)
    return []


def llm_match_gemini(segments: list[Segment], library: SoundLibrary,
                     api_key: str, max_placements: int = 15) -> list[Placement]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = _build_llm_prompt(segments, library, max_placements)

    for attempt in range(2):
        try:
            resp = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7, max_output_tokens=2048,
                ),
            )
            return _parse_llm_response(resp.text, library)
        except json.JSONDecodeError:
            log.warning("  Gemini: malformed JSON (attempt %d)", attempt + 1)
        except Exception as exc:
            log.warning("  Gemini: %s (no retry)", exc)
            return []
        time.sleep(1)
    return []

# ---------------------------------------------------------------------------
# Matchers composition
# ---------------------------------------------------------------------------

def find_pauses(segments: list[Segment], min_gap: float = 0.5) -> list[Pause]:
    words = [w for seg in segments for w in seg.words]
    pauses = []
    for i in range(len(words) - 1):
        gap = words[i + 1].start - words[i].end
        if gap >= min_gap:
            pauses.append(Pause(start=words[i].end, end=words[i + 1].start))
    return pauses

def deduplicate(placements: list[Placement]) -> list[Placement]:
    if not placements:
        return []
    placements.sort(key=lambda x: x.start)
    result = [placements[0]]
    for pl in placements[1:]:
        prev = result[-1]
        gap = pl.start - prev.start
        if gap >= 0.5 or pl.sound != prev.sound:
            result.append(pl)
    return result

# ---------------------------------------------------------------------------
# Cost helpers
# ---------------------------------------------------------------------------

BACKEND_CONFIG = {
    "deepseek": {"url": "https://api.deepseek.com/v1",  "model": "deepseek-chat", "cost": (0.0002, 0.0004)},
    "ollama":   {"url": "http://localhost:11434/v1",     "model": "qwen2.5:7b",   "cost": (0, 0)},
    "gemini":   {"url": "",                               "model": "gemini-2.0-flash", "cost": (0, 0)},
    "openai":   {"url": "https://api.openai.com/v1",     "model": "gpt-4o-mini",  "cost": (0.0015, 0.0060)},
    "none":     {"url": "",                               "model": "",             "cost": (0, 0)},
}

def estimate_cost(backend: str, duration_sec: float) -> str:
    cfg = BACKEND_CONFIG.get(backend, {})
    rate_in, rate_out = cfg.get("cost", (0, 0))
    est_in = int(duration_sec * 2) + 200
    est_out = 300
    cost = (est_in / 1000 * rate_in) + (est_out / 1000 * rate_out)
    if cost == 0:
        return "0 (free)"
    return f"~${cost:.4f} (~{cost * 100:.2f} cents)"

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
    vol = min(1.0, max(0.01, volume_pct / 100))

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
    timeout = max(60, int(dur * 1.5))
    cmd = [
        "ffmpeg", "-y",
        "-i", str(original_path),
        *delay_inputs,
        "-filter_complex", "".join(filters),
        "-ac", "2", "-b:a", "192k",
        str(output_path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            for line in r.stderr.split("\n"):
                if any(w in line.lower() for w in ["error", "invalid", "cannot"]):
                    log.error("  FFmpeg: %s", line.strip()[:120])
            log.warning("  FFmpeg failed — copying original")
            shutil.copy2(str(original_path), str(output_path))
        else:
            log.info("Saved: %s", output_path)
    except subprocess.TimeoutExpired:
        log.error("FFmpeg timed out (%ds)", timeout)
        shutil.copy2(str(original_path), str(output_path))

# ---------------------------------------------------------------------------
# Video extraction (with cleanup)
# ---------------------------------------------------------------------------

_temp_dirs: list[Path] = []

def _extract_audio(video_path: Path) -> Path:
    tmpdir = Path(tempfile.mkdtemp(prefix="gachi_"))
    _temp_dirs.append(tmpdir)
    out = tmpdir / f"{video_path.stem}_audio.mp3"
    log.info("Extracting audio from %s...", video_path.name)
    subprocess.run(["ffmpeg", "-y", "-i", str(video_path), "-q:a", "0",
                    "-map", "a", str(out)],
                   capture_output=True, check=True, timeout=120)
    return out

def cleanup_temp():
    for d in _temp_dirs:
        try:
            shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Process pipeline
# ---------------------------------------------------------------------------

def _run_ai_match(backend: str, segments: list[Segment], library: SoundLibrary,
                  api_key: str | None, llm_url: str, llm_model: str,
                  max_placements: int) -> list[Placement]:
    mp = min(max_placements, 30)
    if backend == "gemini":
        if not api_key:
            log.warning("--backend gemini requires --api-key")
            return []
        log.info("Gemini matching...")
        return llm_match_gemini(segments, library, api_key, mp)
    elif backend == "deepseek":
        key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        if not key:
            log.warning("--backend deepseek requires --api-key")
            return []
        log.info("DeepSeek matching (%s)...", llm_model)
        return llm_match_openai(segments, library,
                                "https://api.deepseek.com/v1", llm_model, key, mp)
    elif backend == "ollama":
        log.info("Ollama matching (%s)...", llm_model)
        return llm_match_openai(segments, library, llm_url, llm_model,
                                max_placements=mp)
    elif backend == "openai":
        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            log.warning("OPENAI_API_KEY not set")
            return []
        log.info("OpenAI matching (%s)...", llm_model)
        return llm_match_openai(segments, library, llm_url, llm_model, key, mp)
    return []


def _run_rules_match(segments: list[Segment], library: SoundLibrary) -> list[Placement]:
    used: dict[str, int] = {}
    result = []
    for seg in segments:
        for w in seg.words:
            match = library.match_by_rules(w.text, used)
            if match:
                result.append(Placement(start=w.start, sound=match))
                used[match] = used.get(match, 0) + 1
    return result


def _run_random_match(segments: list[Segment], library: SoundLibrary,
                      chance: float, min_gap: float) -> list[Placement]:
    pauses = find_pauses(segments, min_gap)
    result = []
    for p in pauses:
        if random.random() < chance:
            result.append(Placement(start=p.start, sound=library.random()))
    return result


def _instrumental_fallback(audio_path: Path, library: SoundLibrary) -> list[Placement]:
    dur = get_duration(audio_path) or 30
    result = []
    for t in range(2, int(dur) - 1, 3):
        result.append(Placement(start=float(t), sound=library.random()))
    return result


def process_file(input_path: Path, output_path: str, library: SoundLibrary,
                 args: argparse.Namespace) -> None:
    import argparse
    has_video = input_path.suffix.lower() in SUPPORTED_VIDEO
    audio_path = _extract_audio(input_path) if has_video else input_path

    segments = transcribe(audio_path, args.model, args.lang, args.device)
    has_words = any(seg.words for seg in segments)

    if has_words and args.backend != "none" and not args.load_placements:
        dur = get_duration(audio_path)
        log.info("  ~Cost: %s", estimate_cost(args.backend, dur))

    placements: list[Placement] = []

    # 1. Load pre-defined placements
    if args.load_placements:
        try:
            with open(args.load_placements) as f:
                data = json.load(f)
            placements = [Placement(**item) for item in data]
            log.info("Loaded %d placements", len(placements))
        except Exception as exc:
            log.error("Can't load placements: %s", exc)

    # 2. AI matching
    if not args.load_placements and has_words and args.backend != "none":
        ai_p = _run_ai_match(args.backend, segments, library,
                             args.api_key, args.llm_url, args.llm_model,
                             args.max_placements)
        placements.extend(ai_p)
        if ai_p:
            log.info("  %s: %d placements", args.backend.capitalize(), len(ai_p))

    # 3. Rules-based
    if not args.no_fallback and has_words and not args.load_placements:
        rules_p = _run_rules_match(segments, library)
        placements.extend(rules_p)
        if rules_p:
            log.info("  Rules: %d placements", len(rules_p))

    # 4. Random on pauses
    if args.random_chance > 0 and has_words and not args.load_placements:
        rand_p = _run_random_match(segments, library, args.random_chance, args.min_pause)
        placements.extend(rand_p)
        if rand_p:
            log.info("  Random: %d placements", len(rand_p))

    # 5. Instrumental fallback
    if not placements and not has_words and not args.load_placements:
        placements = _instrumental_fallback(audio_path, library)
        log.info("  Instrumental: %d placements", len(placements))

    if not placements:
        log.warning("No placements — copying original")
        shutil.copy2(str(audio_path), output_path)
        return

    placements = deduplicate(placements)

    log.info("Total: %d placements", len(placements))
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _setup_logging(debug: bool, verbose: bool) -> None:
    if debug:
        logging.getLogger().setLevel(logging.DEBUG)
    elif verbose:
        logging.getLogger().setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore", "google", "huggingface_hub",
                  "urllib3", "fsspec", "PIL", "ctranslate2", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _collect_files(input_path: Path, max_files: int, yes: bool) -> list[Path]:
    if not input_path.exists():
        log.error("File not found: %s", input_path)
        sys.exit(1)
    if input_path.is_dir():
        files = sorted(f for f in input_path.iterdir() if f.suffix.lower() in SUPPORTED_EXT)
        if not files:
            log.error("No audio/video files in %s", input_path)
            sys.exit(1)
        if len(files) > max_files and not yes:
            ans = input(f"  {len(files)} files. Max {max_files}. Continue? [y/N] ")
            if ans.lower() != "y":
                log.info("Aborted")
                sys.exit(0)
        files = files[:max_files]
        log.info("Batch: %d files", len(files))
        return files
    return [input_path]


def _resolve_output(input_path: Path, output: str | None, output_dir: str | None) -> str:
    if output:
        return output
    if output_dir:
        outdir = Path(output_dir)
        outdir.mkdir(parents=True, exist_ok=True)
        return str(outdir / f"{input_path.stem}_gachi_remix.mp3")
    return str(input_path.with_name(input_path.stem + "_gachi_remix.mp3"))


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(
        description="Gachi Remix — gachi-ремикс любой песни через AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  %(prog)s track.mp3\n"
            "  %(prog)s track.mp3 --backend ollama\n"
            "  %(prog)s track.mp3 --backend deepseek --api-key sk-...\n"
            "  %(prog)s track.mp3 --backend gemini --api-key KEY\n"
            "  %(prog)s track.mp3 --dry-run --save-placements plan.json\n"
            "  %(prog)s папка/ --output-dir remixes/\n"
            "\n"
            "Установка Ollama:\n"
            "  1. https://ollama.com/download\n"
            "  2. ollama pull qwen2.5:7b\n"
            "  3. Запустите с --backend ollama\n"
        ),
    )
    p.add_argument("input", help="Файл или папка с треками")
    p.add_argument("--version", "-V", action="version", version=f"%(prog)s {VERSION}")
    p.add_argument("--backend", default="ollama",
                   choices=list(BACKEND_CONFIG),
                   help="Бэкенд AI (def: ollama)")
    p.add_argument("--api-key", help="API ключ (deepseek/gemini/openai)")
    p.add_argument("--llm-url", default=BACKEND_CONFIG["ollama"]["url"],
                   help="URL API (def: http://localhost:11434/v1)")
    p.add_argument("--llm-model",
                   help="Модель LLM (def: qwen2.5:7b / deepseek-chat)")
    p.add_argument("--max-placements", type=int, default=15,
                   help="Максимум вставок (def: 15)")
    p.add_argument("--max-files", type=int, default=5,
                   help="Максимум файлов при batch (def: 5)")
    p.add_argument("--sounds", default=str(Path(__file__).parent / "sounds"),
                   help="Папка со звуками")
    p.add_argument("--output", "-o", help="Выходной файл")
    p.add_argument("--output-dir", help="Папка для результатов (batch)")
    p.add_argument("--model", default="small",
                   choices=["tiny", "base", "small", "medium", "large", "large-v3"],
                   help="Модель whisper (def: small)")
    p.add_argument("--lang", help="Язык (авто по умолчанию)")
    p.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    p.add_argument("--volume", type=float, default=85,
                   help="Громкость вставок %% (def: 85)")
    p.add_argument("--random-chance", type=float, default=0.15,
                   help="Шанс случайных вставок 0-1 (def: 0.15)")
    p.add_argument("--min-pause", type=float, default=0.8,
                   help="Мин. пауза для случайных вставок (def: 0.8)")
    p.add_argument("--no-fallback", action="store_true",
                   help="Только AI, без правил")
    p.add_argument("--dry-run", action="store_true",
                   help="Без создания файла")
    p.add_argument("--save-placements", help="Сохранить placements в JSON")
    p.add_argument("--load-placements", help="Загрузить placements из JSON")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Авто-подтверждение batch")
    p.add_argument("--verbose", "-v", action="store_true", help="Подробнее")
    p.add_argument("--debug", action="store_true", help="Debug логи")

    args = p.parse_args()

    if not args.llm_model:
        args.llm_model = (BACKEND_CONFIG["deepseek"]["model"]
                          if args.backend == "deepseek"
                          else BACKEND_CONFIG.get(args.backend, {}).get("model", "qwen2.5:7b"))

    _setup_logging(args.debug, args.verbose)
    check_ffmpeg()

    library = SoundLibrary(args.sounds)
    if not library.all_sounds:
        log.error("No sounds in %s", args.sounds)
        sys.exit(1)
    log.info("Sounds: %d files, %d keywords", len(library.all_sounds),
             len(library.by_keyword))

    files = _collect_files(Path(args.input), args.max_files, args.yes)

    try:
        for idx, fpath in enumerate(files, 1):
            if len(files) > 1:
                log.info("\n--- [%d/%d] %s ---", idx, len(files), fpath.name)
            output_path = _resolve_output(fpath, args.output, args.output_dir)
            process_file(fpath, output_path, library, args)
    except KeyboardInterrupt:
        log.info("\nAborted")
    finally:
        cleanup_temp()


if __name__ == "__main__":
    main()
