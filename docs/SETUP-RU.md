# DragShift + видео + увеличение фото: инструкция с нуля (Windows, PowerShell)

Каждая команда написана отдельно, под ней сказано, что должно появиться. Копируйте команды по одной.
Слова вида `PS>` в начало не копируйте. Это просто значок приглашения PowerShell.

## Что понадобится

| Что | Зачем | Обязательно |
|---|---|---|
| Windows 10/11, ~3 ГБ свободного места, интернет | всё | да |
| Python **3.12** | запуск и сборка (на нём работает CI проекта) | да |
| Git | скачать код с GitHub | да |
| `ffmpeg.exe` и `ffprobe.exe` | видео-инструменты | для видео |
| Real-ESRGAN (`realesrgan-ncnn-vulkan.exe` + модели) и видеокарта с драйвером Vulkan | AI-увеличение фото | нет: без него работает режим Fast |
| Inno Setup 6 | только если нужен установщик `DragShift-Setup.exe` | нет |

---

## Часть 1. Подготовка

### Шаг 1. Установите Python 3.12

Скачайте «Windows installer (64-bit)» версии 3.12.x с python.org/downloads. В первом окне установщика **поставьте галочку «Add python.exe to PATH»**, затем «Install Now».

Откройте новое окно PowerShell (меню «Пуск» → введите `PowerShell`).

```powershell
python --version
```
Должно появиться: `Python 3.12.x`. Если пишет «python не найден»: переустановите Python и не забудьте галочку PATH.

### Шаг 2. Установите Git

Скачайте и установите Git for Windows с git-scm.com/download/win (везде «Next»). Закройте и снова откройте PowerShell.

```powershell
git --version
```
Должно появиться: `git version 2.xx...`.

### Шаг 3. Скачайте код DragShift с GitHub

```powershell
cd $HOME\Documents
```
Ничего не выводится, но путь в приглашении сменится на `...\Documents`.

```powershell
git clone https://github.com/llamaboi55/DragShift.git
```
Появится `Cloning into 'DragShift'...` и в конце `done.`. В Документах возникнет папка `DragShift`. Если вы работаете со своим форком, возьмите его адрес.

```powershell
cd DragShift
```
Путь сменится на `...\Documents\DragShift`.

```powershell
dir
```
В списке должны быть: `.github`, `dragshift`, `installer`, `tests`, `DragShift.spec`, `requirements.txt`, `run.py`, `README.md` и др. Важно: **никаких** `_internal`, `dist`, `build`. Их быть не должно, это чистая копия.

### Шаг 4. Создайте виртуальное окружение (отдельную «коробку» для библиотек проекта)

```powershell
python -m venv .venv
```
Секунд 10 ничего не видно, потом появится папка `.venv`.

```powershell
.\.venv\Scripts\Activate.ps1
```
Приглашение начнётся со слова `(.venv)`. Так надо, это значит, что окружение включено. Его нужно включать в каждом новом окне PowerShell этой же командой.

Если вместо этого красная ошибка про «выполнение сценариев отключено» (running scripts is disabled):

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```
Ответьте `Y` и снова выполните команду активации.

```powershell
python -m pip install --upgrade pip
```
В конце: `Successfully installed pip-...` или `Requirement already satisfied`.

---

## Часть 2. Положите файлы, которые я подготовил

### Шаг 5. Распакуйте мой архив прямо в папку проекта

Скачайте `DragShift-video-upscale.zip` в «Загрузки», затем:

```powershell
Expand-Archive -Path "$HOME\Downloads\DragShift-video-upscale.zip" -DestinationPath . -Force
```
Ничего не выводится. Файлы встанут на свои места, а совпадающие заменятся (так и надо).

```powershell
git status --short
```
Должно быть примерно так:

```
 M .gitignore
 M DragShift.spec
 M dragshift/app.py
 M dragshift/util.py
?? bin/
?? docs/SETUP-RU.md
?? dragshift/upscale.py
?? dragshift/upscale_tools.py
?? dragshift/video.py
?? dragshift/video_tools.py
?? requirements-dev.txt
?? tests/test_app_wiring.py
?? tests/test_upscale.py
?? tests/test_video.py
```
`M` значит «заменён», `??` значит «новый». Файл `requirements.txt` в списке быть не должен: он уже совпадает с вашим.

```powershell
git diff dragshift/util.py
```
Проверка, что в `util.py` изменились ровно три строки: вы увидите три строки с плюсом (`+    from .video import VIDEO_EXTS`, `+    if ext in VIDEO_EXTS:`, `+        return "video"`). Если изменений больше, значит, на GitHub `util.py` с тех пор обновился: выполните `git checkout dragshift/util.py` и добавьте эти три строки вручную в конец функции `category()`, перед `return None`.

### Шаг 6. Установите библиотеки

```powershell
pip install -r requirements-dev.txt
```
Идёт 2–5 минут, много строк. В конце: `Successfully installed ... PySide6-6.x ... Pillow ... pytest ... pyinstaller ...`. Этот файл ставит всё нужное для запуска (`requirements.txt`) плюс `pytest` и `pyinstaller`.

---

## Часть 3. Скачайте то, что не лежит в репозитории

### Шаг 7. FFmpeg (для видео)

```powershell
$ProgressPreference = "SilentlyContinue"
```
Ускоряет загрузки в PowerShell; ничего не выводит.

```powershell
Invoke-WebRequest -Uri "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile "$env:TEMP\ffmpeg.zip"
```
Скачивается ~100 МБ. Если ссылка не открылась, зайдите на gyan.dev/ffmpeg/builds и возьмите `ffmpeg-release-essentials.zip` вручную.

```powershell
Expand-Archive -Path "$env:TEMP\ffmpeg.zip" -DestinationPath "$env:TEMP\ffmpeg" -Force
```
Ничего не выводится.

```powershell
Get-ChildItem "$env:TEMP\ffmpeg" -Recurse -Include ffmpeg.exe,ffprobe.exe | Copy-Item -Destination bin
```
Ничего не выводится. Проверка:

```powershell
.\bin\ffmpeg.exe -version
```
Первая строка: `ffmpeg version ...`.

### Шаг 8. Real-ESRGAN (для AI-увеличения). Можно пропустить, тогда будет только режим Fast

```powershell
New-Item -ItemType Directory -Force -Path bin\realesrgan | Out-Null
```
Ничего не выводится; появится папка `bin\realesrgan`.

```powershell
Invoke-WebRequest -Uri "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip" -OutFile "$env:TEMP\realesrgan.zip"
```
Скачивается ~50–60 МБ.

```powershell
Expand-Archive -Path "$env:TEMP\realesrgan.zip" -DestinationPath "$env:TEMP\realesrgan" -Force
```
Ничего не выводится.

```powershell
$exe = Get-ChildItem "$env:TEMP\realesrgan" -Recurse -Filter realesrgan-ncnn-vulkan.exe | Select-Object -First 1
```
Ничего не выводится (команда находит папку с программой, где бы она ни лежала в архиве).

```powershell
Copy-Item -Path "$($exe.DirectoryName)\*" -Destination bin\realesrgan -Recurse -Force
```
Ничего не выводится.

```powershell
dir bin\realesrgan
```
Должны быть `realesrgan-ncnn-vulkan.exe`, несколько `.dll` и папка `models`.

Необязательно, чтобы сборка была меньше. Оставить только две нужные модели («Фото» и «Иллюстрации»):

```powershell
Get-ChildItem bin\realesrgan\models | Where-Object { $_.BaseName -notin "realesrgan-x4plus","realesrgan-x4plus-anime" } | Remove-Item
```
Ничего не выводится. После этого в `bin\realesrgan\models` останутся 4 файла: `realesrgan-x4plus.param/.bin` и `realesrgan-x4plus-anime.param/.bin`.

```powershell
.\bin\realesrgan\realesrgan-ncnn-vulkan.exe -h
```
Должен напечататься текст `Usage: realesrgan-ncnn-vulkan.exe -i infile -o outfile [options]...`. Если Windows пишет, что не найдена какая-то `.dll`, установите «Microsoft Visual C++ Redistributable (x64)» с сайта Microsoft.

---

## Часть 4. Проверка и запуск

### Шаг 9. Тесты

```powershell
python -m pytest -q
```
В конце строка вида `NN passed`. Видео-тесты запускаются, если ffmpeg найден (шаг 7). Тесты, которым нужна другая система, пропускаются сами (`skipped`).

### Шаг 10. Самотест приложения

```powershell
python run.py --selftest
```
Окна на секунду мелькнут, а в консоли появятся строки `font ... inked samples` и в конце `SELFTEST OK`.

```powershell
echo $LASTEXITCODE
```
Должно быть `0`.

### Шаг 11. Запуск

```powershell
python run.py
```
Консоль «зависнет» (так и надо: программа работает), а в системном трее (у часов, возможно за стрелкой `^`) появится значок DragShift.

Проверьте:
1. Начните перетаскивать фото `.jpg` и **нажмите Shift**: меню покажет лепестки. В кольце Tools будет **✦ Upscale**.
2. То же с видео `.mp4`: кольцо Convert (MP4, MOV, MKV, WEBM, GIF, MP3) и Tools (Trim, Compress, 720p, Mute, Rotate, Speed, Frame).
3. Ошибки и подробности пишутся в `%LOCALAPPDATA%\DragShift\log.txt`.

Остановить программу: правый клик по значку в трее → выход, или `Ctrl+C` в консоли.

Если после `python run.py` ничего не появилось, а в уголке мелькнуло «DragShift is already running»: программа уже работает в трее (вторая копия не запускается, а передаёт команду первой). Закройте её через значок в трее и запустите снова.

---

## Часть 5. Сборка готового .exe

### Шаг 12. Папка с программой (для установщика)

Если нужно, чтобы внутрь вошли FFmpeg и Real-ESRGAN, они уже лежат в `bin\` (шаги 7 и 8). Сборка подхватит их сама.

```powershell
pyinstaller --noconfirm --clean DragShift.spec
```
Идёт 2–5 минут, много строк. В конце: `Building COLLECT COLLECT-00.toc completed successfully.` Появятся папки `build\` и `dist\`.

```powershell
Test-Path dist\DragShift\DragShift.exe
```
Должно быть `True`.

Проверьте готовую программу тем же самотестом:

```powershell
$p = Start-Process dist\DragShift\DragShift.exe -ArgumentList "--selftest" -Wait -PassThru; $p.ExitCode
```
Должно быть `0`. У собранной программы нет консоли, поэтому её сообщения пишутся в `%LOCALAPPDATA%\DragShift\log.txt`: в конце файла будет строка `SELFTEST OK`. Запустить сам DragShift: двойной клик по `dist\DragShift\DragShift.exe`.

### Шаг 13. Другие варианты сборки (по желанию)

Один portable-файл вместо папки:

```powershell
$env:ONEFILE = "1"
```
```powershell
pyinstaller --noconfirm --clean DragShift.spec
```
Получится `dist\DragShift.exe`. Потом верните обычный режим: `Remove-Item Env:ONEFILE`.

С воспроизведением видео в окне Trim (кнопка Play; сборка станет заметно больше):

```powershell
$env:PLAYER = "1"
```
```powershell
pyinstaller --noconfirm --clean DragShift.spec
```
Потом: `Remove-Item Env:PLAYER`.

### Шаг 14. Установщик `DragShift-Setup.exe` (по желанию)

Установите Inno Setup 6 (jrsoftware.org/isdl.php). Сначала нужна сборка из шага 12, потом:

```powershell
$env:DS_VERSION = "1.1.0"
```
```powershell
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" installer\DragShift.iss
```
В конце: `Successful compile`, файл `dist\DragShift-Setup.exe`. Windows SmartScreen может предупредить, что программа не подписана: «Подробнее» → «Выполнить в любом случае».

**Важно:** готовую программу устанавливайте в отдельную папку, а не в папку проекта. Из-за этого в вашем прежнем архиве появились `_internal` и `unins000.*`.

---

## Часть 6. Сохранить в git (по желанию)

```powershell
git add -A
```
```powershell
git status --short
```
В списке не должно быть `_internal`, `dist`, `build`, `.venv`, `__pycache__`, `unins000.*`, `ffmpeg.exe`, `bin\realesrgan`. Их закрывает `.gitignore`.

```powershell
git commit -m "Add video tools and AI photo upscale"
```
Затем `git push`, если у вас есть права на этот репозиторий.

---

## Если что-то пошло не так

| Что видите | Что делать |
|---|---|
| «Can't convert .jpeg», пустые лепестки | `dragshift\app.py` не заменён: повторите шаг 5, затем `python -m pytest -q tests\test_app_wiring.py` |
| `FFmpeg not found` / `ffmpeg not found` | Нет `bin\ffmpeg.exe` и `bin\ffprobe.exe`: повторите шаг 7 |
| «The AI engine (realesrgan-ncnn-vulkan.exe) was not found» | Повторите шаг 8 или выберите в окне режим Fast |
| «models\realesrgan-x4plus... is missing» | Папка `models` не скопирована рядом с exe: повторите шаг 8 |
| «Vulkan isn't available» | Обновите драйвер видеокарты (NVIDIA/AMD/Intel). В виртуальной машине и удалённом рабочем столе Vulkan часто нет. Режим Fast работает без видеокарты |
| «ran out of video memory» | Закройте программы, нагружающие видеокарту, или выберите Fast, или меньшее фото |
| «error 0xC0000135» | Установите Microsoft Visual C++ Redistributable (x64) и держите все `.dll` рядом с `realesrgan-ncnn-vulkan.exe` |
| `ModuleNotFoundError` при запуске или после сборки | Окружение не включено (нет `(.venv)` в приглашении) или не ставились библиотеки: шаги 4 и 6, затем `pyinstaller --noconfirm --clean DragShift.spec` |
| Antivirus блокирует `realesrgan-ncnn-vulkan.exe` | Правый клик по exe → Свойства → «Разблокировать», или добавьте в исключения |

Обновить код позже: сначала сохраните свои правки (`git add -A` и `git commit`), затем `git pull`.
