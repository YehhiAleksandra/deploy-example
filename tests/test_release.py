import io
import tarfile
import tempfile
import unittest
from pathlib import Path

import release


def make_site(root: Path, text: str) -> Path:
    site = root / "site"
    site.mkdir()
    (site / "index.html").write_text(f"<h1>{text}</h1>", encoding="utf-8")
    return site


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.root = Path(self.workspace.name)
        self.target = self.root / "app"
        self.site = make_site(self.root, "v1")

    def tearDown(self):
        self.workspace.cleanup()

    def deploy(self, version: str, keep: int = 3):
        built = release.build(self.site, self.root / "build", version)
        return release.deploy(
            release.argparse.Namespace(
                target=str(self.target),
                artifact=built["artifact"],
                sha256=built["sha256"],
                version=version,
                keep=keep,
                health_file="public/index.html",
            )
        )

    def test_deploy_switches_current_and_sets_previous(self):
        result = self.deploy("v1")
        self.assertIsNone(result["previous"])
        self.assertEqual(release.resolve_current(self.target).name, "v1")
        self.assertTrue((self.target / "releases/v1/public/index.html").is_file())
        self.deploy("v2")
        self.assertEqual(release.resolve_current(self.target).name, "v2")
        self.assertEqual(release.read_pointer(self.target, "previous"), "v1")

    def test_rollback_swaps_current_and_previous(self):
        self.deploy("v1")
        self.deploy("v2")
        rolled = release.rollback(
            release.argparse.Namespace(target=str(self.target))
        )
        self.assertEqual(rolled["current"], str(self.target / "releases/v1"))
        self.assertEqual(release.resolve_current(self.target).name, "v1")
        release.rollback(release.argparse.Namespace(target=str(self.target)))
        self.assertEqual(release.resolve_current(self.target).name, "v2")

    def test_checksum_mismatch_is_refused(self):
        built = release.build(self.site, self.root / "build", "v1")
        with self.assertRaises(SystemExit):
            release.deploy(
                release.argparse.Namespace(
                    target=str(self.target),
                    artifact=built["artifact"],
                    sha256="0" * 64,
                    version="v1",
                    keep=3,
                    health_file="public/index.html",
                )
            )

    def test_missing_health_file_keeps_current(self):
        self.deploy("v1")
        broken = self.root / "broken"
        (broken / "public").mkdir(parents=True)
        (broken / "public/other.html").write_text("x", encoding="utf-8")
        artifact = self.root / "broken.tar"
        with tarfile.open(artifact, "w") as archive:
            archive.add(broken / "public", arcname="public")
        with self.assertRaises(SystemExit):
            release.deploy(
                release.argparse.Namespace(
                    target=str(self.target),
                    artifact=str(artifact),
                    sha256=release.sha256_file(artifact),
                    version="v2",
                    keep=3,
                    health_file="public/index.html",
                )
            )
        self.assertEqual(release.resolve_current(self.target).name, "v1")
        self.assertFalse((self.target / "releases/v2").exists())
        self.assertEqual(
            [path.name for path in self.target.iterdir() if path.name.startswith(".staging-")],
            [],
        )

    def test_archive_cannot_escape_release_directory(self):
        artifact = self.root / "evil.tar"
        with tarfile.open(artifact, "w") as archive:
            payload = b"owned"
            info = tarfile.TarInfo("../owned.txt")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        with self.assertRaises(SystemExit):
            release.deploy(
                release.argparse.Namespace(
                    target=str(self.target),
                    artifact=str(artifact),
                    sha256=release.sha256_file(artifact),
                    version="evil",
                    keep=3,
                    health_file="",
                )
            )
        self.assertFalse((self.root / "owned.txt").exists())

    def test_prune_keeps_newest_releases(self):
        for version in ("v1", "v2", "v3"):
            self.deploy(version, keep=2)
        remaining = sorted(p.name for p in (self.target / "releases").iterdir())
        self.assertEqual(remaining, ["v2", "v3"])
        self.assertEqual(release.resolve_current(self.target).name, "v3")


if __name__ == "__main__":
    unittest.main()
