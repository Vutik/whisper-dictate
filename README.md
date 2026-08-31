# whisper-dictate

Диктовка текста в любое окно — локально, офлайн, на GPU. Аналог superwhisper
для Linux.

Нажали хоткей → говорите → нажали снова → текст появился там, где стоял курсор.
Ничего не уходит в сеть.

---

## Установка

### Одной командой

```bash
curl -fsSL https://raw.githubusercontent.com/USER/whisper-dictate/main/install.sh | bash
```

> Подставьте свой адрес репозитория. Для установки «из веба» скрипту нужно
> знать, откуда брать исходники: `WHISPER_DICTATE_REPO=https://…/whisper-dictate.git`.

### Из каталога с исходниками

```bash
git clone https://github.com/USER/whisper-dictate.git
cd whisper-dictate
./install.sh
```

### Параметры установщика

| Флаг | По умолчанию | Что делает |
|---|---|---|
| `--hotkey <spec>` | `ctrl+alt+space` | комбинация для переключения записи |
| `--model <name>` | `large-v3-turbo` | какую модель скачать |
| `--no-model` | — | не качать модель (скачается при первом запуске) |
| `--dir <path>` | `~/.local/share/whisper-dictate` | куда ставить при установке из веба |

Скрипт **идемпотентный** — повторный запуск обновляет и чинит существующую
установку, ничего не ломая.

### Что он делает

1. Определяет тип сессии (X11 или Wayland) и ставит нужные системные пакеты
2. Проверяет наличие NVIDIA GPU, предупреждает если его нет
3. Создаёт виртуальное окружение и ставит зависимости (~2.5 ГБ CUDA-колёс)
4. Скачивает модель (~1.6 ГБ в `~/.cache/huggingface`)
5. Прописывает systemd-сервис пользователя и запускает его
6. Проверяет, что демон отвечает, и показывает выбранные бэкенды

### Требования

| | |
|---|---|
| ОС | Linux, X11 или Wayland |
| Python | 3.9+ с модулем `venv` |
| GPU | NVIDIA — желательно; без него работает на CPU, но медленно |
| Диск | ~4.5 ГБ (окружение + модель) |
| Автозапуск | сервис `systemd --user`, стартует при входе в сессию |

---

## Как пользоваться

1. Поставьте курсор в любое поле ввода
2. **Ctrl+Alt+Space** — пойдёт запись (звук + уведомление «🎤 Запись…»)
3. Говорите, можно мешать русский с английским
4. **Ctrl+Alt+Space** — через ~1 с текст вставится в это поле

```bash
./dictate                  # то же, что хоткей
./dictate start|stop|cancel
./dictate status           # idle / recording / busy + состояние модели
./dictate backends         # какие реализации выбраны
```

---

## Настройки

Меняются на лету — демон следит за конфигом и перечитывает его за две секунды,
перезапуск не нужен даже при смене модели или хоткея.

```bash
./dictate get                    # весь конфиг
./dictate get model              # одно значение
./dictate set model small        # изменить и применить
./dictate set backends.inject x11
./dictate edit                   # открыть в $EDITOR
./dictate reload                 # перечитать принудительно
```

Файл: `~/.config/whisper-dictate/config.json`.

### Распознавание

| Ключ | По умолчанию | Комментарий |
|---|---|---|
| `model` | `large-v3-turbo` | `large-v3` точнее и вдвое медленнее; `medium`, `small`, `base`, `tiny` — легче и хуже |
| `device` | `cuda` | `cpu` — работает, но ~10 с на фразу вместо 0.6 с |
| `compute_type` | `int8_float16` | 1.1 ГБ VRAM. `float16` — 2.2 ГБ без выигрыша в скорости |
| `language` | `null` | автоопределение (ru/en вперемешку). Жёстко: `"ru"` |
| `beam_size` | `5` | больше — точнее и медленнее |
| `vad_filter` | `true` | отсекать тишину до распознавания |
| `initial_prompt` | `null` | произвольная подсказка декодеру |
| `no_speech_threshold` | `0.9` | отбрасывать сегменты с такой вероятностью тишины |

### Память

| Ключ | По умолчанию | Комментарий |
|---|---|---|
| `idle_unload_seconds` | `30` | простой до освобождения VRAM. Возврат 0.15 с, спрятан за началом записи. `0` — не выгружать |
| `deep_unload_seconds` | `900` | простой до выброса весов из RAM. Возврат 0.75 с. `0` — не выбрасывать |

Расход: 1.1 ГБ VRAM во время работы, **110 МиБ** через 30 с простоя,
RSS ~690 МБ через 15 минут.

### Запись

| Ключ | По умолчанию | Комментарий |
|---|---|---|
| `samplerate` | `16000` | Whisper всё равно работает на 16 кГц |
| `input_device` | `null` | микрофон по умолчанию в системе; можно указать имя или индекс |
| `max_seconds` | `300` | автостоп записи |
| `min_seconds` | `0.35` | короче — считается случайным нажатием |

### Хоткей

| Ключ | По умолчанию | Комментарий |
|---|---|---|
| `hotkey` | `ctrl+alt+space` | модификаторы `ctrl`, `alt`, `shift`, `super` |
| `hotkey_grab` | `true` | `false` — только через `./dictate` |

`dictate set hotkey` сверяется с горячими клавишами рабочего стола и
отказывается ставить занятую комбинацию (перекрыть — флагом `--force`).
Проверка нужна не зря: X11-захват занятой комбинации проходит **успешно**,
но GNOME обрабатывает её параллельно — Ctrl+Alt+D одновременно диктовал бы
и сворачивал все окна.

### Вставка текста

| Ключ | По умолчанию | Комментарий |
|---|---|---|
| `insert_method` | `paste` | буфер + Ctrl+V. `type` — эмуляция набора, `clipboard` — только скопировать |
| `type_delay_ms` | `4` | задержка между клавишами для `type` |
| `append_space` | `true` | пробел в конце, чтобы диктовать подряд |
| `restore_clipboard` | `true` | вернуть прежнее содержимое буфера через 0.6 с |
| `terminal_classes` | 12 значений | в этих окнах вставка идёт через Ctrl+**Shift**+V |

`paste` стоит по умолчанию не случайно: при активной русской раскладке
эмуляция набора кириллицы на X11 выдаёт мусор, а буфер обмена от раскладки
не зависит. Именно на этом, судя по всему, ломаются готовые аналоги.

### Английские термины в русской речи

Whisper транслитерирует технические слова — «мохоз» вместо «macOS». Лечится
двумя механизмами:

| Ключ | Что делает |
|---|---|
| `vocabulary` | 53 правильных написания; уходят в декодер как `initial_prompt` и `hotwords` — предотвращает проблему |
| `use_hotwords` | передавать ли словарь как `hotwords` |
| `replacements` | 51 правило замены по целым словам, регистронезависимо — исправляет проскочившее |
| `hallucination_phrases` | фразы, которые Whisper выдумывает над тишиной («продолжение следует») — отбрасываются, если составляют весь результат |

```
Открой сеттингс и нажми аксепт    →  Открой settings и нажми accept
Отправь пул реквест в гитхаб      →  Отправь pull request в GitHub
Работает под линукс и виндовс     →  Работает под Linux и Windows
```

Пополнять: `./dictate set replacements '{"деплой":"deploy", ...}'` или через
`./dictate edit`.

### Уведомления и звук

| Ключ | По умолчанию |
|---|---|
| `notifications` | `true` |
| `sounds` | `true` |
| `sound_start` / `sound_done` / `sound_error` | звуки из `/usr/share/sounds/freedesktop/stereo/` |

### Выбор реализаций

| Ключ | По умолчанию |
|---|---|
| `backends.stt` · `audio` · `inject` · `hotkey` · `notify` · `sound` · `postprocess` | `auto` |

`auto` означает: при старте опрашивается каждая зарегистрированная реализация,
берётся работоспособная с наибольшим приоритетом. Ничего настраивать руками
не нужно — при переходе с X11 на Wayland вставка сама переключится. Закрепить
жёстко: `./dictate set backends.inject x11`.

---

## Поддержка платформ

```bash
./dictate backends                                  # что выбрано сейчас
.venv/bin/python dictate_daemon.py --list-backends  # что вообще есть
```

| | X11 | Wayland (GNOME) | macOS |
|---|---|---|---|
| Распознавание | ✅ CUDA | ✅ CUDA | ⚠️ только CPU |
| Микрофон | ✅ | ✅ | ✅ |
| Вставка текста | ✅ `xdotool` + `xclip` | ⚠️ `wl-copy` + `ydotool` | ❌ нужен бэкенд |
| Хоткей | ✅ `XGrabKey` | ⚠️ `evdev` | ❌ нужен бэкенд |
| Уведомления | ✅ DBus | ✅ DBus | ❌ нужен бэкенд |

### Что нужно для Wayland

Wayland принципиально не даёт приложению ни перехватить клавишу, ни
синтезировать нажатие. Обходится через ядро:

```bash
sudo usermod -aG input $USER     # чтение /dev/input для хоткея; нужен релогин
sudo apt install ydotool          # синтез Ctrl+V через /dev/uinput
```

Оговорки, о которых честно стоит знать:

- `evdev` не **поглощает** клавишу — приложение под курсором тоже её получит.
  Выбирайте комбинацию, которую программы игнорируют.
- Группа `input` даёт процессу доступ ко всему потоку клавиатуры. Это ослабление
  изоляции; решайте осознанно.
- `wtype` на GNOME бесполезен: Mutter не реализует `zwp_virtual_keyboard_v1`.
- Без `ydotool` всё равно работает — текст просто копируется в буфер, вставляете
  вы сами.
- Портал `GlobalShortcuts` (чистое решение) требует xdg-desktop-portal ≥ 1.17;
  в Ubuntu 22.04 версия 1.14.

---

## Архитектура

Всё системно-зависимое спрятано за интерфейсами, ядро не импортирует ни X11,
ни CUDA, ни DBus. Реализации сами регистрируются в реестре.

```
whisper_dictate/
  interfaces.py   SpeechToText · AudioCapture · TextInjector · HotkeyBinder
                  Notifier · SoundPlayer · TextProcessor
  registry.py     @register(kind, name, priority) + выбор "auto"
  core.py         конечный автомат диктовки, платформо-независимый
  server.py       сборка зависимостей, сокет, горячая перезагрузка конфига
  backends/       stt_faster_whisper · audio_sounddevice
                  inject_x11 · inject_wayland
                  hotkey_x11 · hotkey_evdev
                  notify_dbus · notify_libnotify · sound_paplay · feedback_null
                  postprocess_rules
```

### Как добавить поддержку новой системы

Положить файл в `backends/`, ничего больше не трогая:

```python
@register("inject", "macos", priority=120)
class MacInjector(TextInjector):
    @classmethod
    def is_available(cls, cfg):
        return sys.platform == "darwin"

    def insert(self, text):
        subprocess.run(["pbcopy"], input=text.encode())
        subprocess.run(["osascript", "-e",
                        'tell app "System Events" to keystroke "v" using command down'])
```

Модуль подхватится автоматически, а `is_available()` не даст ему выиграть не
на своей платформе. Модуль, чьи зависимости отсутствуют, пропускается с
записью в лог, а не роняет демон.

---

## Диагностика

```bash
systemctl --user status whisper-dictate
journalctl --user -u whisper-dictate -f     # видно распознанный текст и тайминги
./dictate ping
```

| Симптом | Куда смотреть |
|---|---|
| Хоткей не срабатывает | в логе строка `hotkey: grabbed` |
| Текст распознался, но не вставился | `./dictate get insert_method`, проверить `xdotool`/`wl-copy` |
| «Ничего не распознано» | тихий или не тот микрофон: `./dictate set input_device "..."` |
| Долгий первый запуск | загрузка модели, ~2 с; дальше живёт в памяти |
| Занята видеопамять | `systemctl --user stop whisper-dictate` или `./dictate set idle_unload_seconds 10` |

---

## Удаление

```bash
./uninstall.sh                                  # сервис
rm -rf ~/.cache/huggingface/hub/*whisper*       # модели
rm -rf ~/.config/whisper-dictate                # настройки
```
