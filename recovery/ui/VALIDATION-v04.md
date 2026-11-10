# UI v0.4-dev — локальна перевірка

Дата: 2026-11-05. Scope: `ui/`, без телефонних, block-device, DRM або input-device операцій. Апаратні результати інтегрованого образу записуються окремо.

## Реалізовано

Список папки рухається за пальцем у пікселях. Після руху понад 12 логічних пікселів tap на файл чи його меню скасовується; горизонтальний drag також не перетворюється на натискання. Зсув обмежено початком і кінцем списку. Частково видимі рядки обрізаються viewport. Замість сторінок є безперервний зсув, scrollbar та додаткові стрілки на половину viewport. UI тримає до 64 записів і підвантажує перекривні вікна, тому довгі папки не обмежені першими 64 файлами. Інерції після відпускання немає.

Типи мають одночасно колір, піктограму й текстовий підпис: папки — помаранчеві, звичайні файли — світло-блакитні, виконувані — зелені, symlinks — фіолетові, спеціальні/заблоковані записи — приглушені. `Executable` визначається тільки для звичайного файла за фактичними execute permission bits. UI не запускає файл через цю позначку і не вгадує MIME за розширенням.

Tools → Display refresh rate показує поточні 60/90 Гц, роздільність 1080×2408 та доступні режими. Режим діє в поточному recovery boot; нове завантаження recovery починається з 60 Гц. Helper викликається через окремі argv. Після кожного успішного `set` обов'язковий незалежний `status` із відповідними `current_hz`, `actual_hz`, supported/service flags. UI не підміняє перевірку локальним перемиканням напису. Технічні timing/service поля не захаращують основний екран, але використовуються для внутрішньої перевірки.

## Перевірки

| Перевірка | Результат |
|---|---|
| C11 host build із `-Wall -Wextra -Werror` | PASS |
| Self-tests | 52 PASS |
| Реальний filesystem helper: папка 200 файлів | 48 drag-жестів; доступ до першого й останнього файла; PASS |
| Ненавмисні файлові дії після drag | 0 read, 0 stat, 0 write; тільки 2 явні tap-read у сценарії |
| Зсуви | 0, 21, 500 та кінцеві 22382 px перевірено; межі не порушено |
| Пам'ять списку | Не більш як 64 записи у вікні, підвантаження до кінця папки |
| Базовий файловий roundtrip після нової навігації | RO→RW→create/edit/copy/rename/delete→RO→unmount PASS |
| Display fixture | 60→90→60, unavailable mode/service, хибний успіх set, явна помилка set — усі очікувані результати PASS |
| ASan + UBSan | 52 self-tests і всі 7 цільових workflows PASS; фінальні mutable fixtures на `/dev/shm` tmpfs |
| Native visual QA | 1080×2408: типи/значки/кольори, довга кирилиця, clipped rows, 90 Гц та unavailable state PASS |

LeakSanitizer вимкнено через несумісність поточного середовища з ptrace; перевірка витоків не заявляється. Повні історичні backup/restore тести v0.3 не повторювалися: ця зміна не торкалася backup backend або фізичного restore guard.

Незалежний review знайшов і відтворив коротке вікно між завершенням list helper та redraw: новий кеш міг отримати старі hit rectangles. Виправлено скиданням hit map/focus/pending action при запуску та завершенні list. Окремий self-test виконує заміну запису й tap до render та доводить, що жодний файл не вибирається; новий render відновлює правильні кнопки. Інших конкретних cache/gesture blockers review не знайшов.

### Виявлена особливість робочого файлового середовища

Під час фінального sanitizer roundtrip на workspace overlay після успішних rename і delete початкове ім'я файла з'явилося знову з **іншим inode**. Трасування зафіксувало правильні argv та JSON results: перейменований inode `1577229` був видалений, каталог став порожнім, але перед наступним rmdir з'явився початковий файл з inode `1577747` і новішим ctime. Тоді backend коректно відмовив із `IO_ERROR`, errno **39 / ENOTEMPTY**. В іншому спостереженні повернення файла сталося після успішного UI workflow. Процес, який його відтворив, не встановлено; це не названо доведеною помилкою конкретного сервісу синхронізації.

Один вузький порівняльний запуск незмінених UI binary і filesystem helper на окремому `/dev/shm` tmpfs завершився успішно. Parent і новий subprocess підтвердили відсутність каталогу, той самий inode тому й однаковий mount namespace. Після цього всі 7 цільових sanitizer workflows пройшли на tmpfs. Product-код видалення не змінювався. Mutable fixtures тестового runner тепер створюються поза source workspace; `A22X_UI_FIXTURE_BASE` дозволяє явно вибрати tmpfs.

Повні receipts збережено у `v04-workspace-mutation-trace.jsonl`, `v04-traced-fixture-result.json`, `v04-tmpfs-roundtrip-result.json`. Тимчасовий workspace fixture після збереження трасування прибрано. Спостереження з workspace не приховано успішним повторенням на іншому filesystem.

## Відтворення

```fish
make test
make interaction-integration
env A22X_UI_FIXTURE_BASE=/dev/shm make interaction-integration
./a22x-recovery-ui --preview file-scroll --render-native previews/v04-file-scroll-native.ppm
./a22x-recovery-ui --preview display --render-native previews/v04-display-native.ppm
python3 tools/ppm_to_png.py previews
```

Машинні звіти: `v04-validation.json`, `v04-interaction-results.json`, `v04-sanitizer-results.json`. Preview PNG позначено PREVIEW, реальний fixture workflow — FILE TEST. Жоден із них не видається за screenshot телефона.

## Апаратний smoke test

Логічні координати 720×1600: Home→Files `(532,506)`, перший mounted userdata `(360,311)`, swipe `(250,1050)`→`(250,550)`. Native 1080×2408 projection: `x×1.5`, `y×1.5+4`; evdev harness додатково враховує фактичні діапазони осей.

Після release `/run/a22x-recovery/ui.log` містить `offset_px=500 first_index=4 ... tap_suppressed=1`; reverse gesture повертає `offset_px=0 first_index=0`. Лог не містить назв файлів. Root запускає окремий physical-input harness після закриття SSH operation lease. Цей документ не заявляє результат того запуску.

## Frozen runtime

`recovery_ui.c` SHA-256: `b6a69630cf47760978695afb4060c7626614be64a6bb67c3c1d551d7f0b85e58`.

Renderer залишається fbdev. Планований drag redraw interval 16 мс не є вимірюванням FPS. Фактична підтримка 60/90 Гц, persistent DRM fd, холодне завантаження й touch input належать перевіркам інтегрованого recovery image.
