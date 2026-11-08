#!/usr/bin/env python3
"""Local-network Android screen viewer; scrcpy handles the video transport."""
import ipaddress
import os
import re
import secrets
import shutil
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / 'vendor' / 'python'))
IS_WINDOWS = sys.platform == 'win32'

from PySide6.QtCore import QProcess, QProcessEnvironment, QSettings, QTimer, Qt
from PySide6.QtGui import QImage, QPixmap, QColor
from PySide6.QtWidgets import (
    QApplication, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QDialog, QMainWindow, QMessageBox, QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget,
)

VENDOR = ROOT / 'vendor' / ('scrcpy-win64-v5.0.1' if IS_WINDOWS else 'scrcpy-linux-x86_64-v5.0.1')
LOOKUPS = ThreadPoolExecutor(max_workers=2)


def binary(name):
    return str(VENDOR / (name + '.exe' if IS_WINDOWS else name))


def connection_log():
    if IS_WINDOWS:
        folder = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'PhoneScreen'
    elif getattr(sys, 'frozen', False):
        folder = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'phone-screen'
    else:
        folder = ROOT
    folder.mkdir(parents=True, exist_ok=True)
    return folder / 'connection.log'


def qr_pixmap(payload):
    import qrcode
    code = qrcode.QRCode(box_size=7, border=4)
    code.add_data(payload)
    code.make(fit=True)
    matrix = code.get_matrix()
    size = len(matrix)
    image = QImage(size, size, QImage.Format_RGB32)
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            image.setPixelColor(x, y, QColor('black' if dark else 'white'))
    return QPixmap.fromImage(image.scaled(size * 7, size * 7, Qt.KeepAspectRatio, Qt.FastTransformation))


def endpoint(value):
    """Require an explicit IPv4 address and port, as shown by Android."""
    host, separator, port = value.strip().rpartition(':')
    if not separator:
        raise ValueError('Введите IP и порт, например 192.168.3.10:37123.')
    try:
        ipaddress.IPv4Address(host)
        valid_port = port.isascii() and port.isdecimal() and 1 <= int(port) <= 65535
    except ValueError:
        valid_port = False
    if not valid_port:
        raise ValueError('Проверьте IPv4-адрес и порт (от 1 до 65535).')
    return f'{host}:{int(port)}'


def services(text):
    result = []
    for line in text.splitlines():
        # Avahi also sees Android variants whose quoted service names are
        # ignored by adb's built-in discovery. Escapes use decimal octets.
        avahi = line.split(';')
        if len(avahi) >= 9 and avahi[0] == '=':
            name = re.sub(r'\\(\d{3})', lambda match: chr(int(match.group(1))), avahi[3])
            try:
                result.append((name, avahi[4].rstrip('.'), endpoint(avahi[7] + ':' + avahi[8])))
            except ValueError:
                pass
            continue
        fields = line.split()
        if len(fields) >= 2 and fields[1] == 'device':
            try:
                result.append(('connected', '_adb-tls-connect._tcp', endpoint(fields[0])))
            except ValueError:
                pass
            continue
        if len(fields) == 3 and fields[1].startswith('_adb-'):
            try:
                result.append((fields[0].removesuffix('.'), fields[1].rstrip('.'), endpoint(fields[2])))
            except ValueError:
                pass
    return result


class Window(QMainWindow):
    def __init__(self, enable_discovery=True):
        super().__init__()
        self.setWindowTitle('Экран телефона')
        self.resize(640, 630)
        self.settings = QSettings('LocalTools', 'PhoneScreen')
        self.task = None
        self.video = None
        self.pending = None
        self.stopping = False
        self.discovery = None
        self.discovery_process = None
        self.avahi = shutil.which('avahi-browse')
        self.direct_lookup = True
        self.qr_dialog = None
        self.qr_secret = None
        self.qr_generation = 0
        self.discovery_generation = 0
        self.reconnect_deadline = None
        self.closing = False
        self.lookup_future = None
        self.lan_discovery = None
        if enable_discovery:
            try:
                from lan_discovery import LanDiscovery
                self.lan_discovery = LanDiscovery()
            except Exception as error:
                with connection_log().open('a', encoding='utf-8') as log:
                    log.write('Ошибка обнаружения в сети: ' + str(error) + '\n')
        self.discovery_timer = QTimer(self)
        self.discovery_timer.setInterval(1000)
        self.discovery_timer.timeout.connect(self.discover)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self.timed_out)

        body = QWidget()
        body.setMinimumHeight(700)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        self.setCentralWidget(scroll)
        layout = QVBoxLayout(body)
        title = QLabel('Экран Android по Wi-Fi')
        title.setStyleSheet('font-size: 24px; font-weight: 600; margin-bottom: 10px')
        layout.addWidget(title)
        instructions = QLabel(
            'Телефон и ПК должны быть в одной локальной сети.\n'
            'На телефоне: Для разработчиков → Беспроводная отладка.\n'
            'Для простого подключения нажмите «Сопряжение по QR».\n'
            'Полностью беспроводное сопряжение требует Android 11 или новее.'
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        pair_box = QGroupBox('1. Первое сопряжение')
        pair_form = QFormLayout(pair_box)
        self.qr_button = QPushButton('Сопряжение по QR — без ввода адресов')
        self.qr_button.clicked.connect(self.show_qr)
        pair_form.addRow(self.qr_button)
        self.pair_address = QLineEdit()
        self.pair_address.setPlaceholderText('IP:порт из окна с кодом сопряжения')
        self.code = QLineEdit()
        self.code.setPlaceholderText('6 цифр с телефона')
        self.code.setMaxLength(6)
        self.code.setEchoMode(QLineEdit.Password)
        self.pair_button = QPushButton('Выполнить сопряжение')
        self.pair_button.clicked.connect(self.pair)
        pair_form.addRow('Адрес сопряжения', self.pair_address)
        pair_form.addRow('Код', self.code)
        pair_form.addRow(self.pair_button)
        layout.addWidget(pair_box)

        view_box = QGroupBox('2. Трансляция')
        view_form = QFormLayout(view_box)
        self.address = QLineEdit(str(self.settings.value('address', '')))
        self.address.setPlaceholderText('IP:порт на главном экране беспроводной отладки')
        view_form.addRow('Адрес подключения', self.address)
        self.find_button = QPushButton('Найти адрес подключения в сети')
        self.find_button.clicked.connect(lambda: self.begin_discovery('find'))
        view_form.addRow(self.find_button)
        hint = QLabel('Порт подключения отличается от порта сопряжения и может меняться.')
        hint.setWordWrap(True)
        view_form.addRow(hint)
        buttons = QHBoxLayout()
        self.start_button = QPushButton('Показать экран')
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton('Остановить')
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        view_form.addRow(buttons)
        layout.addWidget(view_box)
        self.status = QLabel('Готово к подключению. Экран и звук телефона, без управления.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(150)
        layout.addWidget(self.log)

    def make_process(self):
        process = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert('ADB', binary('adb'))
        env.insert('SCRCPY_SERVER_PATH', str(VENDOR / 'scrcpy-server'))
        process.setProcessEnvironment(env)
        process.setProcessChannelMode(QProcess.MergedChannels)
        return process

    def busy(self, active):
        self.pair_button.setEnabled(not active)
        self.start_button.setEnabled(not active)
        self.qr_button.setEnabled(not active)
        self.find_button.setEnabled(not active)

    def error(self, message):
        self.status.setText(message)
        QMessageBox.warning(self, 'Экран телефона', message)

    def run_adb(self, arguments, callback, secret=None):
        self.busy(True)
        self.log.clear()
        process = self.make_process()
        self.task = process
        output = bytearray()
        process.readyReadStandardOutput.connect(
            lambda: output.extend(bytes(process.readAllStandardOutput()))
        )
        self.pending = callback
        process.finished.connect(lambda code, _status: self.adb_done(process, code, output))
        process.errorOccurred.connect(lambda _error: self.process_error(process))
        # adb officially supports the pairing secret as its final argument.
        # Do not depend on interactive console input in a graphical application.
        if secret:
            arguments = [*arguments, secret]
        self.timeout.start(20000)
        process.start(binary('adb'), arguments)

    def show_qr(self):
        if self.qr_dialog and self.qr_dialog.isVisible():
            self.qr_dialog.reject()
        self.cancel_task()
        self.stop_discovery()
        self.reconnect_deadline = None
        self.qr_generation += 1
        self.qr_name = 'studio-' + secrets.token_hex(5)
        self.qr_secret = secrets.token_hex(12)
        payload = f'WIFI:T:ADB;S:{self.qr_name};P:{self.qr_secret};;'
        try:
            pixmap = qr_pixmap(payload)
        except Exception as error:
            self.error('Не удалось создать QR-код: ' + str(error))
            return
        dialog = QDialog(self)
        self.qr_dialog = dialog
        dialog.setWindowTitle('Сопряжение по QR')
        dialog.setModal(True)
        layout = QVBoxLayout(dialog)
        label = QLabel('На телефоне откройте:\nБеспроводная отладка → Сопряжение по QR-коду.\n'
                       'Сканируйте этот код сканером из настроек отладки.')
        layout.addWidget(label)
        picture = QLabel()
        picture.setPixmap(pixmap)
        picture.setAlignment(Qt.AlignCenter)
        layout.addWidget(picture)
        self.qr_status = QLabel('Ожидание сканирования…')
        self.qr_status.setWordWrap(True)
        layout.addWidget(self.qr_status)
        refresh = QPushButton('Создать новый QR-код')
        refresh.clicked.connect(self.show_qr)
        layout.addWidget(refresh)
        cancel = QPushButton('Закрыть')
        cancel.clicked.connect(dialog.reject)
        layout.addWidget(cancel)
        dialog.finished.connect(lambda _result: self.qr_closed(dialog))
        dialog.show()
        self.begin_discovery('qr')

    def qr_closed(self, dialog):
        if dialog is not self.qr_dialog:
            return
        self.qr_generation += 1
        if self.discovery == 'qr':
            self.stop_discovery()
            self.status.setText('Сопряжение по QR отменено.')
        self.qr_secret = None
        self.cancel_task()

    def cancel_task(self):
        if self.task:
            process, self.task = self.task, None
            self.pending = None
            self.timeout.stop()
            process.kill()
            self.busy(False)

    def begin_discovery(self, mode, host=None):
        self.stop_discovery()
        self.discovery = mode
        if host is None:
            try:
                host = endpoint(self.address.text()).rpartition(':')[0]
            except ValueError:
                pass
        self.discovery_host = host
        self.discovery_count = 0
        self.discovery_deadline = time.monotonic() + (90 if mode == 'qr' else 25)
        if mode == 'connect':
            if self.reconnect_deadline is None:
                self.reconnect_deadline = time.monotonic() + 45
            self.discovery_deadline = self.reconnect_deadline
            self.stop_button.setEnabled(True)
        self.status.setText('Поиск телефона в локальной сети…')
        self.discovery_timer.start()
        self.discover()

    def stop_discovery(self):
        self.discovery_timer.stop()
        self.discovery = None
        self.discovery_generation += 1
        if self.lookup_future:
            self.lookup_future.cancel()
            self.lookup_future = None
        if self.discovery_process:
            process, self.discovery_process = self.discovery_process, None
            process.kill()

    def discover(self):
        if not self.discovery:
            return
        if time.monotonic() > self.discovery_deadline:
            mode = self.discovery
            self.stop_discovery()
            message = 'Телефон не найден. Проверьте Wi-Fi и беспроводную отладку. Можно ввести адрес вручную.'
            self.status.setText(message)
            self.stop_button.setEnabled(False)
            self.reconnect_deadline = None
            if mode == 'qr' and self.qr_dialog:
                self.qr_status.setText('Время ожидания истекло. Закройте окно и создайте новый QR-код.')
            return
        if self.discovery_process or self.lookup_future or self.task or self.video:
            return
        kind = '_adb-tls-pairing._tcp' if self.discovery == 'qr' else '_adb-tls-connect._tcp'
        if self.lan_discovery:
            rows = self.lan_discovery.snapshot(kind)
            if rows:
                text = '\n'.join('\t'.join(row) for row in rows)
                self.discovered(None, self.discovery, text.encode(), self.discovery_generation)
                if not self.discovery or self.task:
                    return
        self.discovery_count += 1
        if self.direct_lookup and self.discovery_host and self.discovery_count % 3 == 1:
            from mdns_lookup import lookup
            future = LOOKUPS.submit(lookup, self.discovery_host, kind)
            self.lookup_future = future
            self.poll_lookup(future, self.discovery, self.discovery_generation)
            return
        process = self.make_process()
        self.discovery_process = process
        output = bytearray()
        mode = self.discovery
        generation = self.discovery_generation
        process.readyReadStandardOutput.connect(lambda: output.extend(bytes(process.readAllStandardOutput())))
        process.finished.connect(lambda _code, _status: self.discovered(process, mode, output, generation))
        process.errorOccurred.connect(lambda _error: self.discovery_failed(process))
        if mode != 'qr' and self.discovery_count % 3 == 2:
            process.start(binary('adb'), ['devices', '-l'])
        elif self.avahi:
            process.start(self.avahi, ['-rtp', kind])
        else:
            process.start(binary('adb'), ['mdns', 'services'])
        QTimer.singleShot(4000, lambda: process.kill() if self.discovery_process is process else None)

    def poll_lookup(self, future, mode, generation):
        if self.closing or future is not self.lookup_future:
            return
        if not future.done():
            QTimer.singleShot(50, lambda: self.poll_lookup(future, mode, generation))
            return
        self.lookup_future = None
        try:
            rows = future.result()
        except Exception:
            rows = []
        output = '\n'.join('\t'.join(row) for row in rows).encode()
        self.discovered(None, mode, output, generation)

    def discovery_failed(self, process):
        if process.error() == QProcess.FailedToStart:
            if self.discovery_process is not process:
                process.deleteLater()
                return
            self.discovery_process = None
            self.stop_discovery()
            self.status.setText('Не удалось запустить поиск: ' + process.errorString())
            process.deleteLater()

    def discovered(self, process, mode, output, generation=None):
        if self.discovery_process is process:
            self.discovery_process = None
        if process is not None:
            process.deleteLater()
        if generation is not None and generation != self.discovery_generation:
            return
        if self.discovery != mode:
            return
        found = services(output.decode(errors='replace'))
        if mode == 'qr':
            matches = [(name, address) for name, kind, address in found
                       if kind == '_adb-tls-pairing._tcp' and name.strip('"') == self.qr_name]
            if matches:
                name, address = matches[0]
                secret = self.qr_secret
                if name == '"' + self.qr_name + '"':
                    # Some Android builds retain WifiConfiguration quoting in
                    # BOTH the service name and shared password.
                    secret = '"' + secret + '"'
                self.stop_discovery()
                self.qr_status.setText('Телефон найден. Выполняется сопряжение…')
                qr_generation = self.qr_generation
                self.run_adb(['pair', address], lambda ok, text: self.qr_paired(ok, text, address, qr_generation), secret=secret)
        else:
            matches = sorted(set(address for _name, kind, address in found
                                 if kind == '_adb-tls-connect._tcp' and
                                 (not self.discovery_host or address.rpartition(':')[0] == self.discovery_host)))
            if len(matches) == 1:
                self.stop_discovery()
                self.address.setText(matches[0])
                self.status.setText('Адрес подключения найден. Нажмите «Показать экран».')
                if mode == 'connect':
                    self.start(retry=True)
            elif len(matches) > 1:
                self.stop_discovery()
                self.status.setText('Найдено несколько телефонов. Введите адрес нужного вручную.')

    def qr_paired(self, success, output, address, generation=None):
        if generation is not None and generation != self.qr_generation:
            return
        if not self.qr_dialog or not self.qr_dialog.isVisible():
            return
        if success and 'Successfully paired' in output:
            self.qr_dialog.accept()
            self.begin_discovery('connect', address.rpartition(':')[0])
        else:
            self.qr_secret = None
            self.qr_status.setText('Сопряжение не удалось. Закройте окно и создайте новый QR-код. Подробности в журнале.')
            self.status.setText('Сопряжение не удалось: ' + output)

    def process_error(self, process):
        if process.error() == QProcess.FailedToStart:
            self.timeout.stop()
            if process is self.task:
                self.task = None
                self.pending = None
            if process is self.video:
                self.video = None
                self.stop_button.setEnabled(False)
            self.busy(False)
            self.error('Не удалось запустить компонент: ' + process.errorString())
            process.deleteLater()

    def timed_out(self):
        if self.task:
            self.cancel_task()
            self.status.setText('Время подключения истекло. Проверьте адрес, Wi-Fi и беспроводную отладку.')
            if self.qr_dialog and self.qr_dialog.isVisible():
                self.qr_status.setText('Нет ответа от телефона. Нажмите «Создать новый QR-код» и сканируйте снова.')

    def adb_done(self, process, code, output):
        if process is not self.task:
            process.deleteLater()
            return
        self.timeout.stop()
        text = output.decode(errors='replace').strip()
        self.log.setPlainText(text)
        with connection_log().open('a', encoding='utf-8') as log:
            log.write(time.strftime('%Y-%m-%d %H:%M:%S ') + text + '\n')
        callback, self.pending = self.pending, None
        self.task = None
        self.busy(False)
        process.deleteLater()
        if callback:
            callback(code == 0, text)

    def pair(self):
        try:
            address = endpoint(self.pair_address.text())
            code = self.code.text().strip()
            if len(code) != 6 or not code.isascii() or not code.isdecimal():
                raise ValueError('Введите шестизначный код с телефона.')
        except ValueError as error:
            self.error(str(error))
            return
        self.status.setText('Выполняется сопряжение… Оставьте окно с кодом на телефоне открытым.')
        self.code.clear()
        self.run_adb(['pair', address], self.paired, secret=code)

    def paired(self, success, output):
        success = success and 'Successfully paired' in output
        self.status.setText(
            'Сопряжение выполнено. Введите адрес с главного экрана беспроводной отладки.'
            if success else ('Сопряжение не выполнено. Создайте новый код на телефоне или используйте QR. '
                             'Ответ ADB: ' + output)
        )
        if success:
            self.begin_discovery('connect', self.pair_address.text().strip().rpartition(':')[0])

    def start(self, _checked=False, retry=False):
        if self.task or self.video:
            return
        if not retry:
            self.reconnect_deadline = time.monotonic() + 45
        self.stop_discovery()
        try:
            self.target = endpoint(self.address.text())
        except ValueError as error:
            self.error(str(error))
            return
        self.status.setText('Подключение к телефону…')
        self.run_adb(['connect', self.target], self.connected)

    def connected(self, success, output):
        # adb connect may return zero even when the connection failed.
        if not success or not any(line.startswith(('connected to ', 'already connected to ')) for line in output.splitlines()):
            if self.reconnect_deadline and time.monotonic() < self.reconnect_deadline:
                self.begin_discovery('connect', self.target.rpartition(':')[0])
                self.status.setText('Соединение не установлено. Ищу актуальный порт телефона…')
            else:
                self.reconnect_deadline = None
                self.status.setText('Подключение не удалось. Проверьте беспроводную отладку или создайте новый QR-код.')
            return
        self.reconnect_deadline = None
        self.settings.setValue('address', self.target)
        self.video = self.make_process()
        self.video.readyReadStandardOutput.connect(self.video_output)
        self.video.finished.connect(self.video_done)
        self.video.errorOccurred.connect(lambda _error: self.process_error(self.video) if self.video else None)
        self.stopping = False
        self.stream_disconnected = False
        self.busy(True)
        self.stop_button.setEnabled(True)
        self.status.setText('Запуск окна экрана…')
        self.video.start(binary('scrcpy'), [
            '--serial=' + self.target, '--no-control', '--audio-source=output',
            '--max-size=1920', '--max-fps=60', '--video-bit-rate=8M',
            '--window-title=Экран телефона',
        ])

    def video_output(self):
        output = bytes(self.video.readAllStandardOutput()).decode(errors='replace')
        self.log.append(output.rstrip())
        with connection_log().open('a', encoding='utf-8') as log:
            log.write(output)
        if 'Device disconnected' in output:
            self.stream_disconnected = True
        if re.search(r'Texture(?:\s*\([^)]*\))?:', output):
            self.status.setText('Экран и звук передаются на ПК. Закройте окно экрана для остановки.')

    def video_done(self, code, _status):
        self.video_output()
        process, self.video = self.video, None
        if process:
            process.deleteLater()
        self.busy(False)
        self.stop_button.setEnabled(False)
        self.status.setText('Трансляция остановлена.' if code == 0 or self.stopping else
                            'Трансляция прервалась. Проверьте телефон и подключитесь снова; подробности ниже.')
        if not self.closing and not self.stopping and (code != 0 or self.stream_disconnected):
            self.begin_discovery('connect', self.target.rpartition(':')[0])
            self.status.setText('Связь прервалась. Пытаюсь восстановить экран и звук…')

    def stop(self):
        self.stop_discovery()
        self.cancel_task()
        self.reconnect_deadline = None
        if self.video:
            self.stopping = True
            self.video.terminate()
            process = self.video
            QTimer.singleShot(2000, lambda: process.kill() if self.video is process else None)
        else:
            self.stop_button.setEnabled(False)
            self.status.setText('Подключение остановлено.')

    def closeEvent(self, event):
        self.closing = True
        if self.lan_discovery:
            self.lan_discovery.close()
        self.stop_discovery()
        self.timeout.stop()
        for process in (self.task, self.video, self.discovery_process):
            if process and process.state() != QProcess.NotRunning:
                process.kill()
                process.waitForFinished(1000)
        event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = Window()
    window.show()
    if '--qr' in sys.argv:
        QTimer.singleShot(0, window.show_qr)
    for argument in sys.argv[1:]:
        if argument.startswith('--connect='):
            window.address.setText(argument.split('=', 1)[1])
            QTimer.singleShot(0, window.start)
    sys.exit(app.exec())
