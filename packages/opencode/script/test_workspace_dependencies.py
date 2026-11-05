import importlib.util
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("diagnose", Path(__file__).with_name("diagnose_file_search.py"))
DIAG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DIAG)


class WorkspaceDependencyTests(unittest.TestCase):
    def test_binary_proof_refuses_another_worktree_or_changed_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "expected"
            binary = root / "packages/core/src/ripgrep/binary.ts"
            binary.parent.mkdir(parents=True)
            binary.write_text("expected source")
            environment = {"binaryModule": str(binary), "binarySHA256": hashlib.sha256(binary.read_bytes()).hexdigest()}
            DIAG.verify_binary(root, environment)
            other = Path(tmp) / "another-binary.ts"
            other.write_bytes(binary.read_bytes())
            with self.assertRaisesRegex(DIAG.DiagnosticGuardError, "unexpected_binary_module"):
                DIAG.verify_binary(root, {**environment, "binaryModule": str(other)})
            binary.write_text("different source")
            with self.assertRaisesRegex(DIAG.DiagnosticGuardError, "binary_source_mismatch"):
                DIAG.verify_binary(root, environment)

    def test_native_sdk_import_fails_with_old_layout_and_resolves_with_declared_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "installed"
            tree = Path(tmp) / "isolated"
            sdk = root / "packages/sdk/js"
            dependency = sdk / "node_modules/cross-spawn"
            dependency.mkdir(parents=True)
            (root / "node_modules").mkdir()
            (root / "package.json").write_text(json.dumps({"workspaces": {"packages": ["packages/*", "packages/sdk/js"]}}))
            (sdk / "package.json").write_text('{"type":"module"}')
            (dependency / "package.json").write_text('{"name":"cross-spawn","type":"module","exports":"./index.js"}')
            (dependency / "index.js").write_text('export default "SDK_DEPENDENCY_RESOLVED"')
            target = tree / "packages/sdk/js"
            target.mkdir(parents=True)
            (target / "package.json").write_text('{"type":"module"}')
            (target / "server.js").write_text('import marker from "cross-spawn"; console.log(marker)')
            node = shutil.which("node")
            self.assertIsNotNone(node)
            # The production diagnostic's former packages/* glob misses sdk/js.
            self.assertEqual(list(root.glob("packages/*/node_modules")), [])
            before = subprocess.run([node, str(target / "server.js")], capture_output=True, text=True)
            self.assertNotEqual(before.returncode, 0)
            self.assertIn("cross-spawn", before.stderr)
            for source in DIAG.dependency_directories(root):
                DIAG.link_directory(tree / source.relative_to(root), source)
            after = subprocess.run([node, str(target / "server.js")], capture_output=True, text=True)
            self.assertEqual(after.returncode, 0, after.stderr)
            self.assertEqual(after.stdout.strip(), "SDK_DEPENDENCY_RESOLVED")

    def test_nested_sdk_dependencies_follow_declared_workspace_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(json.dumps({"workspaces": {"packages": ["packages/*", "packages/sdk/js", "packages/console/*"]}}))
            for name in ["packages/core", "packages/sdk/js", "packages/console/app", "packages/undeclared/deeper"]:
                package = root / name
                package.mkdir(parents=True)
                (package / "package.json").write_text("{}")
                (package / "node_modules").mkdir()
            (root / "node_modules").mkdir()
            paths = {str(p.relative_to(root)) for p in DIAG.dependency_directories(root)}
            self.assertEqual(paths, {"node_modules", "packages/core/node_modules", "packages/sdk/js/node_modules", "packages/console/app/node_modules"})

    def test_missing_or_external_workspace_is_refused_before_linking(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for patterns in [[], ["../outside"], ["/tmp/outside"]]:
                with self.subTest(patterns=patterns):
                    (root / "package.json").write_text(json.dumps({"workspaces": {"packages": patterns}}))
                    with self.assertRaises(DIAG.DiagnosticGuardError):
                        DIAG.dependency_directories(root)

    def test_declared_sdk_with_missing_dependencies_fails_before_creating_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "node_modules").mkdir()
            (root / "package.json").write_text(json.dumps({"workspaces": {"packages": ["packages/sdk/js"]}}))
            package = root / "packages/sdk/js"
            package.mkdir(parents=True)
            (package / "package.json").write_text('{"dependencies":{"cross-spawn":"1.0.0"}}')
            with self.assertRaisesRegex(DIAG.DiagnosticGuardError, "workspace_dependencies_missing"):
                DIAG.dependency_directories(root)
            target = root / "uncreated-link"
            with self.assertRaisesRegex(DIAG.DiagnosticGuardError, "dependency_source_missing"):
                DIAG.link_directory(target, package / "node_modules")
            self.assertFalse(target.exists())
            self.assertFalse(target.is_symlink())

    def test_workspace_symlink_outside_root_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            outside = Path(tmp) / "outside"
            (root / "node_modules").mkdir(parents=True)
            (root / "packages").mkdir()
            outside.mkdir()
            (outside / "package.json").write_text('{}')
            (root / "package.json").write_text(json.dumps({"workspaces": {"packages": ["packages/*"]}}))
            (root / "packages/sdk").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(DIAG.DiagnosticGuardError, "workspace_package_external"):
                DIAG.dependency_directories(root)


if __name__ == "__main__":
    unittest.main()
