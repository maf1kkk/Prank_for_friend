import os, sys, random, json, threading, time, ctypes, ctypes.wintypes, winreg
from pathlib import Path

if getattr(sys, 'frozen', False):
    ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

import keyboard, mouse, pystray
from PIL import Image, ImageDraw

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent.parent
SOUNDS_DIR = BASE_DIR / "sounds"
CONFIG_FILE = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    "app_name": "Audio Service",
    "startup_name": "AudioService",
    "hotkeys": ["space","ctrl","alt","shift","w","a","s","d","q","e","r","t","y","f","g","h","j","k","l","z","x","c","v","b","n","m","1","2","3","4","5","6","7","8","9","0"],
    "mouse_buttons": ["left","right","middle","x1","x2"],
    "volume": 100,
    "cooldown_ms": 300,
    "autostart": True,
    "tray_icon": True,
    "tray_tooltip": "Audio Service",
    "exit_hotkey": "ctrl+alt+shift+f12",
    "priority_keywords": [],
    "priority_weight": 3
}

winmm = ctypes.WinDLL("winmm")
winmm.mciSendStringW.argtypes = [ctypes.wintypes.LPCWSTR, ctypes.wintypes.LPWSTR, ctypes.wintypes.UINT, ctypes.wintypes.HANDLE]
winmm.mciSendStringW.restype = ctypes.wintypes.DWORD
alias_counter = 0
alias_lock = threading.Lock()

def play_sound(filepath):
    global alias_counter
    with alias_lock:
        alias_counter += 1
        alias = f"p{alias_counter % 99999}"
    def _play():
        try:
            winmm.mciSendStringW(f"close {alias}", None, 0, None)
            fp = str(Path(filepath).resolve())
            winmm.mciSendStringW(f'open "{fp}" alias {alias}', None, 0, None)
            winmm.mciSendStringW(f"play {alias} wait", None, 0, None)
            winmm.mciSendStringW(f"close {alias}", None, 0, None)
        except: pass
    threading.Thread(target=_play, daemon=True).start()

def sound_weight(path, config):
    name = Path(path).stem.lower()
    for kw in config.get("priority_keywords", []):
        if kw in name:
            return config.get("priority_weight", 3)
    return 1

def load_sounds():
    if not SOUNDS_DIR.exists():
        SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
    return [str(f) for f in SOUNDS_DIR.iterdir() if f.suffix.lower() in (".mp3", ".wav", ".ogg", ".flac", ".m4a")]

def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except: pass
    return dict(DEFAULT_CONFIG)

def setup_autostart(config):
    if not config.get("autostart"): return
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        exe = sys.executable if getattr(sys, "frozen", False) else str(Path(__file__).resolve())
        winreg.SetValueEx(key, config.get("startup_name", "AudioService"), 0, winreg.REG_SZ, f'"{exe}"')
        winreg.CloseKey(key)
    except: pass

def remove_autostart(config):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, config.get("startup_name", "AudioService"))
        winreg.CloseKey(key)
    except: pass

def create_tray_icon(app_name, on_exit, on_remove):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([16, 16, 48, 48], fill=(80, 140, 255, 220))
    draw.ellipse([24, 24, 40, 40], fill=(200, 220, 255, 180))
    menu = pystray.Menu(
        pystray.MenuItem("Sounds folder", lambda: os.startfile(str(SOUNDS_DIR))),
        pystray.MenuItem("Remove & Exit", on_remove),
        pystray.MenuItem("Exit", on_exit)
    )
    return pystray.Icon("prank", img, app_name, menu)

class PrankApp:
    def __init__(self):
        self.config = load_config()
        self.sounds = load_sounds()
        self.running = True
        self.last_play = 0.0
        if not self.sounds:
            print("No sounds found in:", SOUNDS_DIR)
        setup_autostart(self.config)
        self.register_hotkeys()
        self.register_mouse()

    def play_random(self):
        now = time.time()
        if (now - self.last_play) * 1000 < self.config.get("cooldown_ms", 300):
            return
        self.last_play = now
        if not self.sounds:
            self.sounds = load_sounds()
            if not self.sounds:
                return
        weights = [sound_weight(s, self.config) for s in self.sounds]
        s = random.choices(self.sounds, weights=weights, k=1)[0]
        play_sound(s)

    def register_hotkeys(self):
        for hk in self.config.get("hotkeys", []):
            try:
                keyboard.add_hotkey(hk, lambda: self.play_random())
            except: pass

    def register_mouse(self):
        btn_map = {"left": mouse.LEFT, "right": mouse.RIGHT, "middle": mouse.MIDDLE, "x1": mouse.X, "x2": mouse.X2}
        btns = [btn_map[b] for b in self.config.get("mouse_buttons", []) if b in btn_map]
        if btns:
            try:
                mouse.on_button(lambda: self.play_random(), buttons=tuple(btns), types=(mouse.DOWN,))
            except: pass

    def exit_clean(self, remove_from_startup=False, icon=None):
        if remove_from_startup:
            remove_autostart(self.config)
        self.running = False
        if icon: icon.stop()
        keyboard.unhook_all()
        mouse.unhook_all()

    def exit_app(self, icon=None):
        self.exit_clean(remove_from_startup=False, icon=icon)

    def remove_and_exit(self, icon=None):
        self.exit_clean(remove_from_startup=True, icon=icon)

    def run(self):
        try:
            keyboard.add_hotkey(self.config.get("exit_hotkey", "ctrl+alt+shift+f12"), self.remove_and_exit)
        except: pass
        if self.config.get("tray_icon", True):
            icon = create_tray_icon(self.config.get("tray_tooltip", "Prank"), self.exit_app, self.remove_and_exit)
            icon.run()
        else:
            while self.running:
                time.sleep(1)

if __name__ == "__main__":
    PrankApp().run()
