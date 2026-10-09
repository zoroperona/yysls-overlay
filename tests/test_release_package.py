from pathlib import Path
import tempfile
import unittest
import zipfile

from reminder_catalog import load_catalog
from scripts.build_release import build, release_files


ROOT = Path(__file__).resolve().parents[1]


class ReleasePackageTest(unittest.TestCase):
    def test_archive_is_complete_and_does_not_recurse_into_local_materials(self):
        with tempfile.TemporaryDirectory() as temp:
            # Only copy the allowlisted inputs, then add unrelated local files.
            import shutil
            root = Path(temp)/'project'
            root.mkdir()
            for name in release_files(ROOT):
                dest = root/name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT/name, dest)
            for name in ('assets/icons/private-original.png', 'assets/audio/personal.wav',
                         'config.json', 'screenshots/account.png', '.env', 'private.key'):
                dest = root/name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text('must not ship')
            package = build(root)
            with zipfile.ZipFile(package) as archive:
                names = {n.removeprefix('yysls-overlay/') for n in archive.namelist()}
                self.assertEqual(names, set(release_files(root)))
                self.assertTrue({s.icon for s in load_catalog(root)} <= names)
                self.assertFalse(any('private' in n or 'personal' in n or 'screenshots' in n for n in names))
                self.assertNotIn('config.json', names)
                self.assertNotIn('.env', names)


if __name__ == '__main__':
    unittest.main()
