"""Windows runtime smoke check; does not pair with or control any phone."""
import json
import os
from pathlib import Path
import sys

os.environ['ADB_SERVER_SOCKET'] = 'tcp:127.0.0.1:5041'
from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtCore import QTimer
import main


app = QApplication([])
window = main.Window()
window.show()
window.show_qr()
folder = Path(sys.argv[1])
folder.mkdir(parents=True, exist_ok=True)


def verify():
    picture = next(label for label in window.qr_dialog.findChildren(QLabel) if not label.pixmap().isNull())
    picture.pixmap().save(str(folder / 'windows-qr.png'))
    window.qr_dialog.grab().save(str(folder / 'windows-preview.png'))
    report = {
        'platform': sys.platform,
        'native_windows_runtime': main.IS_WINDOWS,
        'adb_exe_present': Path(main.binary('adb')).is_file(),
        'scrcpy_exe_present': Path(main.binary('scrcpy')).is_file(),
        'qr_created': not picture.pixmap().isNull(),
        'zeroconf_started': window.lan_discovery is not None,
        'log_writable': main.connection_log().parent.is_dir(),
    }
    (folder / 'windows-runtime-test.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    window.qr_dialog.reject()
    window.close()
    app.exit(0 if all(value for key, value in report.items() if key != 'platform') else 1)


QTimer.singleShot(1500, verify)
sys.exit(app.exec())
