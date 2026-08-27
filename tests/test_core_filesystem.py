import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from apos.core import (
    Actor,
    ActorKind,
    AuditLog,
    AuthorizationService,
    Capability,
    Decision,
    ErrorCode,
    FileSystemService,
    PermissionEngine,
    ProjectWorkspace,
    StaticPermissionPolicy,
    ToolResult,
)


class ToolResultTests(unittest.TestCase):
    def test_result_envelope_is_stable(self):
        result = ToolResult.fail(ErrorCode.PERMISSION_REQUIRED, "approval needed", details={"capability": "network"})

        self.assertEqual(
            result.to_dict(),
            {
                "success": False,
                "data": None,
                "error": {
                    "code": "PERMISSION_REQUIRED",
                    "message": "approval needed",
                    "details": {"capability": "network"},
                },
                "meta": {},
            },
        )


class ProjectFileSystemTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
        (self.root / ".apos").mkdir()
        (self.root / ".apos" / "config.json").write_text("{}\n", encoding="utf-8")
        self.workspace = ProjectWorkspace.register(self.root)
        self.actor = Actor(ActorKind.EXTERNAL_AI, "test-agent")
        policy = StaticPermissionPolicy(
            {Capability.PROJECT_READ: Decision.ALLOW, Capability.PROJECT_WRITE: Decision.ALLOW}
        )
        authorization = AuthorizationService(PermissionEngine(policy), AuditLog(self.workspace))
        self.files = FileSystemService(self.workspace, authorization)

    def tearDown(self):
        self.temporary.cleanup()

    def test_lists_project_files_without_secret_or_internal_paths(self):
        result = self.files.list_files(recursive=True, actor=self.actor)

        self.assertTrue(result.success, result.to_dict())
        paths = [entry["path"] for entry in result.data["entries"]]
        self.assertIn("src/app.py", paths)
        self.assertNotIn(".env", paths)
        self.assertFalse(any(path.startswith(".apos") for path in paths))

    def test_reads_project_file_with_project_metadata(self):
        result = self.files.read_file("src/app.py", actor=self.actor)

        self.assertTrue(result.success, result.to_dict())
        self.assertEqual(result.data["content"], "VALUE = 1\n")
        self.assertEqual(result.meta["project_id"], self.workspace.project_id)

    def test_rejects_absolute_and_traversal_paths(self):
        absolute = self.files.read_file(str((self.root / "src" / "app.py").resolve()), actor=self.actor)
        traversal = self.files.read_file("../outside.txt", actor=self.actor)

        self.assertEqual(absolute.error.code, ErrorCode.PATH_OUTSIDE_PROJECT)
        self.assertEqual(traversal.error.code, ErrorCode.PATH_OUTSIDE_PROJECT)

    def test_denies_secret_paths_by_default(self):
        env_result = self.files.read_file(".env", actor=self.actor)
        internal_result = self.files.read_file(".apos/config.json", actor=self.actor)
        key_result = self.files.write_file("src/private.key", "secret", actor=self.actor)

        self.assertEqual(env_result.error.code, ErrorCode.SECRET_PATH_DENIED)
        self.assertEqual(internal_result.error.code, ErrorCode.SECRET_PATH_DENIED)
        self.assertEqual(key_result.error.code, ErrorCode.SECRET_PATH_DENIED)

    def test_writes_atomically_inside_project(self):
        result = self.files.write_file("src/app.py", "VALUE = 2\n", actor=self.actor)

        self.assertTrue(result.success, result.to_dict())
        self.assertTrue(result.data["atomic"])
        self.assertEqual((self.root / "src" / "app.py").read_text(encoding="utf-8"), "VALUE = 2\n")
        self.assertEqual(list((self.root / "src").glob("*.tmp")), [])

    def test_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as outside_tmp:
            outside = Path(outside_tmp)
            (outside / "secret.txt").write_text("outside\n", encoding="utf-8")
            link = self.root / "outside-link"
            self._create_directory_link(link, outside)

            read_result = self.files.read_file("outside-link/secret.txt", actor=self.actor)
            write_result = self.files.write_file("outside-link/new.txt", "blocked", actor=self.actor)
            list_result = self.files.list_files(recursive=True, actor=self.actor)

            self.assertEqual(read_result.error.code, ErrorCode.PATH_OUTSIDE_PROJECT)
            self.assertEqual(write_result.error.code, ErrorCode.PATH_OUTSIDE_PROJECT)
            self.assertFalse((outside / "new.txt").exists())
            listed = {entry["path"]: entry for entry in list_result.data["entries"]}
            self.assertFalse(listed["outside-link"]["accessible"])
            self.assertNotIn("outside-link/secret.txt", listed)

    def test_denies_link_alias_to_internal_secret_directory(self):
        link = self.root / "public-config"
        self._create_directory_link(link, self.root / ".apos")

        read_result = self.files.read_file("public-config/config.json", actor=self.actor)
        list_result = self.files.list_files(recursive=True, actor=self.actor)

        self.assertEqual(read_result.error.code, ErrorCode.SECRET_PATH_DENIED)
        listed_paths = [entry["path"] for entry in list_result.data["entries"]]
        self.assertNotIn("public-config", listed_paths)
        self.assertNotIn("public-config/config.json", listed_paths)

    def _create_directory_link(self, link: Path, target: Path) -> None:
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            if os.name != "nt":
                self.skipTest(f"symlink creation is unavailable: {exc}")
            junction = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                text=True,
                capture_output=True,
            )
            if junction.returncode != 0:
                self.skipTest(f"symlink and junction creation are unavailable: {exc}")


if __name__ == "__main__":
    unittest.main()
