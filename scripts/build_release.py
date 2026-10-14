"""Build a runtime-only ZIP from explicit files and validated catalog references."""
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reminder_catalog import load_catalog


def release_files(root=ROOT):
    fixed = [
        'README.md', 'LICENSE', 'ASSETS.md', 'docs/reminder-controls.md',
        'skills.json', 'requirements.txt', 'start-windows.bat',
        'buff_reminder_v2_dizi_hotkey.py', 'reminder_assets.py',
        'reminder_capture.py', 'reminder_presentation.py', 'reminder_timing.py',
        'reminder_catalog.py', 'reminder_catalog_ui.py',
        'yysls_export/frame_debuff.png', 'yysls_export/debuff_shihunluopo.png',
        'yysls_export/dizi.png',
    ]
    fixed += [f'assets/audio/{kind}_{preset}.wav'
              for kind in ('loss', 'dizi') for preset in ('baidu', 'electronic')]
    files = sorted(set(fixed + [s.icon for s in load_catalog(root)]))
    for name in files:
        path = (root/name).resolve()
        if root.resolve() not in path.parents or not path.is_file():
            raise ValueError('Missing or external release file: '+name)
    return files


def build(root=ROOT, output=None):
    output = output or root/'dist/yysls-overlay.zip'
    files = release_files(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(root/name, 'yysls-overlay/'+name)
    return output


if __name__ == '__main__':
    print(build())
