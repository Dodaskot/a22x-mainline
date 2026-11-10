# UI v0.5: реалізація та локальна перевірка

Це початковий checkpoint інерційної навігації, Linux-бекапу й USB UI. Пізніші
Terminal, Linux systems та Install images мають окремі контракти й результати
поруч із цим файлом. Остаточні 13 runtime-файлів зафіксовано у
`v05-ui-runtime-freeze.json`; цей історичний звіт зберігає контрольні суми саме
тієї ранньої збірки, на якій виконувалися наведені нижче перевірки.

Дата звіту: 2026-11-05. Runtime зафіксовано після останніх перевірок. Цей звіт описує локальний C UI, його виклики справжніх filesystem/Linux helpers та окремий USB contract stub. Він не містить результатів прошивання, фізичного wipe/restore, MTP на телефоні або виміряного FPS дисплея.

## Зафіксовані файли

| Файл | SHA-256 |
| --- | --- |
| `recovery_ui.c` | `3da3b49c0b70a2e989b1f32860295423faaed42f65daf86337730cafd1cb3365` |
| `native_vector.h` | `d5b75c668edfac3026530e93ec8d9cf5ef5134a9494740d57491177b6db710b4` |
| `kinetic_scroll.h` | `bca41f1c353c4000ce3bb596cbc2edf9f5a87ae3732a9864a2e4dc236f8507e9` |
| локальний `a22x-recovery-ui` | `4fe7a4a59c323b6b13511e6f54006620e1152c40329fe8274471e5a19d9e8f07` |
| локальний `a22x-recovery-ui-asan-v05` | `abc3876733f6128e7c8d7e01d17071d7e680ef07c7460dac33f449fe2fd95314` |

Перший binary — локальна збірка для fixture/preview, не ARM binary з recovery image. У збірку ARM потрібно включити обидва нові заголовки. Повний список контрольних сум UI runtime, тестів і зображень містить `v05-validation.json`.

## Що змінилося

### Рух списків і тексту

Files volumes, список каталогу та viewer/editor мають безперервний піксельний scroll. Після швидкого жесту рух триває за інерцією. Швидкість оцінюється за останніми 100 мс контактного руху, обмежена 3500 px/s; інерція починається від 180 px/s і сповільнюється на 4200 px/s². Обчислення використовують monotonic elapsed time, а не кількість подій input. Повільний жест або пауза пальця перед відпусканням не запускають fling.

Перший дотик по області, що рухається, зупиняє її та поглинається: він не відкриває файл, не вибирає інший том і не переставляє caret. Наступний окремий tap діє звичайно. Рух понад 12 логічних пікселів скасовує tap. Клавіатура або навігаційна кнопка поза content area можуть свідомо перервати інерцію й виконати свою дію. У editor drag зберігає позицію caret; тільки tap, курсорна навігація чи введення змінюють її.

Список каталогу використовує cache до 64 entries з перекриттям вікон по 32. Fling проходить межі cache без повернення до сторінок. Список томів обмежений 80 entries. Для тексту збережено ліміт 64 KiB; навіть текст, повністю складений із переносів рядка, має обмежений layout index. Частково видимі рядки, glyphs, кнопки й native icons обрізаються viewport.

Під час зміни inventory, mount state, screen або заміни cache стару hit map скидають. Якщо палець ще утримується, нові дії заблоковано до його повного відпускання: async completion не перетворює залишок старого контакту на новий tap. Збережено скасування підтвердження за другого контакту, втрати input sync або виходу за межі hold button.

Цільовий інтервал активної анімації — 16 мс у режимі 60 Гц, 11 мс у підтвердженому режимі 90 Гц. Це не виміряні 60/90 rendered FPS. `--profile-render` або `A22X_RECOVERY_PROFILE_RENDER=1` додає до журналу кількість представлених кадрів, elapsed time та сумарну/максимальну вартість render. Тести з синтетичним clock перевіряють рух, а не швидкість framebuffer на телефоні.

### Значки та типи

Значки, стрілки, back chevron і radio rings формуються лініями, кільцями та прямокутниками у `native_vector.h`. Після проєкції logical scene вони растеризуються в фізичній роздільності framebuffer із 4×4 coverage samples на фізичний піксель. Це окрема геометрична сцена; збільшення маленької bitmap для цих символів більше немає. Додаткової великої графічної бібліотеки немає.

Текст використовує існуючий font renderer. Типи файлів залишаються відмінними за кольором, символом і текстовою назвою: папка, звичайний файл, executable за фактичними mode bits, symlink, special/blocked. Томам додано зрозумілу відмінність storage category та стану mount/access; колір не є єдиним носієм інформації.

### Linux backup, wipe, restore

Home → Backup → Linux відкриває підготовку userdata. Для змонтованого тому, яким керує filesystem helper, користувач окремо утримує кнопку звичайного unmount одну секунду. Потім UI перечитує inventory й створює новий план. Немає forced/lazy unmount; чужий mount owner, busy mount, stale token або незбережений editor buffer зупиняють підготовку. SD залишається змонтованою. Після операції userdata залишається offline.

Linux backup обирає `--format partclone-ext4` явно. Review показує used ext4 blocks, оцінений/виміряний розмір архіву, вільне місце та резерв; insufficient-space plan не можна підтвердити. Hold backup — 2 секунди.

Restore → saved Linux v2 snapshot містить Verify, Review userdata wipe і Review restore. Wipe форматує тільки userdata в порожню ext4 та зберігає BOOT; це не secure erasure. Restore спочатку відновлює й перевіряє userdata, а BOOT — останнім, лише якщо він відрізняється. Для кожного destructive plan потрібно нове утримання 3 секунди. Автоматичного reboot немає.

UI самостійно перевіряє schema/workflow/data format/profile, точну operation, plan path, confirmation token, `can_execute`, blockers, hold duration і write targets. У device mode додатково потрібні поточні inventory capabilities та qualified/eligible capsule. Path/size кожного target мають відповідати inventory. Старий generic raw/Android device restore залишається заблокованим. `fixture-only` qualification не перетворюється на фізичний допуск. Backend перевіряє умови повторно; його qualification й readback є остаточним gate.

### USB files

Home → Tools → USB files показує лише кандидати, які повернув production helper: вже змонтовані userdata або зовнішній SD. За замовчуванням вибрано read only; read/write доступний лише для реально writable candidate. Hold Start — 2 секунди. Start передає незмінні volume і mount tokens. Stop передає точний session token.

Після Start/Stop UI окремо запитує status. Він не вважає sharing активним тільки через успішну відповідь Start; перевіряються фактичний state, stable volume ID, обидва tokens і access. Starting/stopping залишаються явними проміжними станами. Stale token та непідтверджений стан не замінюються припущенням про успіх.

Helper володіє storage lease на весь час export; sharing потрібно явно зупинити перед offline backup/wipe/restore або локальними файловими діями. Вихід з екрана USB не завершує сесію потайки. Незбережений editor buffer блокує Start. Нові MTP files мають root:root і типові modes 0644/0755; MTP не передає повну POSIX metadata семантику. Повні metadata операції залишаються в локальному file manager та SSH/SFTP.

## Результати перевірок

| Набір | Результат | Що перевірено |
| --- | --- | --- |
| C self-tests | 92 PASS | Input/hold/cancel, held-finger release latch, UTF-8/caret, cache hit map, clipping, velocity/bounds/stop tap, vector coverage, display admission, точний Linux v2 gate, dirty buffer guards |
| Motion + справжній filesystem helper | 4 PASS | 200 entries і cache crossings у двох напрямках за 11/16 мс ticks; 80 томів; viewer/editor fling; caret safe; keyboard interrupt; рівно один guarded UTF-8 save |
| Linux UI + справжні native tools | 4 PASS | Stale unmount refusal; compact backup; userdata mkfs; restore + незалежне reclone/readback на власному regular ext4 image 64 MiB |
| USB UI contract | 5 PASS | RO/RW Start/Stop; stale mount; stale session і recovery через status; unavailable; false-success Start rejected |
| ASan + UBSan | Усі наведені набори PASS | Фінальний runtime SHA, 92 self-tests + 13 workflows; leak detection вимкнено |
| Native preview | Оглянуто 1080×2408 | Частково видимі rows/text/icons, змішані file types і довгі UTF-8 names, editor/keyboard, Linux review/blocked plans, USB access selection |

### Межі та докази Linux тесту

Linux runner створив власний regular ext4 image 67 108 864 bytes. Справжні Partclone, zstd та e2fsprogs виконали backup → wipe → restore. У фінальному запуску архів entries мав 1 051 354 bytes; це розмір спеціально створеної невеликої fixture, не прогноз для userdata телефона. Після restore independent reclone повернув native body SHA-256 `42feb9d93a403eebb08a59753e630944a3530ce732aed819be01c0b3ea85f664`, збережений sentinel text прочитано через debugfs. BOOT/інші images та файли SD архіву не змінилися. Stale unmount не створив план і не змінив image.

Mount states у цьому тесті — дані filesystem fixture; реальних mount/loop/block operations немає. Native image restore перевіряє UI→CLI→backend інтеграцію на host, але не доводить успішний фізичний restore телефона. Receipt: `v05-linux-results.json`.

### Межі USB тесту

`tools/usb_fixture.py` — явно позначений file-backed contract double. Він перевіряє argv, токени, hold та post-operation status у UI, але не відкриває gadget/FunctionFS і не тестує MTP client. Поточне локальне execution середовище забороняє створення AF_UNIX socket; production helper не послаблювали для цього обмеження. Production supervisor/lease/IPC та фізичний MTP перевіряються окремо відповідальним агентом. Receipt: `v05-usb-ui-results.json`.

## Артефакти

- `v05-validation.json` — runtime/test/preview manifest.
- `v05-sanitizer-results.json` — фінальний sanitizer run; кожен case має exit/result/time.
- `v05-interaction-results.json`, `v05-linux-results.json`, `v05-usb-ui-results.json` — детальні receipts.
- `v05-selftest.stdout.log`, `v05-selftest.stderr.log` — raw normal selftest output.
- `v05-asan-{selftest,motion,usb-contract,linux-native}.{stdout,stderr}.log` — raw sanitizer wrapper output.
- `v05-{workflow}.log` — окремі UI helper/gesture logs, зокрема cache crossings і consumed stop taps.
- `previews/v05-*-native.png` — native previews та FILE TEST result screens; вони не є screenshots телефона.

Mutable fixtures створювалися в `/dev/shm`, поза source workspace. Це усуває раніше зафіксоване стороннє відновлення видалених/перейменованих fixture entries у shared workspace; історичні receipts v0.4 залишено без змін. Цей факт не був підставою для послаблення filesystem guards або product deletion logic.

## Відтворення на host

Команди наведено для fish. Native Linux test потребує вже встановлених Partclone/zstd/e2fsprogs у PATH. Він не завантажує binaries самостійно й не потребує loop mount.

```fish
cd /workspace/scratch/14f750bc1a57/a22x-recovery-dev/ui
make
make test
env A22X_UI_FIXTURE_BASE=/dev/shm make motion-integration
env A22X_UI_FIXTURE_BASE=/dev/shm make usb-ui-integration
begin
    set -lx PATH /workspace/scratch/14f750bc1a57/partclone-test-runtime/bin $PATH
    env A22X_UI_FIXTURE_BASE=/dev/shm make linux-integration
end
```

Для sanitizer використовують той самий C source та `-fsanitize=address,undefined -fno-omit-frame-pointer`; binary передають runners через `A22X_UI_BINARY`, а `ASAN_OPTIONS=detect_leaks=0` вимикає тільки leak detection. Успіх AddressSanitizer/UndefinedBehaviorSanitizer не є твердженням про LeakSanitizer.

## Що ще має підтвердити фізична перевірка

Root перевіряє точний ARM binary/boot ID, реальні evdev gestures і framebuffer render cost на 60/90 Гц, не відкриваючи сторонніх файлів. Для editor використовується окремий owned text fixture з відомим SHA. Mounted userdata відкривається безпосередньо з Files volume list: додатковий tap Open files після вибору змонтованого userdata не потрібен.

Окремі image/qualification receipts потрібні для фізичного Linux restore та MTP. До їх отримання цей звіт підтверджує реалізацію й локальну інтеграцію UI, не успіх фізичних destructive operations і не гарантовану кадрову частоту.
