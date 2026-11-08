"""Run with QT_QPA_PLATFORM=offscreen python3 -m unittest -v."""
import json
import os
import sys
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLabel
import main


class ConnectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_root, self.old_vendor = main.ROOT, main.VENDOR
        main.ROOT = main.VENDOR = self.root
        QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(self.root))
        adb = self.root / 'adb'
        adb.write_text('#!' + sys.executable + '\n' + '''
import json,sys,time
from pathlib import Path
state_path=Path(__file__).with_name('state.json')
state=json.loads(state_path.read_text())
if sys.argv[1:]==['mdns','services']:
 print('List of discovered mdns services')
 for row in state.get('services',[]): print(' '.join(row))
elif sys.argv[1]=='pair':
 time.sleep(state.get('pair_delay',0))
 assert len(sys.argv)==4 and sys.argv[3]==state['secret']
 if state.get('fail'):
  print("error: protocol fault (couldn't read status message): Success")
 else:
  state['services']=[['adb-test','_adb-tls-connect._tcp','192.168.3.4:39001']]
  state_path.write_text(json.dumps(state))
  print('Successfully paired to '+sys.argv[2])
elif sys.argv[1]=='connect':
 print('failed to connect' if sys.argv[2]==state.get('bad_address') else 'connected to '+sys.argv[2])
''')
        adb.chmod(0o755)
        scrcpy = self.root / 'scrcpy'
        scrcpy.write_text('#!' + sys.executable + '\n' + '''
import sys,time
from pathlib import Path
Path(__file__).with_name('video-args.json').write_text(__import__('json').dumps(sys.argv))
print('INFO: Texture: 1080x1920',flush=True)
time.sleep(30)
''')
        scrcpy.chmod(0o755)
        self.state(services=[])
        self.window = main.Window(enable_discovery=False)
        self.window.avahi = None
        self.window.direct_lookup = False

    def tearDown(self):
        if self.window.qr_dialog:
            self.window.qr_dialog.close()
        self.window.close()
        self.app.processEvents()
        main.ROOT, main.VENDOR = self.old_root, self.old_vendor
        self.temp.cleanup()

    def state(self, **state):
        (self.root / 'state.json').write_text(json.dumps(state))

    def until(self, predicate):
        deadline = time.monotonic() + 6
        while not predicate():
            self.app.processEvents()
            time.sleep(.01)
            self.assertLess(time.monotonic(), deadline, self.window.status.text())

    def test_qr_roundtrip_and_stream(self):
        w = self.window
        w.show_qr()
        picture = next(label for label in w.qr_dialog.findChildren(QLabel)
                       if not label.pixmap().isNull())
        png = self.root / 'qr.png'
        picture.pixmap().save(str(png))
        decoded = subprocess.check_output(['zbarimg', '--quiet', '--raw', str(png)], stderr=subprocess.DEVNULL).decode().strip()
        self.assertEqual(decoded, f'WIFI:T:ADB;S:{w.qr_name};P:{w.qr_secret};;')
        self.state(secret=w.qr_secret, services=[
            ['studio-unrelated', '_adb-tls-pairing._tcp', '192.168.3.9:41234'],
            [w.qr_name, '_adb-tls-pairing._tcp', '192.168.3.4:41235'],
        ])
        self.until(lambda: 'Экран и звук передаются' in w.status.text())
        self.assertFalse(w.qr_dialog.isVisible())
        self.assertIsNone(w.qr_secret)
        self.assertEqual(w.address.text(), '192.168.3.4:39001')
        args = json.loads((self.root / 'video-args.json').read_text())
        self.assertIn('--no-control', args)
        self.assertNotIn('--no-audio', args)
        self.assertIn('--audio-source=output', args)
        self.assertIn('--serial=192.168.3.4:39001', args)
        self.assertNotIn(decoded.split('P:')[1].split(';')[0], (self.root / 'connection.log').read_text())
        w.stop()
        self.until(lambda: w.video is None)
        self.assertTrue(w.start_button.isEnabled())

    def test_pair_failure_and_qr_cancel(self):
        w = self.window
        w.show_qr()
        self.state(secret=w.qr_secret, fail=True, services=[
            [w.qr_name, '_adb-tls-pairing._tcp', '192.168.3.4:41235']])
        self.until(lambda: 'Сопряжение не удалось' in w.status.text())
        self.assertIsNone(w.video)
        self.assertIn('protocol fault', w.log.toPlainText())
        w.qr_dialog.reject()
        self.assertIsNone(w.discovery)
        self.assertIsNone(w.qr_secret)

    def test_manual_code_and_automatic_address(self):
        w = self.window
        self.state(secret='123456', services=[])
        w.pair_address.setText('192.168.3.4:41235')
        w.code.setText('123456')
        w.pair()
        self.until(lambda: 'Экран и звук передаются' in w.status.text())
        self.assertEqual(w.code.text(), '')

    def test_invalid_addresses_and_mdns(self):
        for value in ['foo:5555', '192.168.1.2', '192.168.1.2:0', '192.168.1.2:65536']:
            with self.assertRaises(ValueError):
                main.endpoint(value)
        self.assertEqual(main.services('name _adb-tls-connect._tcp. 192.168.3.4:5555\ninvalid'),
                         [('name', '_adb-tls-connect._tcp', '192.168.3.4:5555')])

    def test_quoted_android_qr_and_avahi(self):
        w = self.window
        w.show_qr()
        secret = '"' + w.qr_secret + '"'
        self.state(secret=secret, services=[])
        self.until(lambda: w.discovery_process is None)
        escaped = r'\034' + w.qr_name + r'\034'
        text = f'=;wlan0;IPv4;{escaped};_adb-tls-pairing._tcp;local;Android.local;192.168.3.4;40661;\n'
        self.assertEqual(main.services(text), [('"' + w.qr_name + '"', '_adb-tls-pairing._tcp', '192.168.3.4:40661')])
        # Exercise the exact resolved Avahi record from this phone.
        process = w.make_process()
        w.discovered(process, 'qr', text.encode())
        self.until(lambda: 'Экран и звук передаются' in w.status.text())
        self.assertIsNone(w.qr_secret)

    def test_changed_port_without_repairing(self):
        w = self.window
        self.state(bad_address='192.168.3.4:39000', services=[
            ['adb-test', '_adb-tls-connect._tcp', '192.168.3.4:39002']])
        w.address.setText('192.168.3.4:39000')
        w.start()
        self.until(lambda: 'Экран и звук передаются' in w.status.text())
        self.assertEqual(w.target, '192.168.3.4:39002')

    def test_stream_disconnect_recovers_to_new_port(self):
        w = self.window
        self.state(services=[])
        w.address.setText('192.168.3.4:39000')
        w.start()
        self.until(lambda: 'Экран и звук передаются' in w.status.text())
        old_video = w.video
        self.state(services=[['adb-test', '_adb-tls-connect._tcp', '192.168.3.4:39003']])
        old_video.kill()
        self.until(lambda: w.video is not None and w.video is not old_video and
                   'Экран и звук передаются' in w.status.text())
        self.assertEqual(w.target, '192.168.3.4:39003')
        w.stop()
        self.until(lambda: w.video is None)
        self.assertIsNone(w.discovery)

    def test_qr_refresh_cancels_old_pairing(self):
        w = self.window
        w.show_qr()
        old_generation = w.qr_generation
        old_name = w.qr_name
        self.state(secret=w.qr_secret, pair_delay=2, services=[
            [w.qr_name, '_adb-tls-pairing._tcp', '192.168.3.4:41235']])
        self.until(lambda: w.task is not None)
        old_task = w.task
        w.show_qr()
        self.assertIsNone(w.task)
        self.assertNotEqual(w.qr_name, old_name)
        self.assertTrue(w.qr_button.isEnabled())
        # Late completion from the cancelled QR must not close the new QR.
        w.qr_paired(True, 'Successfully paired', '192.168.3.4:41235', old_generation)
        self.assertTrue(w.qr_dialog.isVisible())
        self.assertEqual(w.discovery, 'qr')
        self.until(lambda: old_task.state() == main.QProcess.NotRunning)

    def test_discovery_ignores_old_generation(self):
        w = self.window
        w.begin_discovery('find')
        old_generation = w.discovery_generation
        w.begin_discovery('find')
        current_process = w.discovery_process
        obsolete = w.make_process()
        w.discovered(obsolete, 'find', b'old _adb-tls-connect._tcp 192.168.3.99:5555', old_generation)
        self.assertIs(w.discovery_process, current_process)
        self.assertEqual(w.address.text(), '')


if __name__ == '__main__':
    unittest.main()
