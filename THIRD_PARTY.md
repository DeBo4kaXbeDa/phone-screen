# Сторонние компоненты

- [scrcpy и ADB](https://github.com/Genymobile/scrcpy) передают экран и звук.
- [Python](https://www.python.org/) выполняет программу.
- [PySide6 / Qt](https://doc.qt.io/qtforpython-6/) используется для интерфейса.
- [qrcode](https://pypi.org/project/qrcode/) создаёт QR-коды.
- [zeroconf](https://pypi.org/project/zeroconf/) обнаруживает телефоны в сети.

Готовые сборки содержат лицензии компонентов в `licenses/` и внутри
`_internal/vendor/scrcpy-*/`. Версии указаны в `components.json`.
Исходные бинарные файлы scrcpy скачиваются из официальных релизов;
перед распаковкой проверяется опубликованная SHA-256 сумма.
