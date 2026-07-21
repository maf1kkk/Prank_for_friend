"""
Gachi Remix GUI — Desktop-приложение.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from gachi_remix import (
    VERSION, BACKEND_CONFIG, SoundLibrary, Placement,
    check_ffmpeg, get_duration, estimate_cost,
    transcribe, process_file,
)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")

log = logging.getLogger("gachi_remix_gui")


class LogHandler(logging.Handler):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def emit(self, record):
        self.callback(self.format(record))


class GachiRemixGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"Gachi Remix v{VERSION}")
        self.geometry("740x680")
        self.minsize(640, 560)

        self.input_path: str | None = None
        self.output_path: str | None = None
        self._running = False
        self._pause_slider = None

        self._build_ui()
        self._setup_logging()
        self.after(500, self._check_ffmpeg)

        # Drag & drop
        self.drop_target_register(self._tk_dnd_wrapper())
        self._bind_dnd()

    # ------------------------------------------------------------------
    # Drag & drop wrapper (cross-platform)
    # ------------------------------------------------------------------

    def _tk_dnd_wrapper(self):
        try:
            import tkinterdnd2
            return tkinterdnd2.TkinterDnD
        except ImportError:
            pass
        return None

    def _bind_dnd(self):
        self.file_label.bind("<Enter>", lambda e: self.file_label.configure(fg_color=("gray70", "gray35")))
        self.file_label.bind("<Leave>", lambda e: self.file_label.configure(fg_color=("gray80", "gray25")))

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        # Header
        header = ctk.CTkFrame(self, corner_radius=0, height=50)
        header.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        header.grid_propagate(False)
        ctk.CTkLabel(header, text="🎵  Gachi Remix", font=("", 18, "bold")).pack(side="left", padx=14)

        # ─── File ───
        f1 = ctk.CTkFrame(self)
        f1.grid(row=1, column=0, sticky="ew", padx=12, pady=(10, 2))

        ctk.CTkLabel(f1, text="Файл:", font=("", 13)).pack(side="left", padx=(8, 6))
        self.file_label = ctk.CTkLabel(f1, text="Перетащите файл или нажмите Обзор",
                                        fg_color=("gray80", "gray25"),
                                        corner_radius=6, anchor="w", padx=10,
                                        height=32, font=("", 12))
        self.file_label.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(f1, text="📁 Обзор", width=80, command=self._browse_file).pack(side="left", padx=(0, 8))

        # ─── Backend & Key ───
        f2 = ctk.CTkFrame(self)
        f2.grid(row=2, column=0, sticky="ew", padx=12, pady=2)

        ctk.CTkLabel(f2, text="Бэкенд:", font=("", 13)).grid(row=0, column=0, sticky="w", padx=(8, 4), pady=3)
        self.backend_var = ctk.StringVar(value="deepseek")
        self.backend_menu = ctk.CTkOptionMenu(f2, values=list(BACKEND_CONFIG),
                                                variable=self.backend_var,
                                                command=self._on_backend_change, width=110)
        self.backend_menu.grid(row=0, column=1, sticky="w", padx=(0, 8), pady=3)

        ctk.CTkLabel(f2, text="API ключ:", font=("", 13)).grid(row=0, column=2, sticky="w", padx=(4, 4), pady=3)
        self.key_entry = ctk.CTkEntry(f2, placeholder_text="sk-...", width=180, show="*")
        self.key_entry.grid(row=0, column=3, sticky="ew", padx=(0, 8), pady=3)

        ctk.CTkLabel(f2, text="Модель:", font=("", 13)).grid(row=1, column=0, sticky="w", padx=(8, 4), pady=3)
        self.model_entry = ctk.CTkEntry(f2, width=140)
        self.model_entry.grid(row=1, column=1, sticky="w", padx=(0, 8), pady=3)
        self._on_backend_change("deepseek")

        ctk.CTkLabel(f2, text="Whisper:", font=("", 13)).grid(row=1, column=2, sticky="w", padx=(4, 4), pady=3)
        self.whisper_var = ctk.StringVar(value="small")
        ctk.CTkOptionMenu(f2, values=["tiny", "base", "small", "medium", "large"],
                           variable=self.whisper_var, width=90).grid(row=1, column=3, sticky="w", padx=(0, 8), pady=3)
        f2.columnconfigure(3, weight=1)

        # ─── Sliders ───
        f3 = ctk.CTkFrame(self)
        f3.grid(row=3, column=0, sticky="ew", padx=12, pady=2)

        ctk.CTkLabel(f3, text="🔊").grid(row=0, column=0, padx=(8, 2))
        self.vol_slider = ctk.CTkSlider(f3, from_=10, to=100, command=lambda v: self.vol_label.configure(text=f"{int(v)}%"))
        self.vol_slider.set(85)
        self.vol_slider.grid(row=0, column=1, sticky="ew", padx=(0, 4))
        self.vol_label = ctk.CTkLabel(f3, text="85%", width=36)
        self.vol_label.grid(row=0, column=2, padx=(0, 12))

        ctk.CTkLabel(f3, text="🎲").grid(row=0, column=3, padx=(4, 2))
        self.rnd_slider = ctk.CTkSlider(f3, from_=0, to=50, number_of_steps=10,
                                          command=lambda v: self.rnd_label.configure(text=f"{int(v)}%"))
        self.rnd_slider.set(15)
        self.rnd_slider.grid(row=0, column=4, sticky="ew", padx=(0, 4))
        self.rnd_label = ctk.CTkLabel(f3, text="15%", width=36)
        self.rnd_label.grid(row=0, column=5, padx=(0, 8))
        f3.columnconfigure((1, 4), weight=1)

        # ─── Checkboxes ───
        f4 = ctk.CTkFrame(self)
        f4.grid(row=4, column=0, sticky="ew", padx=12, pady=2)

        self.no_fallback_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(f4, text="Только AI", variable=self.no_fallback_var).pack(side="left", padx=8)
        self.dry_run_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(f4, text="Dry-run", variable=self.dry_run_var).pack(side="left", padx=8)

        # ─── Generate ───
        self.gen_btn = ctk.CTkButton(self, text="🎵  Сгенерировать", height=44,
                                      font=("", 15, "bold"), command=self._generate)
        self.gen_btn.grid(row=5, column=0, sticky="ew", padx=12, pady=(6, 0))

        self.progress = ctk.CTkProgressBar(self, mode="determinate")
        self.progress.grid(row=6, column=0, sticky="ew", padx=12, pady=(4, 0))
        self.progress.set(0)

        self.status_label = ctk.CTkLabel(self, text="", font=("", 11), anchor="w")
        self.status_label.grid(row=7, column=0, sticky="ew", padx=14, pady=(0, 2))

        # ─── Log ───
        self.log_text = ctk.CTkTextbox(self, state="disabled", font=("Consolas", 11), height=120)
        self.log_text.grid(row=8, column=0, sticky="nsew", padx=12, pady=(2, 2))

        # ─── Output ───
        f5 = ctk.CTkFrame(self)
        f5.grid(row=9, column=0, sticky="ew", padx=12, pady=(0, 10))

        ctk.CTkLabel(f5, text="Готово:", font=("", 13)).pack(side="left", padx=(8, 4))
        self.out_label = ctk.CTkLabel(f5, text="—", fg_color=("gray80", "gray25"),
                                       corner_radius=6, anchor="w", padx=8)
        self.out_label.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.play_btn = ctk.CTkButton(f5, text="▶", width=40, state="disabled", command=self._play)
        self.play_btn.pack(side="left", padx=(0, 2))
        self.open_btn = ctk.CTkButton(f5, text="📂", width=40, state="disabled", command=self._open_folder)
        self.open_btn.pack(side="left", padx=(0, 8))

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_backend_change(self, choice: str):
        cfg = BACKEND_CONFIG.get(choice, {})
        self.model_entry.delete(0, "end")
        self.model_entry.insert(0, cfg.get("model", ""))
        if choice == "none":
            self.key_entry.configure(state="disabled", placeholder_text="—")
        else:
            self.key_entry.configure(state="normal", placeholder_text="sk-...")

    def _check_ffmpeg(self):
        try:
            check_ffmpeg()
        except SystemExit:
            self._log("❌ FFmpeg не найден! Установите: https://ffmpeg.org/download.html")
            messagebox.showerror("Ffmpeg Error", "FFmpeg не найден. Установите FFmpeg.")

    def _browse_file(self):
        path = filedialog.askopenfilename(
            title="Выберите файл",
            filetypes=[("Аудио/Видео", "*.mp3 *.wav *.ogg *.flac *.m4a *.mp4 *.mov *.avi"),
                       ("Все файлы", "*.*")],
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self.input_path = path
        self.file_label.configure(text=os.path.basename(path))
        self._log(f"Файл: {path}")
        dur = get_duration(path)
        if dur > 0:
            cost = estimate_cost(self.backend_var.get(), dur)
            self._log(f"  Длит: {dur:.0f}s | ~{cost}")

    def _log(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_log(self, msg: str):
        self.after(0, self._log, msg)

    def _set_status(self, text: str):
        self.status_label.configure(text=text)

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
        self._set_status("Подготовка...")

        self._running = True
        self.gen_btn.configure(state="disabled", text="⏳  Генерация...")
        threading.Thread(target=self._generate_thread, daemon=True).start()

    def _generate_thread(self):
        try:
            self._do_generate()
        except Exception as e:
            self.after(0, self._log, f"❌ Ошибка: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.after(0, self._on_done)

    def _on_done(self):
        self._running = False
        self.gen_btn.configure(state="normal", text="🎵  Сгенерировать")
        self._set_status("")

    def _do_generate(self):
        from pathlib import Path as P
        import random as rnd

        backend = self.backend_var.get()
        api_key = self.key_entry.get().strip() or None
        whisper_model = self.whisper_var.get()
        volume = self.vol_slider.get()
        random_chance = self.rnd_slider.get() / 100
        no_fallback = self.no_fallback_var.get()
        dry_run = self.dry_run_var.get()
        llm_model = self.model_entry.get().strip()

        input_path = P(self.input_path)
        sounds_dir = P(__file__).parent / "sounds"
        output_path = str(input_path.with_name(input_path.stem + "_gachi_remix.mp3"))

        self.after(0, lambda: self._log(f"🔧 {backend} | {llm_model}"))
        self.after(0, lambda: self.progress.configure(value=0.05))
        self.after(0, lambda: self._set_status("Загрузка звуков..."))

        library = SoundLibrary(str(sounds_dir))
        self.after(0, lambda: self._log(f"📁 {len(library.all_sounds)} звуков"))

        has_video = input_path.suffix.lower() in (".mp4", ".mov", ".avi")
        audio_path = input_path

        if has_video:
            import tempfile
            tmp = P(tempfile.mkdtemp()) / f"{input_path.stem}_audio.mp3"
            self.after(0, lambda: self._log("🎬 Извлекаю аудио..."))
            self.after(0, lambda: self._set_status("Извлечение аудио из видео..."))
            subprocess.run(["ffmpeg", "-y", "-i", str(input_path), "-q:a", "0",
                            "-map", "a", str(tmp)], capture_output=True, check=True, timeout=120)
            audio_path = tmp
            self.after(0, lambda: self.progress.configure(value=0.1))

        self.after(0, lambda: self._set_status("Распознавание речи (Whisper)..."))
        self.after(0, lambda: self._log("🎤 Распознаю текст..."))
        segments = transcribe(str(audio_path), whisper_model, None, "cpu")
        self.after(0, lambda: self.progress.configure(value=0.35))
        has_words = any(seg.words for seg in segments)

        # Cost & confirm (via Event, safe from worker thread)
        if has_words and backend in ("deepseek", "openai") and api_key and not dry_run:
            dur = get_duration(str(audio_path))
            cost = estimate_cost(backend, dur)
            ev = threading.Event()
            result = [False]
            def ask():
                result[0] = messagebox.askokcancel("Подтверждение", f"{backend}\nОценка: {cost}\nПродолжить?")
                ev.set()
            self.after(0, ask)
            ev.wait()
            if not result[0]:
                self.after(0, lambda: self._log("  Отменено"))
                return

        placements: list[Placement] = []
        used: dict[str, int] = {}

        # AI match
        if has_words and backend != "none":
            cfg = BACKEND_CONFIG.get(backend, {})
            cost = estimate_cost(backend, get_duration(str(audio_path)))
            self.after(0, lambda b=backend, c=cost: self._log(f"🧠 {b}: ~{c}"))
            self.after(0, lambda: self._set_status(f"AI матчинг ({backend})..."))

            from gachi_remix import llm_match_openai, llm_match_gemini
            mp = 15
            if backend == "gemini":
                ai_p = llm_match_gemini(segments, library, api_key or "", mp)
            else:
                url = cfg.get("url", "http://localhost:11434/v1")
                key = api_key or ("ollama" if backend == "ollama" else api_key or "")
                ai_p = llm_match_openai(segments, library, url,
                                        llm_model or cfg.get("model", ""), key, mp)
            for pl in ai_p:
                placements.append(pl)
                used[pl.sound] = used.get(pl.sound, 0) + 1
            self.after(0, lambda: self._log(f"  AI: {len(ai_p)} placements"))
            self.after(0, lambda: self.progress.configure(value=0.5))

        # Rules
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
                self.after(0, lambda: self._log(f"  Правила: {cnt}"))
            self.after(0, lambda: self.progress.configure(value=0.6))

        # Random
        if random_chance > 0 and has_words:
            from gachi_remix import find_pauses
            pauses = find_pauses(segments, 0.8)
            cnt = 0
            for p in pauses:
                if rnd.random() < random_chance:
                    placements.append(Placement(start=p.start, sound=library.random()))
                    cnt += 1
            if cnt:
                self.after(0, lambda: self._log(f"  Случайно: {cnt}"))

        # Instrumental
        if not placements and not has_words:
            dur = get_duration(str(audio_path)) or 30
            for t in range(2, int(dur) - 1, 3):
                placements.append(Placement(start=float(t), sound=library.random()))
            self.after(0, lambda: self._log(f"  Инстр: {len(placements)}"))

        if not placements:
            self.after(0, lambda: self._log("⚠️ Нет вставок — копирую оригинал"))
            import shutil
            shutil.copy2(str(audio_path), output_path)
            self.after(0, lambda: self._finish(output_path))
            return

        from gachi_remix import deduplicate
        placements = deduplicate(placements)

        self.after(0, lambda: self._log(f"🎯 Итого: {len(placements)} вставок"))
        for pl in placements[:5]:
            self.after(0, lambda p=pl: self._log(f"  [{p.start:6.2f}s] {P(p.sound).name}"))
        if len(placements) > 5:
            self.after(0, lambda: self._log(f"  ... +{len(placements)-5}"))
        self.after(0, lambda: self.progress.configure(value=0.75))

        if dry_run:
            self.after(0, lambda: self._log("✅ Dry-run завершён"))
            self.after(0, lambda: self.progress.configure(value=1.0))
            self.after(0, lambda: self.out_label.configure(text="dry-run"))
            return

        self.after(0, lambda: self._set_status("Микширование через FFmpeg..."))
        self.after(0, lambda: self._log("🎛️  Микширую..."))
        from gachi_remix import mix
        mix_path = audio_path if not has_video else input_path
        mix(str(mix_path), placements, output_path, volume)
        self.after(0, lambda: self.progress.configure(value=1.0))
        self.after(0, lambda: self._set_status(""))
        self.after(0, lambda: self._finish(output_path))

    def _finish(self, path: str):
        self.output_path = path
        sz = os.path.getsize(path)
        name = os.path.basename(path)
        self.out_label.configure(text=f"{name} ({sz/1024/1024:.1f} MB)")
        self.play_btn.configure(state="normal")
        self.open_btn.configure(state="normal")
        self._log(f"✅ Готово: {path}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _play(self):
        if not self.output_path or not os.path.isfile(self.output_path):
            return
        if sys.platform == "win32":
            os.startfile(self.output_path)
        elif sys.platform == "darwin":
            subprocess.run(["open", self.output_path])
        else:
            subprocess.run(["xdg-open", self.output_path])

    def _open_folder(self):
        if not self.output_path:
            return
        folder = os.path.dirname(self.output_path)
        if sys.platform == "win32":
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.run(["open", folder])
        else:
            subprocess.run(["xdg-open", folder])


if __name__ == "__main__":
    app = GachiRemixGUI()
    app.mainloop()
