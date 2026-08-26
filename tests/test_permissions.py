import unittest

from apos.models import PermissionSpec
from apos.permissions import PermissionError, PermissionManager


class PermissionManagerTests(unittest.TestCase):
    def test_allows_authorized_patch_paths(self):
        manager = PermissionManager(PermissionSpec(read=["src/app.py"], write=["src/app.py"], execute=[]))

        changed = manager.validate_patch(
            "\n".join(
                [
                    "diff --git a/src/app.py b/src/app.py",
                    "--- a/src/app.py",
                    "+++ b/src/app.py",
                    "@@ -1 +1 @@",
                    "-old",
                    "+new",
                ]
            )
        )

        self.assertEqual(changed, ["src/app.py"])

    def test_rejects_unauthorized_patch_paths(self):
        manager = PermissionManager(PermissionSpec(read=["src/app.py"], write=["src/app.py"], execute=[]))

        with self.assertRaises(PermissionError):
            manager.validate_patch(
                "\n".join(
                    [
                        "diff --git a/src/secret.py b/src/secret.py",
                        "--- a/src/secret.py",
                        "+++ b/src/secret.py",
                        "@@ -1 +1 @@",
                        "-old",
                        "+new",
                    ]
                )
            )


if __name__ == "__main__":
    unittest.main()

