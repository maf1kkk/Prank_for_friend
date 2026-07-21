"""
Gachi Remix GUI — Desktop-приложение для автоматического gachi-ремикса.
"""

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from gachi_remix import (
    SoundLibrary, Placement, check_ffmpeg,
    transcribe, find_pauses,
    llm_match_openai, llm_match_gemini,
    mix,
)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")

log = logging.getLogger("gachi_remix_gui")

BACKENDS = {
    "deepseek": {"url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "ollama":   {"url": "http://localhost:11434/v1",   "model": "qwen2.5:7b"},
    "gemini":   {"url": "",                             "model": "gemini-2.0-flash"},
    "openai":   {"url": "https://api.openai.com/v1",    "model": "gpt-4o-mini"},
    "none":     {"url": "",                             "model": ""},
}


class LogHandler(logging.Handler):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def emit(self, record):
        msg = self.format(record)
        self.callback(msg)


class GachiRemixGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Gachi Remix")
        self.geometry("700x620")
        self.minsize(600, 520)
        self.iconbitmap(default="")  # можно добавить иконку

        self.input_path: str | None = None
        self.output_path: str | None = None
        self._running = False

        self._build_ui()

        # Логи в UI
        handler = LogHandler(self._on_log)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger("gachi_remix").setLevel(logging.INFO)
        logging.getLogger("gachi_remix").addHandler(handler)

        # Проверка FFmpeg при старте
        self.after(500, self._check_ffmpeg)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        # --- File selection ---
        file_frame = ctk.CTkFrame(self)
        file_frame.pack(fill="x", padx=12, pady=(12, 4))

        ctk.CTkLabel(file_frame, text="Входной файл:", font=("", 13)).pack(side="left", padx=(8, 8))
        self.file_label = ctk.CTkLabel(file_frame, text="не выбран", fg_color=("gray80", "gray25"),
                                        corner_radius=6, anchor="w", padx=8)
        self.file_label.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(file_frame, text="Обзор", width=80, command=self._browse_file).pack(side="left", padx=(0, 8))

        # --- Backend ---
        params = ctk.CTkFrame(self)
        params.pack(fill="x", padx=12, pady=4)

        ctk.CTkLabel(params, text="Бэкенд:", font=("", 13)).grid(row=0, column=0, sticky="w", padx=(8, 4), pady=4)
        self.backend_var = ctk.StringVar(value="deepseek")
        self.backend_menu = ctk.CTkOptionMenu(params, values=list(BACKENDS.keys()),
                                                variable=self.backend_var,
                                                command=self._on_backend_change, width=120)
        self.backend_menu.grid(row=0, column=1, sticky="w", padx=(0, 12), pady=4)

        ctk.CTkLabel(params, text="API ключ:", font=("", 13)).grid(row=0, column=2, sticky="w", padx=(4, 4), pady=4)
        self.api_key_entry = ctk.CTkEntry(params, placeholder_text="sk-... (необязательно)", width=200, show="*")
        self.api_key_entry.grid(row=0, column=3, sticky="ew", padx=(0, 8), pady=4)

        # --- Model ---
        ctk.CTkLabel(params, text="Модель LLM:", font=("", 13)).grid(row=1, column=0, sticky="w", padx=(8, 4), pady=4)
        self.model_entry = ctk.CTkEntry(params, width=180)
        self.model_entry.grid(row=1, column=1, sticky="w", padx=(0, 12), pady=4)
        self._on_backend_change("deepseek")

        ctk.CTkLabel(params, text="Модель Whisper:", font=("", 13)).grid(row=1, column=2, sticky="w", padx=(4, 4), pady=4)
        self.whisper_var = ctk.StringVar(value="small")
        ctk.CTkOptionMenu(params, values=["tiny", "base", "small", "medium", "large"],
                           variable=self.whisper_var, width=100).grid(row=1, column=3, sticky="w", padx=(0, 8), pady=4)

        params.columnconfigure(3, weight=1)

        # --- Sliders ---
        sliders = ctk.CTkFrame(self)
        sliders.pack(fill="x", padx=12, pady=4)

        ctk.CTkLabel(sliders, text="Громкость вставок:").grid(row=0, column=0, sticky="w", padx=(8, 4))
        self.volume_slider = ctk.CTkSlider(sliders, from_=10, to=150, number_of_steps=28, command=self._on_volume)
        self.volume_slider.set(85)
        self.volume_slider.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.volume_label = ctk.CTkLabel(sliders, text="85%", width=40)
        self.volume_label.grid(row=0, column=2, sticky="w", padx=(0, 8))

        ctk.CTkLabel(sliders, text="Случайные вставки:").grid(row=1, column=0, sticky="w", padx=(8, 4))
        self.random_slider = ctk.CTkSlider(sliders, from_=0, to=100, number_of_steps=20, command=self._on_random)
        self.random_slider.set(15)
        self.random_slider.grid(row=1, column=1, sticky="ew", padx=(0, 8))
        self.random_label = ctk.CTkLabel(sliders, text="15%", width=40)
        self.random_label.grid(row=1, column=2, sticky="w", padx=(0, 8))

        sliders.columnconfigure(1, weight=1)

        # --- Checkboxes ---
        opts = ctk.CTkFrame(self)
        opts.pack(fill="x", padx=12, pady=4)

        self.no_fallback_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(opts, text="Только AI (без правил)", variable=self.no_fallback_var).pack(side="left", padx=8)
        self.dry_run_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(opts, text="Dry-run (без файла)", variable=self.dry_run_var).pack(side="left", padx=8)

        # --- Generate button ---
        self.gen_btn = ctk.CTkButton(self, text="🎵  Сгенерировать gachi-ремикс", height=42,
                                      font=("", 15, "bold"), command=self._generate)
        self.gen_btn.pack(fill="x", padx=12, pady=8)

        # --- Progress ---
        self.progress = ctk.CTkProgressBar(self, mode="determinate")
        self.progress.pack(fill="x", padx=12, pady=(0, 4))
        self.progress.set(0)

        # --- Log ---
        log_frame = ctk.CTkFrame(self)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 4))

        self.log_text = ctk.CTkTextbox(log_frame, state="disabled", font=("Consolas", 11))
        self.log_text.pack(fill="both", expand=True)

        # --- Output ---
        out_frame = ctk.CTkFrame(self)
        out_frame.pack(fill="x", padx=12, pady=(0, 12))

        ctk.CTkLabel(out_frame, text="Результат:", font=("", 13)).pack(side="left", padx=(8, 4))
        self.out_label = ctk.CTkLabel(out_frame, text="—", fg_color=("gray80", "gray25"),
                                       corner_radius=6, anchor="w", padx=8)
        self.out_label.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.play_btn = ctk.CTkButton(out_frame, text="▶ Играть", width=80, state="disabled", command=self._play)
        self.play_btn.pack(side="left", padx=(0, 4))
        self.open_btn = ctk.CTkButton(out_frame, text="📂 Папка", width=80, state="disabled", command=self._open_folder)
        self.open_btn.pack(side="left", padx=(0, 8))

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_backend_change(self, choice: str):
        cfg = BACKENDS.get(choice, {})
        self.model_entry.delete(0, "end")
        self.model_entry.insert(0, cfg.get("model", ""))
        if choice == "none":
            self.api_key_entry.configure(state="disabled", placeholder_text="—")
        else:
            self.api_key_entry.configure(state="normal", placeholder_text="sk-... (необязательно)")

    def _on_volume(self, val):
        self.volume_label.configure(text=f"{int(val)}%")

    def _on_random(self, val):
        self.random_label.configure(text=f"{int(val)}%")

    def _check_ffmpeg(self):
        try:
            check_ffmpeg()
            self._log("✅ FFmpeg найден")
        except SystemExit:
            self._log("❌ FFmpeg не найден! Установите: https://ffmpeg.org/download.html")
            messagebox.showerror("Ошибка", "FFmpeg не найден. Установите FFmpeg.")

    def _browse_file(self):
        path = filedialog.askopenfilename(
            title="Выберите аудиофайл",
            filetypes=[("Аудио/Видео", "*.mp3 *.wav *.ogg *.flac *.m4a *.mp4 *.mov *.avi"),
                       ("Все файлы", "*.*")],
        )
        if path:
            self.input_path = path
            self.file_label.configure(text=os.path.basename(path))
            self._log(f"Файл: {path}")

    def _log(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_log(self, msg: str):
        self.after(0, self._log, msg)

    def _set_running(self, running: bool):
        self._running = running
        state = "disabled" if running else "normal"
        self.gen_btn.configure(state=state, text="⏳ Генерация..." if running else "🎵  Сгенерировать gachi-ремикс")

    # ------------------------------------------------------------------
    # Generate
    # ------------------------------------------------------------------

    def _generate(self):
        if self._running:
            return
        if not self.input_path or not os.path.isfile(self.input_path):
            messagebox.showwarning("Ошибка", "Выберите входной файл")
            return

        self.output_path = None
        self.play_btn.configure(state="disabled")
        self.open_btn.configure(state="disabled")
        self.out_label.configure(text="⏳")

        self.progress.set(0)
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        self._set_running(True)
        threading.Thread(target=self._generate_thread, daemon=True).start()

    def _generate_thread(self):
        try:
            self._do_generate()
        except Exception as e:
            self.after(0, self._log, f"❌ Ошибка: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.after(0, lambda: self._set_running(False))

    def _do_generate(self):
        from pathlib import Path as P

        backend = self.backend_var.get()
        api_key = self.api_key_entry.get().strip() or None
        whisper_model = self.whisper_var.get()
        volume = self.volume_slider.get()
        random_chance = self.random_slider.get() / 100
        no_fallback = self.no_fallback_var.get()
        dry_run = self.dry_run_var.get()
        llm_model = self.model_entry.get().strip()

        input_path = P(self.input_path)
        sounds_dir = P(__file__).parent / "sounds"
        output_path = str(input_path.with_name(input_path.stem + "_gachi_remix.mp3"))

        self.after(0, lambda: self._log(f"🔧 Бэкенд: {backend} | Модель: {llm_model or '-'}"))

        library = SoundLibrary(str(sounds_dir))
        self.after(0, lambda: self._log(f"📁 Звуков: {len(library.all_sounds)}"))
        self.after(0, lambda: self.progress.configure(value=0.05))

        has_video = input_path.suffix.lower() in (".mp4", ".mov", ".avi")
        audio_path = input_path

        if has_video:
            import tempfile
            tmp = P(tempfile.mkdtemp()) / f"{input_path.stem}_audio.mp3"
            self.after(0, lambda: self._log("🎬 Извлекаю аудио из видео..."))
            import subprocess
            subprocess.run(["ffmpeg", "-y", "-i", str(input_path), "-q:a", "0",
                            "-map", "a", str(tmp)], capture_output=True, check=True, timeout=120)
            audio_path = tmp
            self.after(0, lambda: self.progress.configure(value=0.1))

        self.after(0, lambda: self._log("🎤 Распознаю текст (Whisper)..."))
        segments = transcribe(str(audio_path), whisper_model, None, "cpu")
        self.after(0, lambda: self.progress.configure(value=0.35))
        has_words = any(seg.words for seg in segments)

        placements: list[Placement] = []
        used: dict[str, int] = {}

        # AI matching
        if has_words and backend != "none":
            cfg = BACKENDS.get(backend, {})
            self.after(0, lambda: self._log(f"🧠 {backend.capitalize()} матчинг..."))

            if backend == "gemini":
                ai_p = llm_match_gemini(segments, library, api_key or "")
            else:
                url = cfg.get("url", "http://localhost:11434/v1")
                key = api_key or "ollama" if backend == "ollama" else api_key or ""
                ai_p = llm_match_openai(segments, library, url, llm_model or cfg.get("model", ""), key)

            for pl in ai_p:
                placements.append(pl)
                used[pl.sound] = used.get(pl.sound, 0) + 1
            self.after(0, lambda: self._log(f"  {backend}: {len(ai_p)} placement'ов"))
            self.after(0, lambda: self.progress.configure(value=0.5))

        # Rules fallback
        if not no_fallback and has_words:
            cnt = 0
            for seg in segments:
                for w in seg.words:
                    match = library.match_by_rules(w.text, used)
                    if match:
                        placements.append(Placement(start=w.start, sound=match))
                        used[match] = used.get(match, 0) + 1
                        cnt += 1
            if cnt:
                self.after(0, lambda: self._log(f"  Правила: {cnt} placement'ов"))
            self.after(0, lambda: self.progress.configure(value=0.6))

        # Random on pauses
        if random_chance > 0 and has_words:
            pauses = find_pauses(segments, 0.8)
            cnt = 0
            import random as rnd
            for p in pauses:
                if rnd.random() < random_chance:
                    placements.append(Placement(start=p["start"], sound=library.random()))
                    cnt += 1
            if cnt:
                self.after(0, lambda: self._log(f"  Случайно: {cnt} placement'ов"))

        # Instrumental fallback
        if not placements and not has_words:
            dur = getattr(audio_path, "stat", lambda: None) and 30
            for t in range(2, int(dur) - 1, 3) if dur else range(2, 30, 3):
                placements.append(Placement(start=float(t), sound=library.random()))
            self.after(0, lambda: self._log(f"  Инструментал: {len(placements)} placement'ов"))

        if not placements:
            self.after(0, lambda: self._log("⚠️ Нет placement'ов — копирую оригинал"))
            import shutil
            shutil.copy2(str(audio_path), output_path)
            self.after(0, lambda: self._finish(output_path))
            return

        placements.sort(key=lambda x: x.start)
        deduped = []
        last_t = -99
        for pl in placements:
            t = int(pl.start)
            if t != last_t:
                deduped.append(pl)
                last_t = t
            elif len(deduped) >= 2 and deduped[-2].sound == pl.sound:
                deduped.append(pl)
                last_t = t
        placements = deduped

        self.after(0, lambda: self._log(f"🎯 Итого: {len(placements)} уникальных вставок"))
        for pl in placements[:6]:
            self.after(0, lambda p=pl: self._log(f"  [{p.start:6.2f}s] {P(p.sound).name}"))
        if len(placements) > 6:
            self.after(0, lambda: self._log(f"  ... и ещё {len(placements)-6}"))

        self.after(0, lambda: self.progress.configure(value=0.75))

        if dry_run:
            self.after(0, lambda: self._log("✅ Dry-run завершён"))
            self.after(0, lambda: self.progress.configure(value=1.0))
            self.after(0, lambda: self.out_label.configure(text="dry-run"))
            return

        # Mix
        self.after(0, lambda: self._log("🎛️  Микширую..."))
        mix_path = audio_path if not has_video else input_path
        mix(str(mix_path), placements, output_path, volume)
        self.after(0, lambda: self.progress.configure(value=1.0))
        self.after(0, lambda: self._finish(output_path))

    def _finish(self, path: str):
        self.output_path = path
        sz = os.path.getsize(path)
        self.out_label.configure(text=f"{os.path.basename(path)} ({sz/1024/1024:.1f} MB)")
        self.play_btn.configure(state="normal")
        self.open_btn.configure(state="normal")
        self._log(f"✅ Готово: {path}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _play(self):
        if self.output_path and os.path.isfile(self.output_path):
            os.startfile(self.output_path)

    def _open_folder(self):
        if self.output_path:
            os.startfile(os.path.dirname(self.output_path))


if __name__ == "__main__":
    GachiRemixGUI().mainloop()
