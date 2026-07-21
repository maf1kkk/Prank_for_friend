import os, sys, json, re, random, argparse, subprocess, tempfile, shutil, math
from pathlib import Path

warnings_filter = None
try:
    import warnings; warnings.filterwarnings("ignore")
except: pass

SOUNDS_DIR = Path(__file__).parent / "sounds"

GEMINI_PROMPT = """You are a music remix assistant. Given lyrics with timestamps,
suggest where to insert gachi sound effects contextually.
Return a JSON array of: [{"start": <seconds>, "sound": "<filename>"}]

Available sounds and their keywords:
{sound_list}

Rules:
- Match sound keywords to nearby lyrics contextually (e.g. "cum" -> cumming, "fuck" -> fuck)
- Place sounds right BEFORE the relevant word
- 2-5 inserts per minute feels natural
- Return ONLY the JSON array, no other text

Lyrics with timestamps:
{lyrics}"""

def extract_keywords(path):
    name = Path(path).stem.lower()
    name = re.sub(r'[_\-\s]+', ' ', name)
    name = re.sub(r'voicy\d*', '', name)
    name = re.sub(r'\d+', '', name)
    return [w for w in name.split() if len(w) > 1]

def build_sound_map(sounds_dir):
    sounds_dir = Path(sounds_dir)
    if not sounds_dir.exists():
        print(f"No sounds directory: {sounds_dir}")
        return {}
    sound_map = {}
    for f in sorted(sounds_dir.iterdir()):
        if f.suffix.lower() in (".mp3", ".wav", ".ogg", ".flac", ".m4a"):
            kws = extract_keywords(f)
            if kws:
                for kw in kws:
                    sound_map.setdefault(kw, []).append(str(f))
            sound_map.setdefault("_all", []).append(str(f))
    return sound_map

def match_rules(word_text, sound_map, used_sounds):
    wt = word_text.lower().strip(".,!?;:'\"")
    if not wt:
        return None
    best = []
    for kw, files in sound_map.items():
        if kw == "_all":
            continue
        if kw in wt or wt in kw:
            best.extend(files)
    if not best:
        return None
    candidates = [f for f in best if used_sounds.get(f, 0) < 2]
    return random.choice(candidates) if candidates else random.choice(best)

def match_with_gemini(segments, sound_map, api_key):
    from google import genai
    client = genai.Client(api_key=api_key)
    sound_list = "\n".join(f"- {Path(f).stem}" for f in sound_map.get("_all", []))
    lyrics_lines = []
    for seg in segments:
        for w in seg.get("words", []):
            lyrics_lines.append(f"[{w['start']:.2f}] {w['text']}")
    lyrics = "\n".join(lyrics_lines)
    prompt = GEMINI_PROMPT.format(sound_list=sound_list, lyrics=lyrics)
    resp = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
    try:
        text = resp.text.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        return json.loads(text)
    except Exception as e:
        print(f"Gemini parse error: {e}")
        print(f"Raw: {resp.text[:500]}")
        return []

def get_audio_duration(path):
    r = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except:
        return 0

def transcribe(audio_path, model_name, language, device):
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device=device, compute_type="float32")
    print(f"Transcribing with {model_name}...")
    segments, info = model.transcribe(str(audio_path), language=language or None, word_timestamps=True)
    result = []
    for seg in segments:
        words = []
        for w in (seg.words or []):
            words.append({"text": w.word.strip(), "start": w.start, "end": w.end})
        result.append({"start": seg.start, "end": seg.end, "text": (seg.text or "").strip(), "words": words})
    print(f"  Language: {info.language} ({info.language_probability:.1%}), segments: {len(result)}")
    return result

def find_pauses(segments, min_pause=0.5):
    words = [w for seg in segments for w in seg.get("words", [])]
    pauses = []
    for i in range(len(words) - 1):
        gap = words[i + 1]["start"] - words[i]["end"]
        if gap >= min_pause:
            pauses.append({"start": words[i]["end"], "end": words[i + 1]["start"], "duration": gap})
    return pauses

def ffmpeg_mix(original_path, placements, output_path, volume_pct):
    tmpdir = Path(tempfile.mkdtemp())
    try:
        dur = get_audio_duration(original_path)
        if dur <= 0:
            dur = 30
        dur_ms = int(dur * 1000)
        delay_inputs = []
        delay_filters = []
        for i, p in enumerate(placements):
            spath = p["sound"]
            if not os.path.exists(spath):
                alt = Path(SOUNDS_DIR) / Path(spath).name
                if alt.exists():
                    spath = str(alt)
                else:
                    alt2 = Path(__file__).parent / "sounds" / Path(spath).name
                    if alt2.exists():
                        spath = str(alt2)
                    else:
                        print(f"  Not found: {spath}")
                        continue
            delay_ms = int(p["start"] * 1000)
            if delay_ms > dur_ms:
                continue
            label = f"s{i}"
            delay_inputs.append(f"-i")
            delay_inputs.append(spath)
            delay_filters.append(f"[{i+1}:a]volume={volume_pct/100}[a{i}];")
            delay_filters.append(f"[a{i}]adelay={delay_ms}|{delay_ms}[{label}];")
        if not delay_filters:
            print("No sounds to place!")
            shutil.copy2(original_path, output_path)
            return
        filter_str = "".join(delay_filters)
        mix_inputs = "[0:a]" + "".join(f"[{l}]" for l in [f"s{i}" for i in range(len(placements))])
        filter_str += f"{mix_inputs}amix=inputs={len(placements)+1}:duration=first"
        print(f"Mixing {len(placements)} sounds via FFmpeg...")
        cmd = ["ffmpeg", "-y", "-i", original_path] + delay_inputs + \
              ["-filter_complex", filter_str, "-ac", "2", "-b:a", "192k", str(output_path)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            errors = [l for l in r.stderr.split("\n") if "error" in l.lower() or "invalid" in l.lower()]
            for e in errors[:5]:
                print(f"  FFmpeg: {e.strip()}")
            if not errors:
                print("  FFmpeg failed (see output)")
            shutil.copy2(original_path, output_path)
            print(f"  Original copied (no mix): {output_path}")
        else:
            print(f"Done: {output_path}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

def main():
    parser = argparse.ArgumentParser(description="Gachi Remix — автоматический gachi-ремикс любой песни")
    parser.add_argument("input", help="Путь к аудиофайлу")
    parser.add_argument("--sounds", default=str(SOUNDS_DIR), help="Папка со звуками")
    parser.add_argument("--output", help="Выходной файл (по умолчанию: input_gachi_remix.mp3)")
    parser.add_argument("--model", default="small", choices=["tiny", "base", "small", "medium", "large"],
                        help="Модель Whisper (def: small)")
    parser.add_argument("--lang", help="Язык для распознавания (по умолчанию авто)")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"], help="Устройство (def: cpu)")
    parser.add_argument("--api-key", help="API ключ Gemini (включает AI-матчинг)")
    parser.add_argument("--volume", type=float, default=100, help="Громкость вставок в процентах (def: 100)")
    parser.add_argument("--random-chance", type=float, default=0.2, help="Шанс случайной вставки 0-1 (def: 0.2)")
    parser.add_argument("--no-rules", action="store_true", help="Отключить rules-based матчинг, только AI")
    args = parser.parse_args()
    input_path = Path(args.input)
    output_path = args.output or str(input_path.with_name(input_path.stem + "_gachi_remix.mp3"))
    sound_map = build_sound_map(Path(args.sounds))
    if not sound_map or not sound_map.get("_all"):
        print(f"No sounds in {args.sounds}")
        sys.exit(1)
    print(f"Loaded {len(sound_map['_all'])} sounds, {len(sound_map)-1} keywords")
    segments = transcribe(str(input_path), args.model, args.lang, args.device)
    has_words = any(seg.get("words") for seg in segments)
    placements = []
    used_sounds = {}
    if args.api_key and has_words:
        print("Matching with Gemini AI...")
        ai_p = match_with_gemini(segments, sound_map, args.api_key)
        for p in ai_p:
            placements.append(p)
            f = p["sound"]
            used_sounds[f] = used_sounds.get(f, 0) + 1
        print(f"  AI: {len(ai_p)} placements")
    if not args.no_rules and has_words:
        rules_found = 0
        for seg in segments:
            for w in seg.get("words", []):
                match = match_rules(w["text"], sound_map, used_sounds)
                if match:
                    placements.append({"start": w["start"], "sound": match})
                    used_sounds[match] = used_sounds.get(match, 0) + 1
                    rules_found += 1
        if rules_found:
            print(f"  Rules: {rules_found} placements")
    if has_words and args.random_chance > 0:
        pauses = find_pauses(segments)
        n = 0
        all_sounds = sound_map["_all"]
        for p in pauses:
            if p["duration"] > 1.0 and random.random() < args.random_chance:
                placements.append({"start": p["start"], "sound": random.choice(all_sounds)})
                n += 1
        if n:
            print(f"  Random: {n} placements")
    if not placements and not has_words:
        dur = get_audio_duration(str(input_path))
        all_sounds = sound_map["_all"]
        for t in range(0, int(dur), 3):
            placements.append({"start": t, "sound": random.choice(all_sounds)})
        print(f"  Random (instrumental): {len(placements)} placements")
    if not placements:
        print("No placements found!")
        sys.exit(1)
    print(f"Total: {len(placements)} sound placements")
    ffmpeg_mix(str(input_path), placements, output_path, args.volume)

if __name__ == "__main__":
    main()
