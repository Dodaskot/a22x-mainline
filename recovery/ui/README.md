# A22x native recovery UI

Нативний інтерфейс для recovery, що завантажується з окремого розділу `RECOVERY` та працює з RAM на mainline Linux. Це компонент recovery-образу; сам ELF-файл не є прошивкою.

Поточний код **v0.5-dev**: C-інтерфейс для Linux із файловим браузером, монтуванням томів, UTF-8 редактором та екранною клавіатурою EN/UK. Список томів, папки й текст прокручуються пальцем у пікселях з інерцією. Значки будуються геометричними примітивами та згладжуються у фізичній роздільності екрана. Додано підготовку offline userdata, компактний Linux backup, огляд userdata wipe/restore та явне USB MTP sharing одного змонтованого тому. Збережено metadata, symlink, copy-tree, діагностику, 60/90 Гц і чотири режими живлення.

Файлові дії виконує `filesystem.py`, діагностику — `management.py`, дисплей — `display.py`, USB sharing — `usb_files.py`, резервування й відновлення — окремий backend. Generic device restore старих raw/Android копій залишається заблокованим. Фізичний Linux wipe/restore допускається лише за точним кваліфікованим контрактом v2; локальні fixtures не означають, що на телефоні виконано руйнівний цикл.

## Запуск у RAM recovery

Supervisor має змонтувати `/proc`, `/sys`, `/dev`, підготувати приватний каталог планів та дочекатися framebuffer і пристроїв evdev. Приклад для fish:

```fish
mkdir -p /run/a22x-recovery/plans/ui
chmod 700 /run/a22x-recovery/plans/ui
/bin/recovery-ui \
    --device \
    --python /usr/bin/python3 \
    --backend /opt/a22x-recovery/backend/recoveryctl.py \
    --repository /mnt/backup/A22xBackups \
    --workdir /run/a22x-recovery/plans/ui \
    --storage-helper /opt/a22x-recovery/storage.py \
    --filesystem-helper /opt/a22x-recovery/filesystem.py \
    --management-helper /opt/a22x-recovery/management.py \
    --display-helper /opt/a22x-recovery/display.py \
    --usb-files-helper /opt/a22x-recovery/usb_files.py \
    --allow-power-actions \
    2>/run/a22x-recovery/ui.log
```

`--allow-power-actions` додають лише за наявності supervisor, який обробляє код виходу **200** як перезапуск установленого Linux, **201** як вимкнення, **202** як перезапуск recovery, **203** як Samsung Download. Перед дією supervisor повинен успішно завершити unmount та синхронізацію записів. UI сам не викликає `reboot`, `poweroff` чи shell. Будь-який інший exit code не є дозволом на автоматичне перезавантаження.

Усі backend-команди запускаються через `fork`/`execv` із окремими аргументами. Для керування SD інтерфейс викликає `python3 storage.py inventory`, `mount` або `umount`. Монтування відбувається тільки після натискання кнопки. Підтримуваний носій v0.1: **ext4 microSD у `/dev/mmcblk1p1`**, mountpoint `/mnt/backup`; USB mass storage та форматування SD не реалізовано. USB MTP sharing — окрема файлова служба, а userdata wipe — окремий перевірений Linux workflow. Helper надає JSON з `ok`, `action`, `mounted`, `mountpoint`, `filesystem`, `free_bytes`, `error`. Його код належить інтеграції recovery, а не цьому каталогу.

## Інтерфейс та підтвердження

- **Backup:** Linux boot + rootfs (`boot` + зайняті ext4-блоки `userdata`, `partclone-ext4` + zstd) та Recovery (`recovery`, raw). Android прибрано лише з вибору нової стандартної копії. Старі Android archives залишаються в Restore/Verify; файли архівів не видаляються. Перед запуском UI отримує новий план і показує його склад, розмір, місце збереження та блокери.
- **Files:** реальний перелік томів; доступні файлові системи показуються першими. Нове монтування — read-only. Увімкнення запису потребує окремого підтвердження. Список папки прокручується пальцем у пікселях; кнопка `...` відкриває дії над вибраним записом.
- **Restore:** перелік завершених копій, перевірка та попередній перегляд точних цільових розділів. Для Linux v2 додається **Review userdata wipe**; точні targets та трисекундне підтвердження обов’язкові. Старі raw/Android device plans залишаються заблокованими.
- **Verify:** backend читає та перевіряє реальну копію; успіх показується лише за `ok=true` та exit code 0.
- **Storage:** у v0.1 — ext4 microSD з явним Mount/Unmount. UI може показати до шести налаштованих repository; це не означає підтримку інших фізичних носіїв поточним helper.
- **System:** натискання панелі середовища на головному екрані відкриває модель/ідентифікатор із backend, фактичне ядро, чотири розділи стандартних профілів, розміри та стан mounted/offline.
- **Tools and diagnostics:** фактичний стан SSH, адреса/порт USB, кількість дозволених ключів, fingerprint сервера й ознака тимчасового host key. Export diagnostics створює архів журналів на вже змонтованому RW томі. Кнопки fsck немає без реалізованого backend-контракту.
- **Display:** у Tools відкриває поточну частоту панелі, доступні 60/90 Гц і повзунок яскравості 1–100%. Зміна яскравості застосовується після відпускання пальця, після чого UI окремо читає фактичний стан. Доступність яскравості перевіряється незалежно від підтримки перемикання частоти.
- **USB files:** у Tools — вибір уже змонтованого userdata або microSD, Read only / Read and write, двосекундний Start та Stop поточного сеансу.
- **Power:** Restart Linux, Reboot recovery, Samsung Download, Power off. Кожен варіант потребує окремого утримання кнопки; під час backend-операції всі недоступні.

Backup потребує утримання кнопки **2 секунди**, wipe/restore — **3 секунди**, power — **2 секунди**. Дія спрацьовує після відпускання. Вихід пальця за межі кнопки, другий контакт або втрата input-подій скасовують підтвердження. Volume Up/Down переміщують фокус, Enter натискає обраний елемент; для критичних кнопок утримання все одно обов'язкове. Фізична Power керує тільки гасінням і пробудженням екрана та не активує вибрану кнопку.

### Файлові дії

`Volume` відкриває mount/unmount та перемикання RO/RW. UI враховує `mountable`, `write_supported`, `read_only` і пояснення backend. Для зашифрованого Android, raw-образів або файлової системи без підтримки запису недоступні дії вимкнено. Виявлення fscrypt не означає наявність ключів чи декриптування.

- Відкриття каталогів, `Up`, прокручування пальцем, перечитування `Refresh`. Додаткові стрілки пересувають список на половину видимої області без прив'язки до рядків або сторінок.
- Папки позначено помаранчевим і значком папки, звичайні файли — світло-блакитним і значком документа, виконувані — зеленим і значком термінала, symlinks — фіолетовим і значком посилання. Спеціальні записи мають приглушений колір і знак блокування. Підпис типу присутній окремо. Ознака `Executable` походить лише з фактичних permission bits `mode & 0111` звичайного файла; натискання такого файла не запускає його.
- Перегляд UTF-8 тексту до **64 KiB**. Більший файл показується як обрізаний preview; збереження такого preview неможливе. Бінарний вміст відхиляє backend.
- Редагування та нові текстові файли: клавіатура EN/UK, цифри, символи, Shift, Enter, backspace, стрілки та встановлення курсора дотиком. Рух/видалення не розривають UTF-8 послідовності. Вихід зі зміненого редактора потребує збереження або явного discard.
- Новий каталог; перейменування; копіювання звичайного файла, зокрема на інший уже змонтований writable том. У copy-prompt кнопка `To:` перемикає доступні цільові томи. Шляхи відлічуються від кореня вибраного тому.
- **Permissions:** чотири вісімкові цифри, наприклад `0644` або `0755`; зміна лише вибраного запису. **Owner / group:** числові `UID:GID`, наприклад `1000:1000`. UI показує фактичні mode/UID/GID після операції. Дії доступні лише коли backend повернув `metadata_supported=true`; root тому, hard-linked файли, symlinks та непідтримувані файлові системи не отримують цих кнопок у активному стані.
- **Symlinks:** `...` виконує `stat` без переходу за посиланням і показує його текст. Окремий `Resolve link` повертає канонічний шлях усередині вибраного тому; лише після успіху стає доступним `Open resolved target`. Звичайні read/list не починають неявно слідувати за symlink.
- **Copy folder:** доступне лише за `inventory.capabilities.copy_directories=true`. Передає `copy-tree`, точний entry token, обидва volume tokens і новий шлях призначення; жоден наявний запис не замінюється. Обмеження підтримуваних типів, розміру й метаданих задає helper.
- Видалення звичайного файла або порожнього каталогу. Рекурсивного видалення немає.

RW mount, save, mkdir, rename, copy/copy-tree, chmod/chown та diagnostic export потребують **2 секунд** утримання; delete — **3 секунд**. Екран підтвердження містить конкретні volume/path, destination або нові mode/UID/GID. Довгі шляхи розбиті на сторінки; кнопка підтвердження активується на останній. Copy/rename не перезаписують існуюче ім'я. Save існуючого файла передає SHA-256 версії, яку відкрив редактор. Chmod/chown/copy-tree передають token запису, отриманий під час `stat`; зміна inode/метаданих між оглядом та записом повинна завершитися відмовою helper.

UI передає volume tokens та окремі argv до helper, без shell. Відредагований текст записується в приватний RAM-файл `0600`, передається через `--text-file` і видаляється після завершення helper. Вміст відповіді filesystem helper не копіюється у UI log. Helper сам перевіряє mount/volume/inode і залишається остаточним контролем дозволу на операцію.

Свайп починається після переміщення понад 12 логічних пікселів; навіть горизонтальний drag скасовує натискання на файл або `...`. Невелике тремтіння пальця залишається звичайним tap. Зсув обмежено початком і кінцем вмісту. Інерція продовжує рух після відпускання, якщо виміряна швидкість достатня; новий дотик до того самого вмісту зупиняє рух і поглинає tap. Рядки обрізаються рамкою списку й не перекривають заголовок або нижні кнопки. UI тримає до 64 записів і підвантажує перекривні вікна; після заміни вікна старе незавершене натискання скасовується. Під час підвантаження прокручування продовжується, файлові дії чекають завершення helper. Це не задає штучного обмеження папки до 64 записів.

### Частота, яскравість і фізична Power

UI викликає `python3 display.py status` і `python3 display.py set --hz 60|90` окремими argv. Після успішного `set` завжди йде окремий `status`: `current_hz` має відповідати вибору, `actual_hz` — бути в межах одного герца від нього, сервіс і режим повинні залишатися доступними. Інакше показується `DISPLAY_MODE_NOT_APPLIED`; автоматичного повторення зміни немає. Число `actual_hz` використовується для внутрішньої перевірки. Основний екран показує 60/90 Гц, роздільність 1080×2408 і тривалість вибору: поточний recovery boot, із поверненням до 60 Гц після нового завантаження recovery. Технічний timing доступний у діагностиці.

Helper за замовчуванням — `/opt/a22x-recovery/display.py`; його можна перевизначити `--display-helper FILE` або `A22X_RECOVERY_DISPLAY_HELPER`. Підтримуваний режим діє в поточному recovery boot. Постійний DRM fd тримає окремий сервіс; UI сам не відкриває DRM і не змінює ядро. Renderer зберігає fbdev. Під час drag запланований мінімальний інтервал перемальовування — 16 мс, у спокої — 70 мс; це не обіцянка фактичних 60 або 90 FPS.

Коротке натискання Power (до 1,5 секунди, дія після відпускання) гасить екран або явно запитує `wake`, якщо екран уже погашений чи попередній результат невідомий. Autorepeat та довге утримання не запускають дій UI. Початок натискання скасовує незавершене підтвердження, drag яскравості й інерцію. Дотики до погашеного екрана не активують елементи; контакт, що триває під час пробудження, поглинається до відпускання. SYN_REPORT від кнопкового input-вузла не може зняти цю заборону для сенсора.

Power і яскравість використовують окремий неблокувальний helper-процес з лімітом відповіді 16 KiB та дедлайном 5 секунд. У дочірньому процесі закриті всі успадковані fd від 3 і прибраний `A22X_RECOVERY_LOCK_FD`. Основна операція backup/restore, PTY та MTP продовжують працювати. UI робить один read-only запит статусу при старті, а далі — лише явні дії й окрему перевірку після них. Втрачена або прострочена відповідь не спричиняє повторення `toggle` чи `brightness`: автоматично дозволений тільки `status`.

Окремий backend guard відновлює видиму яскравість при загибелі display service. У цій версії відновлення самих елементів керування після такого збою потребує перезапуску UI з display service або нового recovery boot; UI не видає старий кешований статус за свіжу відповідь сервісу.

Повзунок має векторну доріжку та маркер, показує попередній вибір лише під час drag і після завершення — значення свіжого readback. Нульова яскравість недоступна через повзунок; для гасіння використовується Power. Поля `brightness_raw`, `brightness_requested_raw` та `brightness_actual_raw` походять із backend. Це кешовані показники ядра, а не оптичне вимірювання панелі. Цілі відсотки квантуються в апаратний діапазон 0–255; наприклад, 78% відповідає raw199, тому повернення до того самого відсотка не обіцяє відновити довільний початковий raw200.

Координати 60/90 Гц залишаються `{28,652,320,118}` та `{372,652,320,118}` у полотні 720×1600. Область повзунка — `{52,1010,616,108}`, доріжка від x76 до x644 на y1064. Нові тести: `make display-ui-integration`; реальний client/service/guard із файловими замінниками обладнання — `tools/test_display_backend_ui.py` у mapped-root user namespace на хості. Апаратні натискання та читання sysfs перевіряються окремим root-operated acceptance harness.

### Діагностика

`management.py --device status` або `--fixture ROOT status` повертає `ssh` і `logs`. UI показує лише статус, адресу, кількість ключів і fingerprint; приватні ключі та вміст authorized keys не запитуються. Після помилки refresh старий статус не видається за актуальний.

Для експорту UI заново читає filesystem inventory і пропонує вже змонтований RW том із `write_supported=true`; microSD має пріоритет, якщо доступна. Підтвердження запускає `management.py --device export --volume ID --expected-volume-token TOKEN --confirm`. Успішний результат має містити шлях архіву, розмір і SHA-256. Невдалий експорт не повторюється автоматично. UI не монтує том RW приховано і не видає локальний preview за працюючий SSH сервер.

Прогрес надходить із JSONL backend, а не обчислюється таймером UI. Без лічильника байтів показано очікування. Навігація, power та нові операції заблоковані до завершення поточного процесу; під час read-only підвантаження списку дозволено лише продовжити прокручування. UI не повторює невдалий запис автоматично. SIGINT/SIGTERM не обривають активну операцію через навігаційну дію.

## Залежності та апаратний контракт

Runtime UI: C11 + Linux UAPI + libc, без Android init, Binder, SurfaceFlinger, Wayland, X11, SDL або бібліотеки шрифтів. Python потрібен **backend**, а не renderer. Вбудований атлас шрифтів містить ASCII, Latin U+00A0–U+017F, Cyrillic U+0400–U+04FF, пунктуацію та стрілки. Українські назви відображаються без runtime font engine. Непідтримуваний glyph показується як replacement symbol; оригінальні UTF-8 байти файла/імені не підміняються.

Renderer використовує `/dev/fb0`, `FBIOGET_FSCREENINFO`/`FBIOGET_VSCREENINFO`, RGB bitfields, реальний stride та offsets. Підтримуються PACKED_PIXELS/TRUECOLOR 16/24/32 bpp, little-endian. Полотно 720×1600 масштабується зі збереженням пропорцій. Прямого DRM/KMS backend немає: потрібна framebuffer emulation ядра.

Evdev сканується при старті, без фіксованого `eventN`. Type-B multitouch використовує реальні діапазони `EVIOCGABS`, tracking IDs та slots; є fallback для ABS_X/Y + BTN_TOUCH. Відомий kernel #107 має вбудовані DRM fbdev emulation, evdev і Himax HX83112F. Recovery init має включити `Himax_firmware_fw_jdi.bin` та дочекатися input nodes. Hotplug/rescan input після запуску поки відсутній. Для дисплея 1080×2408 логічне полотно займає 1080×2400 із відступами по 4 пікселі.

Backend stdout обмежений 1 MiB; JSON parser має межі глибини та кількості tokens. UI звіряє повернутий шлях плану, відкидає некоректний token та виконує лише план, допущений backend. Backend залишається відповідальним за ідентичність пристрою, mounted-state, вільне місце, хеші, сумісність Android та повторну перевірку перед I/O.

## Збирання й перевірка

```fish
make
make test
make integration
make files-integration
make v03-integration
make interaction-integration
make preview
make static
make arm64
```

`make arm64` щоразу створює **a22x-recovery-ui-aarch64** через `aarch64-linux-gnu-gcc -static` та `aarch64-linux-gnu-strip`. `CROSS_COMPILE` можна перевизначити. Host ELF та ARM64 ELF мають різні імена, щоб збірка не використала випадково x86-файл. На host потрібні Python 3 і Pillow лише для PNG previews; runtime UI їх не використовує для малювання.

`make test` виконує 92 перевірки parser/input/confirmation, UTF-8, прав томів, power intents, Linux-профілів, metadata input, native framebuffer projection, swipe/clipping/cache, типів файлів і доступності режимів дисплея, не відкриваючи пристрої. `make integration` запускає справжній backend на синтетичних файлах, змінює їх після backup, відновлює через UI confirmation flow та звіряє SHA-256. Також перевіряє відмову на зіпсованому архіві. Звіт — `test-results.json`.

`make files-integration` перевіряє реальний цикл RO → RW → mkdir → new text → edit → copy на інший том → rename → delete → RO → unmount. Додатково перевіряє stale-hash refusal та обрізання великого UTF-8 файла без дозволу на edit. Українські назви, shell-метасимволи в імені, коротке утримання, вихід пальця за межі кнопки, помилка existing folder, пагінація, незмінність інших файлів та очищення RAM text source входять до цих тестів. Звіт — `filesystem-test-results.json`.

`make v03-integration` перевіряє реальні chmod/chown, відмову зі старим entry token, блокування metadata для hardlinks, явний resolve/open symlink і відмову на виході з кореня тому, копіювання дерева і stale-token refusal, SSH status, справжній diagnostic `.tar.gz`, відмову зі старим volume token та недоступність export без RW тому. Архів додатково читається й перевіряється, ключові файли не повинні потрапляти до нього. Звіт — `v03-test-results.json`. Ці тести не запускають SSH сервер і не відкривають фізичні block/input/framebuffer пристрої.

`make interaction-integration` перевіряє 200 справжніх fixture файлів через 48 drag-жестів, tap із невеликим тремтінням, доступ до останнього файла, межі зсуву та відсутність зайвих read/stat/write під час свайпів. Повторено базовий файловий roundtrip після зміни навігації. Окремий UI display stub перевіряє 60→90→60, unavailable mode/service, хибний успішний `set` і помилку зміни без автоматичного повторення. Звіт — `v04-interaction-results.json`; ці сценарії не заявляють фізичне перемикання дисплея.

Окремий приклад рендеру без framebuffer:

```fish
./a22x-recovery-ui --preview device-restore --render previews/device-restore.ppm
./a22x-recovery-ui --preview file-editor --render-native previews/file-editor-native.ppm
python3 tools/ppm_to_png.py previews
```

`--preview` — ілюстративні стани з позначкою PREVIEW. `--render-native` застосовує точне масштабування для 1080×2408; `--render` зберігає 720×1600. Знімки `*-inventory`, `*-backup-result`, `*-restore-result`, `*-damaged-result`, `files-roundtrip`, `files-stale-write`, `files-large-read` генеруються реальними файловими тестами з позначкою FILE TEST. Жоден із цих PNG не є фотографією або screenshot телефона. Усі headless workflows відхиляються в `--device` режимі.

## Додано у v0.5

Окремі контракти нових інструментів: [локальний термінал](TERMINAL_UI.md),
[перемикання Linux-систем](SYSTEMS_UI.md) і
[встановлення BOOT / стокового Android](INSTALL_UI.md). **Tools** має піксельне
прокручування з інерцією; **Install images** розташовано нижче **Linux systems**.
У початковій позиції координати Display, USB files, Terminal і Linux systems
збережено. Їхні runtime helpers і перевірки є окремими від renderer.

### Інерційне прокручування і векторні значки

Піксельні offsets незалежні для томів, папки та тексту. Для папок збережено кеш до 64 записів із перекривним підвантаженням; кількість записів у папці не обмежено кешем. Інвентар томів обмежений 80 записами. Текстовий layout містить bounded індекс рядків навіть для 64 KiB самих newline. Курсор показується в області перегляду лише після typing/навігації; scroll не повертає екран автоматично до старого курсора.

Швидкість оцінюється з останніх 100 мс timestamped руху; дуже повільний жест або довга зупинка пальця перед release не запускають інерцію. Deceleration залежить від monotonic elapsed time, а не кількості input packets. Frame pacing: 16 мс для 60 Гц і 11 мс для підтверджених 90 Гц. Це цільові інтервали, не виміряний FPS. `--profile-render` або `A22X_RECOVERY_PROFILE_RENDER=1` додає кількість кадрів, сумарну/максимальну вартість render та час інерції до приватного UI log; окремий caret receipt не містить тексту файла.

Після cache refill старі hit rectangles недійсні до redraw. Після async refresh/remount підняти й знову опустити палець обов’язково: той самий утриманий контакт не може натиснути новий запис. Multitouch і SYN_DROPPED зберігають цей захист. Дотик до клавіатури поза текстом зупиняє рух і виконує саме обрану клавішу; звичайний stop-tap у тексті не пересуває курсор.

`native_canvas.h` малює лінії, кільця та прямокутники безпосередньо у фізичний framebuffer зі згладжуванням за відстанню до краю. Значки й стрілки не є збільшеними low-resolution bitmap. Нових runtime бібліотек не потрібно. Текст використовує вбудовані гліфи Adwaita Sans і Noto Sans, термінал — JetBrains Mono. Новий RGB frame buffer займає близько 10 MiB при 1080×2408.

### Linux backup → wipe → restore

**Backup → Linux → Review backup** перечитує inventory userdata. Якщо том змонтований і належить Files helper, екран **Offline filesystem** пропонує утримати кнопку одну секунду, виконати звичайний unmount, повторно перевірити стан і побудувати новий план. Force/lazy unmount не застосовуються. Чужий mount або busy том не знімається. Незбережений редактор блокує цей перехід без втрати buffer. SD лишається змонтованою, userdata — offline після операції.

Compact backup планує `--profile linux --format partclone-ext4`. План показує зайняті ext4-блоки, реально виміряний стиснений архів, вільне місце й резерв. Недостатньо місця — заблокована кнопка, без автоматичного видалення файлів SD. Підтвердження backup — дві секунди.

**Restore → saved Linux v2 snapshot** містить Verify, Review userdata wipe та Review restore. Wipe створює порожню ext4 лише в userdata; BOOT і RECOVERY зберігаються, це не secure erasure. Restore відновлює й перевіряє userdata, потім за потреби BOOT; ідентичний BOOT пропускається. Кожен destructive plan потребує окремого трисекундного hold; автоматичного reboot немає.

UI перевіряє `schema=a22x.plan.v2`, `workflow=linux-partclone-v2`, `backup_schema=a22x.backup.v2`, `data_format=partclone-ext4-v2+zstd`, profile/destructive/hold/operation, `can_execute`, token і повернутий plan path. У device mode додатково потрібні поточні inventory capabilities, `qualification.status=qualified`, `eligible=true`, SHA-shaped capsule ID та порожні blockers. Exact write targets мають збігатися з inventory path/size: wipe — userdata only; restore — userdata і, лише коли потрібно, BOOT last. Legacy raw device writer цим не відкривається. Backend повторює сильніші перевірки й є остаточним gate. Повний контракт — `../backend/LINUX_WORKFLOW.md`.

### USB файли з комп’ютера

**Tools → USB files** читає живий status і candidates від `usb_files.py`. У списку тільки вже змонтовані допустимі userdata/SD. Новий вхід за замовчуванням обирає read-only; RW можливий лише для фактично writable mount. Start передає ID, volume token, mount token і, за потреби, `--read-only`; Stop передає точний session token. Після кожного успішного start/stop UI робить незалежний status-read і звіряє ID/tokens/access або завершення сеансу. Pending/failed/unavailable не підміняються написом про активний export.

Нові MTP файли належать root:root, типові modes — 0644/0755. MTP не зберігає повну Linux metadata семантику; якщо PC client видаляє і створює файл заново, metadata старого inode не зберігаються. Для повних POSIX операцій залишаються USB SSH/SFTP/термінал. Незбережені зміни UI editor блокують Start. Активний export має storage lease, тому його треба явно Stop перед mount, локальним редагуванням, backup/wipe/restore. Вихід з USB screen не зупиняє sharing потайки.

Production CLI: `usb_files.py --mode device status|start|stop`. Шлях задається `--usb-files-helper` або `A22X_RECOVERY_USB_FILES_HELPER`. UI contract tests використовують чітко позначений file-backed stub, бо cloud test container забороняє AF_UNIX; це не тест справжнього MTP або USB gadget. Реальний service IPC/lease та phone acceptance мають власні receipts інтеграції.

### Цільова перевірка v0.5

`make motion-integration`: 200-entry folder flings через cache boundaries на 11/16 мс, 80 томів, довгий UTF-8 viewer/editor, stop-tap, keyboard interrupt і real guarded save. `make linux-integration`: справжній native Partclone backup→wipe→restore на owned regular ext4 image та stale unmount refusal. Native tools мають бути явно доступні в PATH; жодний loop mount або block device не відкривається. `make usb-ui-integration`: RO/RW, stale tokens, unavailable і непідтверджений status у contract stub.

Нова перевірка й scope наведені у `VALIDATION-v05.md`, машинні receipts — `v05-validation.json`, `v05-sanitizer-results.json`, `v05-interaction-results.json`, `v05-linux-results.json`, `v05-usb-ui-results.json`. Старі v03/v04 receipts є історичними. Mutable fixtures v05 створюються поза source workspace, рекомендовано `env A22X_UI_FIXTURE_BASE=/dev/shm ...`. Фізичні input/DRM/MTP результати додає root окремо.

## Межі поточної реалізації

Android state не є повною прошивкою: `super`/system/vendor та сумісний firmware мають залишитися на пристрої. Копії не доводять можливість відновлення Android після зміни TEE, encryption policy або firmware. UI не виконує FBE-декриптування, довільного форматування розділів, встановлення ZIP, модифікації LK, BROM-операцій чи зміни таблиці розділів.

Реальний SSH через USB, виходи supervisor у Recovery/Download, filesystem операції, diagnostic export, touch-свайпи й режими дисплея на телефоні мають окремі receipts інтегрованого образу. Цей каталог не підміняє їх локальними перевірками. Відновлення фізичних розділів не можна вважати реалізованим через успішні тести звичайних файлів. File editor не є hex editor, не встановлює ZIP і не змінює raw-розділи.

Код UI має MIT license. Вбудовані гліфи шрифтів Adwaita Sans, Noto Sans, JetBrains Mono й Adwaita Mono мають окремі умови у `LICENSE.fonts`; цей файл потрібно поширювати разом із програмою.

## Рендер і мови

UI малює прямо в роздільності панелі 1080×2400. Розмітка лишається на сітці 720×1600, одна одиниця дорівнює 1,5 пікселя. Фігури отримують згладжування від точної відстані до краю (`native_canvas.h`), а шрифти заздалегідь растеризуються в атласи (`tools/generate_native_fonts.py`), тому runtime не потребує шрифтового рушія.

Текст шукається за англійським написом під час малювання, тож перекладаються й повідомлення помічників. Каталоги лежать в `i18n/*.po`:

```fish
python3 tools/extract_i18n.py   # оновити i18n/messages.txt після зміни тексту
python3 tools/update_po.py      # додати нові рядки в усі каталоги
python3 tools/generate_i18n.py  # зібрати i18n_data.h
```

Щоб додати мову, створи `i18n/КОД.po` і додай код та назву в `ORDER` і `NAMES` у `tools/generate_i18n.py`. Перекладений рядок має містити ті самі `%`-перетворення в тому самому порядку, інакше збірка зупиниться. Рядок без перекладу показується англійською. Шрифти покривають латиницю та кирилицю; писемності зі складним шейпінгом або CJK потребують нових атласів.

Українська перекладена повністю. Німецька, іспанська, французька та польська містять основні написи, довгі пояснення там поки англійською, і переклад не перевіряли носії мови.

## Теми

Кольори всіх тем лежать у `theme.h`. Тони Daylight та Aurora пораховані в просторі OKLCH (`tools/oklch.py`), а `tools/check_themes.py` зупиняє збірку тем, якщо контраст тексту недостатній. Фони й м'яку тінь карток рендерить LVGL під час збірки:

```fish
git clone --depth 1 --branch release/v9.4 https://github.com/lvgl/lvgl.git
# зібрати tools/theme_assets.c разом із LVGL і перенаправити вивід у theme_assets.h
```

Готовий `theme_assets.h` містить лише таблиці даних, тому в образі recovery немає графічної бібліотеки. Скляні поверхні напівпрозорі: плавний фон за ними виглядає як розмитий, тому розмиття не потрібне під час роботи. У списках, що прокручуються, тіней немає, щоб не втрачати кадри.
