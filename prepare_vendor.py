"""Download official scrcpy binaries for running from a fresh checkout."""
import hashlib
from pathlib import Path
import platform
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION = '5.0.1'


def prepare(target=None):
    target = target or ('windows' if sys.platform == 'win32' else 'linux')
    if platform.machine().lower() not in ('x86_64', 'amd64'):
        raise SystemExit('Only x86_64 builds are supported.')
    if target not in ('windows', 'linux'):
        raise ValueError('Unsupported platform')
    name = f'scrcpy-win64-v{VERSION}.zip' if target == 'windows' else f'scrcpy-linux-x86_64-v{VERSION}.tar.gz'
    directory = ROOT / 'vendor' / name.removesuffix('.tar.gz').removesuffix('.zip')
    if (directory / ('scrcpy.exe' if target == 'windows' else 'scrcpy')).is_file():
        return directory
    base = f'https://github.com/Genymobile/scrcpy/releases/download/v{VERSION}/'
    with urllib.request.urlopen(base + 'SHA256SUMS.txt', timeout=30) as response:
        sums = response.read().decode()
    expected = next(line.split()[0] for line in sums.splitlines() if line.split()[-1].lstrip('*') == name)
    with tempfile.TemporaryDirectory() as temp:
        archive = Path(temp) / name
        urllib.request.urlretrieve(base + name, archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            raise ValueError('scrcpy checksum mismatch')
        (ROOT / 'vendor').mkdir(exist_ok=True)
        if target == 'windows':
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(ROOT / 'vendor')
        else:
            with tarfile.open(archive) as bundle:
                bundle.extractall(ROOT / 'vendor', filter='data')
    return directory


if __name__ == '__main__':
    print(prepare())
