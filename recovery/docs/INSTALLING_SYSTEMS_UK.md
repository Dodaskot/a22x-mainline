# Встановлення інших систем

Recovery встановлює готовий пакет із microSD без комп'ютера. Сам образ ISO або RAW готується один раз на ПК з Linux.

## Що підтримується

- ARM64-образи: ISO, RAW, IMG, qcow2, tar, також стиснені xz, zstd, gzip чи bzip2.
- Файлові системи всередині: ext4, btrfs, xfs, squashfs, erofs.
- Лише системи на systemd з об'єднаним `/usr`.

Ядро й завантажувач образу не використовуються: телефон лишається на своєму BOOT. Встановлення на телефоні не гарантує, що система працюватиме: перший запуск нового дистрибутива треба перевірити.

Потрібні на ПК: `python3`, `tar`, `bsdtar`, `unsquashfs`, `guestfish`, `sfdisk`, налаштовані `/etc/subuid` та `/etc/subgid`.

## 1. Зняти файли телефона

Модулі ядра й прошивки належать виробнику і в репозиторії їх немає. У recovery змонтуй userdata і виконай у терміналі:

```fish
sh /mnt/backup/a22x-capture-platform.sh /mnt/backup/a22x-platform.tar
```

Цей архів прив'язаний до твого телефона. Не публікуй його.

## 2. Підготувати пакет

```fish
python3 tools/a22x-image-import.py Fedora-Workstation-Live.iso \
    --id fedora-arm64 \
    --platform a22x-platform.tar \
    --platform-meta accounts.json \
    --profile distro-platform.json \
    --helpers helpers \
    --output Systems/fedora-arm64 \
    --work /шлях/із/вільним/місцем \
    --console
```

`accounts.json` перелічує користувачів зі спільного `/home`. `--console` запускає систему в текстовому режимі. `--authorized-key` додає ключ для входу root по USB.

## 3. Встановити на телефоні

1. Скопіюй теку з пакетом на microSD у `Systems/`.
2. Files: змонтуй userdata із записом.
3. Operating systems → Linux distributions → імпорт, вибери `bundle.json`.
4. Обери новий слот, перемкнись і перезапусти Linux.

Поточна система лишається окремим слотом, а `/home` спільний. Перемкнутися назад можна в тому самому меню.

## Перезапуск зі встановленої системи

```fish
a22x-reboot recovery
```

Команда спершу записує маркери Samsung у SRAM. Без них перезапуск із підключеним кабелем може привести в режим заряджання, з якого виходять лише довгим натисканням Power.
