import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import main


class PortabilityTest(unittest.TestCase):
    def test_windows_executable_and_writable_log_path(self):
        with tempfile.TemporaryDirectory(prefix='phone screen ') as folder:
            with patch.object(main, 'IS_WINDOWS', True), patch.object(main, 'VENDOR', Path(folder)), \
                    patch.dict(os.environ, {'LOCALAPPDATA': folder}):
                self.assertEqual(main.binary('adb'), str(Path(folder) / 'adb.exe'))
                self.assertEqual(main.binary('scrcpy'), str(Path(folder) / 'scrcpy.exe'))
                log = main.connection_log()
                log.write_text('Проверка', encoding='utf-8')
                self.assertEqual(log, Path(folder) / 'PhoneScreen/connection.log')
                self.assertEqual(log.read_text(encoding='utf-8'), 'Проверка')


if __name__ == '__main__':
    unittest.main()
