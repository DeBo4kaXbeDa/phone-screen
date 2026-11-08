"""Build the Linux x86_64 release in a Debian 12 container (glibc 2.36)."""
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import tarfile

ROOT = Path(__file__).resolve().parent


def inside():
    subprocess.run([sys.executable, '-m', 'unittest', '-v'], cwd=ROOT, check=True)
    vendor = ROOT / 'vendor/scrcpy-linux-x86_64-v5.0.1'
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
        '--name', 'PhoneScreen', '--distpath', str(ROOT / 'dist/linux'),
        '--workpath', '/tmp/phone-screen-work', '--specpath', '/tmp',
        '--collect-submodules', 'zeroconf',
        '--add-data', f'{vendor}:vendor/{vendor.name}',
        '--add-binary', f'{vendor / "scrcpy"}:vendor/{vendor.name}',
        str(ROOT / 'main.py'),
    ], cwd=ROOT, check=True)
    distribution = ROOT / 'dist/linux/PhoneScreen'
    shutil.copy2(ROOT / 'LINUX_README.txt', distribution / 'ПРОЧИТАЙ МЕНЯ.txt')
    shutil.copy2(ROOT / 'install-shortcut.sh', distribution / 'install-shortcut.sh')
    (distribution / 'install-shortcut.sh').chmod(0o755)
    licenses = distribution / 'licenses'
    licenses.mkdir(exist_ok=True)
    python_license = Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copy2(python_license, licenses / 'Python-LICENSE.txt')
    packages = []
    for package in importlib.metadata.distributions():
        packages.append({'name': package.metadata['Name'], 'version': package.version})
        for file in package.files or []:
            if '.dist-info/' not in str(file) or not any(key in str(file).lower() for key in ('license', 'copying', 'notice')):
                continue
            source = Path(package.locate_file(file))
            if source.is_file():
                destination = licenses / str(file)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
    provenance = {'python': platform.python_version(), 'glibc': platform.libc_ver()[1],
                  'scrcpy': '5.0.1', 'packages': sorted(packages, key=lambda entry: entry['name'].lower())}
    (distribution / 'components.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
    archive = ROOT / 'dist/PhoneScreen-Linux-x64.tar.gz'
    def neutral_owner(info):
        info.uid = info.gid = 0
        info.uname = info.gname = ''
        return info
    with tarfile.open(archive, 'w:gz') as bundle:
        bundle.add(distribution, arcname='PhoneScreen', filter=neutral_owner)
    print('Built:', archive, flush=True)


def build():
    if platform.machine().lower() not in ('x86_64', 'amd64'):
        raise SystemExit('An x86_64 Linux host is required.')
    from prepare_vendor import prepare
    prepare('linux')
    image = 'phone-screen-builder:1.0.0'
    subprocess.run(['docker', 'build', '-f', 'Dockerfile.linux', '-t', image, '.'], cwd=ROOT, check=True)
    subprocess.run([
        'docker', 'run', '--rm', '--user', f'{os.getuid()}:{os.getgid()}',
        '-e', 'HOME=/tmp', '-e', 'QT_QPA_PLATFORM=offscreen',
        '-v', f'{ROOT}:/src', image, 'python', 'build_linux.py', '--inside',
    ], cwd=ROOT, check=True)


if __name__ == '__main__':
    inside() if '--inside' in sys.argv else build()
