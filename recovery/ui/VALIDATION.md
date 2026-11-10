# UI v0.3-dev validation — 2026-11-05

## Результат і scope

Runtime source зафіксовано після інтеграції Power 200–203, Linux-профілів, діагностики, chmod/chown, явного symlink resolve/open та copy-tree. Локальні перевірки використовували синтетичні файли й каталоги. Цей звіт не заявляє апаратну перевірку UI v0.3 чи встановлення нового образу на телефон.

| Перевірка | Результат | Звіт |
|---|---|---|
| C11, `-Os -Wall -Wextra -Werror -fstack-protector-strong -D_FORTIFY_SOURCE=2` | PASS | Host binary |
| Self-tests | 41 PASS | `test-results.json` |
| Static x86-64 build і self-tests | 41 PASS | `v03-validation.json` |
| Linux та історичний Android archive: backup → verify → fixture restore | Обидва профілі PASS; SHA-256 відновлено; synthetic super незмінний | `test-results.json` |
| Базовий файловий UI | 3 workflows PASS | `filesystem-test-results.json` |
| Нові metadata/link/diagnostic workflows | 8 PASS, включно з очікуваними відмовами | `v03-test-results.json` |
| Copy-tree через UI | 2 PASS: копія та відмова зі старим entry token | `v03-tree-test-results.json` |
| ASan + UBSan | 41 self-tests та 8 нових спільних workflows PASS | `v03-sanitizer-test-results.json` |
| Native renderer visual QA | Оглянуто 1080×2408 previews і результат реального fixture export | `previews/` |

Host ELF: **1,756,880 bytes** dynamic, **2,740,776 bytes** static. Це x86-64 файли; їхній розмір не є розміром ARM64 binary або recovery partition image. ARM64 cross-build і телефон перевіряє інтеграційна частина проєкту.

ASan/UBSan запускалися з `ASAN_OPTIONS=detect_leaks=0`. LeakSanitizer у цьому середовищі завершився повідомленням про несумісність із ptrace; перевірка витоків пам'яті не заявляється. Два copy-tree workflows додані після завершення sanitizer запуску та перевірені звичайним host binary.

## Поведінка, підтверджена тестами

Power зберігає `--allow-power-actions`, заборону під час активної backend-операції та утримання 2 секунди після вибору. Нові виходи 202/203 перевірено через справжній UI pointer/hold dispatch. Некоректний intent та вимкнений power guard не завершують UI. Старі multitouch, SYN_DROPPED, вихід пальця за кнопку та коротке утримання залишаються у self-tests.

У стандартному Backup залишено `linux` і `recovery`. Android archives продовжують читатися й перевірятися, що підтверджує окремий backup/verify/fixture-restore regression. Жоден старий archive не видаляється. C guard фізичного restore залишається активним; `--device --workflow ...` відхиляється до I/O.

Metadata workflow запускає `stat` → `chmod 0640` → `chown` із підтвердженням, звіряє фактичний mode та перевіряє недоступність chmod/chown для hardlinked файла. Окремий workflow передає старий entry token і отримує `ENTRY_CHANGED`, з незмінним джерелом. Octal mode та numeric UID:GID проходять локальну валідацію; токени й числові значення передаються окремими argv.

Symlink workflow спочатку показує link text без переходу, явно виконує `resolve`, а потім відкриває канонічний target звичайним read. Посилання, що виходить із кореня тому, відхиляється; зовнішній файл не читається й не змінюється. Звичайні read/list зберігають no-follow поведінку.

Copy-tree UI з'являється у дозволеному стані лише за `capabilities.copy_directories=true`. Реальна копія на другий fixture volume зберегла вкладений файл, UTF-8 вміст, `0640`/`0750` modes і symlink як посилання. Старий source entry token дав відмову без опублікованого destination. Обидва volume tokens та entry token передані helper; destination існувати не повинен. Внутрішні межі й додаткові правила tree-copy перевіряються тестами filesystem helper, не підміняються UI.

Diagnostics отримує фактичний JSON management helper. Перевірено статус ключів, тимчасовий fingerprint, вибір змонтованого RW microSD, коротке утримання без запуску, створення справжнього `.tar.gz` та його читання. Архів містить очікуваний log і manifest; fixture key file не потрапив до нього. Старий volume token отримує `VOLUME_CHANGED`; без RW volume export недоступний. Після невдалого refresh старі SSH поля не показуються як актуальні. UI не запускає SSH сервер і не показує фіктивної кнопки fsck.

Базові workflows додатково зберігають RO→RW→mkdir→UTF-8 edit/save→copy→rename→delete→RO→unmount, SHA-256 stale-save refusal, 64 KiB text limit, Unicode/semicolon/`$(...)` literal argv, очищення RAM stage та нерозголошення filesystem response contents у UI log.

## Одноразова помилка під час перевірки

Перший базовий UI roundtrip повернув `IO_ERROR` під час видалення порожнього каталогу. Первинний errno не був зафіксований; причину встановити не вдалося. UI показав невдачу й не повторив дію автоматично. Після додавання детального fixture-only failure message виконано **50 create → stat → delete cycles** та **10 повних UI roundtrips**; усі пройшли, повторення не було. Це зафіксовано у `rmdir-sequence-test-results.json`; відсутність повторення не видається за доведену причину помилки. Фінальний базовий файловий набір також PASS.

## Візуальна перевірка

Оглянуто Home, два Backup профілі, Power, Download confirmation, Diagnostics, destination export, file info, permissions keyboard, symlink info та результат справжнього fixture export. Заголовки, кнопки, кирилиця, UID/GID, fingerprint і SHA-256 видимі без накладання. Native projection використовує 1080×2400 усередині 1080×2408 із відступами по 4 пікселі; self-test звіряє mapping пікселів. PREVIEW та FILE TEST позначки відрізняють синтетичні стани від апаратного screenshot.

`tools-native.png` — ілюстративний екран; `v03-tools-export-native.png` — фактичний завершений fixture export. Шляхи тимчасових fixtures, показані у screenshots, після тестів видалені.

## Відтворення

```fish
make test
make static
./a22x-recovery-ui-static --self-test
make integration
make files-integration
make v03-integration
make preview

cc -std=c11 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined -fno-omit-frame-pointer recovery_ui.c json_min.c -o a22x-recovery-ui-asan
env ASAN_OPTIONS=detect_leaks=0 ./a22x-recovery-ui-asan --self-test
env ASAN_OPTIONS=detect_leaks=0 A22X_UI_BINARY=(pwd)/a22x-recovery-ui-asan python3 tools/test_v03_integration.py --without-tree
```

Фінальний tree-only додаток виконано через `python3 tools/test_v03_integration.py --only-tree`, щоб перевірити нову інтеграцію без повторення вже завершених сценаріїв. Стандартний `make v03-integration` запускає всі 10 сценаріїв разом.

## Frozen source

- `recovery_ui.c`: `b8ba20d8eaad891891f07ef02a193ab492694fea55d985fd0cc73d865626374d`
- `json_min.c`: `cfaaf2f6fe3d423592fec7cf1e2aa60b068725414bfead038c0dc43fb52ce5fc`
- `json_min.h`: `6357cf2d3222444e1489dad0c8769b645e8cd6c3447506b99a0f3d53cc30d2d3`
- `font_data.h`: `28f182dcd0332429873d2e1c51e6d3fd16327b2acfb622b0ac89aa26f9993fa5`
- `font_extra.h`: `286805033c6846b970013512b1d0554105c69680d10c05b921b9fcfbe72720c6`

Машинний підсумок із розмірами й SHA-256 host binaries: `v03-validation.json`.

## Межі цього звіту

Цей каталог не заявляє physical flash, touch/display, SSH/SFTP/PTY, Recovery/Download reboot чи filesystem I/O на телефоні для v0.3. Такі результати належать інтегрованому образу та записуються окремо. Фізичне відновлення розділів у UI заблоковано. Немає форматування, LUKS/FBE unlock, ZIP installer, fsck/repair frontend, repartition або raw-partition editor. Symlink/special files не виконуються і не редагуються як звичайні файли.
