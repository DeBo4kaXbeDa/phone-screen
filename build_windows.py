"""Build a self-contained Windows x64 folder on Linux using Wine.

Downloads official Python, PyPI wheels and the official scrcpy release. PyPI
and scrcpy downloads are verified against their published SHA-256 digests.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parent
CACHE = Path.home() / '.cache' / 'phone-screen-build'
DOWNLOADS = CACHE / 'downloads'
RUNTIME = CACHE / 'python'
PACKAGES = CACHE / 'site-packages'
VERSIONS = {
    'PySide6-Essentials': '6.12.0', 'shiboken6': '6.12.0', 'qrcode': '8.2',
    'zeroconf': '0.154.0', 'ifaddr': '0.2.0', 'async-timeout': '5.0.1',
    'pyinstaller': '6.22.3', 'pyinstaller-hooks-contrib': '2026.9',
    'altgraph': '0.17.5', 'pefile': '2024.8.26', 'pywin32-ctypes': '0.2.3',
    'packaging': '26.3',
    'setuptools': '84.2.1', 'colorama': '0.4.6',
}


def download(url, expected=None):
    path = DOWNLOADS / url.rsplit('/', 1)[-1]
    if not path.exists():
        temporary = path.with_suffix(path.suffix + '.part')
        urllib.request.urlretrieve(url, temporary)
        temporary.replace(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if expected and digest != expected:
        raise ValueError('Checksum mismatch: ' + path.name)
    return path, digest


def wheel(item):
    package, version = item
    with urllib.request.urlopen(f'https://pypi.org/pypi/{package}/{version}/json') as response:
        metadata = json.load(response)
    candidates = [entry for entry in metadata['urls'] if entry['filename'].endswith('.whl') and
                  ('none-any.whl' in entry['filename'] or
                   ('win_amd64.whl' in entry['filename'] and any(
                       tag in entry['filename'] for tag in ('abi3', 'cp313-cp313', 'py3-none'))))]
    if len(candidates) != 1:
        raise ValueError(f'Ambiguous wheel for {package}: {candidates}')
    entry = candidates[0]
    path, digest = download(entry['url'], entry['digests']['sha256'])
    with zipfile.ZipFile(path) as archive:
        archive.extractall(PACKAGES)
    if package == 'qrcode':
        with zipfile.ZipFile(path) as archive:
            archive.extractall(ROOT / 'vendor' / 'python')
    return {'package': package, 'version': version, 'file': path.name, 'sha256': digest, 'url': entry['url']}


def winpath(path):
    return 'Z:' + str(Path(path).resolve()).replace('/', '\\')


def build():
    for folder in (CACHE, DOWNLOADS, RUNTIME, PACKAGES):
        folder.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=5) as executor:
        provenance = list(executor.map(wheel, VERSIONS.items()))
    print('Verified Windows dependencies', flush=True)
    url = 'https://www.python.org/ftp/python/3.13.9/python-3.13.9-embed-amd64.zip'
    python, digest = download(url)
    provenance.append({'package': 'Python', 'version': '3.13.9', 'url': url, 'sha256': digest})
    with zipfile.ZipFile(python) as archive:
        archive.extractall(RUNTIME)
    (RUNTIME / 'python313._pth').write_text(
        'python313.zip\n.\n../site-packages\n' + winpath(ROOT) + '\nimport site\n')
    base = 'https://github.com/Genymobile/scrcpy/releases/download/v5.0.1/'
    filename = 'scrcpy-win64-v5.0.1.zip'
    with urllib.request.urlopen(base + 'SHA256SUMS.txt') as response:
        sums = response.read().decode()
    expected = next(line.split()[0] for line in sums.splitlines() if line.split()[-1].lstrip('*') == filename)
    scrcpy, digest = download(base + filename, expected)
    with zipfile.ZipFile(scrcpy) as archive:
        archive.extractall(ROOT / 'vendor')
    provenance.append({'package': 'scrcpy', 'version': '5.0.1', 'url': base + filename, 'sha256': digest})
    from PIL import Image
    Image.open(ROOT / 'vendor/scrcpy-win64-v5.0.1/scrcpy.png').save(CACHE / 'phone-screen.ico')
    env = os.environ.copy()
    env.update(WINEPREFIX=str(CACHE / 'wine'), WINEDEBUG='-all', WINEDLLOVERRIDES='mscoree,mshtml=')
    command = [
        'wine', str(RUNTIME / 'python.exe'), '-m', 'PyInstaller',
        '--noconfirm', '--clean', '--windowed', '--onedir', '--name', 'PhoneScreen',
        '--distpath', winpath(ROOT / 'dist'), '--workpath', winpath(CACHE / 'work'),
        '--specpath', winpath(CACHE), '--icon', winpath(CACHE / 'phone-screen.ico'),
        '--paths', winpath(ROOT), '--paths', winpath(ROOT / 'vendor/python'),
        '--collect-submodules', 'zeroconf',
        '--add-data', winpath(ROOT / 'vendor/scrcpy-win64-v5.0.1') + ';vendor/scrcpy-win64-v5.0.1',
        winpath(ROOT / 'main.py'),
    ]
    print('Building native Windows executable under Wine', flush=True)
    subprocess.run(command, env=env, cwd=ROOT, check=True)
    distribution = ROOT / 'dist/PhoneScreen'
    shutil.copy2(ROOT / 'WINDOWS_README.txt', distribution / 'ПРОЧИТАЙ МЕНЯ.txt')
    shutil.copy2(ROOT / 'CreateShortcut.vbs', distribution / 'Создать ярлык.vbs')
    licenses = distribution / 'licenses'
    licenses.mkdir(exist_ok=True)
    for package in PACKAGES.glob('*.dist-info'):
        for source in package.rglob('*'):
            if source.is_file() and any(key in str(source.relative_to(package)).lower()
                                        for key in ('license', 'copying', 'notice')):
                target = licenses / package.name / source.relative_to(package)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    shutil.copy2(RUNTIME / 'LICENSE.txt', licenses / 'Python-LICENSE.txt')
    (distribution / 'components.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
    archive_path = ROOT / 'dist/PhoneScreen-Windows-x64.zip'
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for source in sorted(distribution.rglob('*')):
            if source.is_file():
                archive.write(source, source.relative_to(distribution.parent))
    print('Built:', archive_path, flush=True)


if __name__ == '__main__':
    build()
