# Первинні джерела та вибір UI

Перевірено 2026-11-05. Посилання нижче ведуть на офіційні документи/репозиторії; код цієї реалізації створений окремо.

## OrangeFox / TWRP

1. [OrangeFox: Building OrangeFox](https://wiki.orangefox.tech/dev/building). Документ визначає GPLv3-or-later для OrangeFox source/vendor/device trees та вимогу опублікувати змінені джерела при публічному розповсюдженні зміненої збірки. Описує Android build branches, device trees, prebuilt kernel і recoveryimage target. Це підтверджує, що OrangeFox є повним Android recovery-проєктом, а не окремою Linux UI-бібліотекою.
2. [OrangeFox: офіційний source tree fox_12.1](https://gitlab.com/OrangeFox/bootable/Recovery/-/tree/fox_12.1). Дерево містить GUI/minui, Android recovery, partition manager, sideload та Android build-інтеграцію. Безпосереднє перенесення всього проєкту потребувало б окремого порту його залежностей. Це інженерний висновок із архітектури, а не офіційна заява про неможливість порту.
3. [OrangeFox: про проєкт](https://wiki.orangefox.tech/root). Пояснює зв'язок із TWRP та розвиток інтерфейсу/функцій recovery.
4. [Team Win: How to compile TWRP](https://twrp.me/faq/howtocompiletwrp.html). Офіційна документація описує Android-targeted manifests/branches і перенесення device trees.

Практичне рішення: використовувати знайомі дії Backup/Restore/Verify, темний фон, великі touch targets та помаранчевий акцент; створити незалежний native renderer. Не переносити OrangeFox logo/theme/XML/code без необхідності та аудиту ліцензій компонентів. Інтерфейс не називається OrangeFox і не заявляє офіційну сумісність із ним.

## Linux display / input

5. [Linux kernel: framebuffer API](https://docs.kernel.org/fb/api.html). Описує fixed/variable framebuffer info, pixel formats, bitfields, stride та інші параметри, які UI читає ioctl-викликами. Значення resolution і stride не захардкожені.
6. [Linux kernel: multi-touch protocol](https://cdn.kernel.org/doc/html/latest/input/multi-touch-protocol.html). Описує Type-B slots, ABS_MT_TRACKING_ID, SYN_REPORT та збереження стану контактів між подіями. Саме цей контракт використовується для Himax/evdev.

Рішення framebuffer/evdev також спирається на read-only інвентаризацію наявного kernel #107 та його попереднього charger userspace у цьому проєкті. Офіційні Linux документи пояснюють API; вони самі не підтверджують запуск нового UI на SM-A226B.

## Шрифт та ліцензія

7. [DejaVu: License](https://dejavu-fonts.github.io/License.html). Перевірено умови поширення glyphs та збереження notices. Вбудовані ASCII glyphs отримані з локальних DejaVuSans.ttf і DejaVuSans-Bold.ttf; точні SHA-256 зафіксовані у `font-provenance.json`. Локальний copyright notice з пакета `fonts-dejavu-core` збережено у `LICENSE.fonts`. Жодних font assets OrangeFox не використано.

`tools/generate_native_fonts.py` потрібен лише для відтворення атласів шрифтів під час розробки. Runtime не завантажує TTF, не вимагає FreeType і не читає `/data` для UI assets.
