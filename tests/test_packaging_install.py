import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .helpers import PROJECT_ROOT

TOOLS = PROJECT_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from build_distributions import _ignored_tree_path, build, load_config, validate_package
from build_release import build_release
from intentrail_core.errors import InstallationError
from intentrail_core.installer import doctor, install_or_upgrade, resolve_cli_path, target_plan, uninstall


class PackagingInstallTests(unittest.TestCase):
    def test_release_builder_emits_only_publishable_assets_and_checksums(self):
        with tempfile.TemporaryDirectory(dir=str(PROJECT_ROOT)) as temporary:
            output = Path(temporary) / "release"
            result = build_release(output)
            expected = {
                "intentrail-0.5.0-py3-none-any.whl",
                "intentrail-0.5.0.tar.gz",
                "intentrail-claude-code-0.5.0.zip",
                "intentrail-codex-0.5.0.zip",
                "intentrail-copilot-cli-0.5.0.zip",
                "intentrail-generic-agent-skills-0.5.0.zip",
                "release-manifest.json",
                "SHA256SUMS.txt",
            }
            self.assertEqual({path.name for path in output.iterdir()}, expected)
            self.assertTrue(all(path.is_file() for path in output.iterdir()))
            self.assertEqual({item["name"] for item in result["assets"]}, expected)
            checksums = (output / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(checksums), 7)
            manifest = json.loads((output / "release-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["product_version"], "0.5.0")
            self.assertEqual(len(manifest["artifacts"]), 6)
            self.assertTrue(manifest["canonical_content_hash"])
            self.assertTrue(manifest["runtime_source_hash"])

    def test_all_packages_are_generated_from_one_canonical_hash(self):
        with tempfile.TemporaryDirectory(dir=str(PROJECT_ROOT)) as temporary:
            output = Path(temporary) / "dist"
            result = build(output, create_archives=False)
            self.assertEqual(len(result["packages"]), 4)
            config = load_config()
            for package in result["packages"]:
                checked = validate_package(
                    package["directory"],
                    package["host"],
                    config,
                    result["canonical_content_hash"],
                    result["runtime_source_hash"],
                )
                self.assertTrue(checked["valid"])
                self.assertEqual(checked["runtime_source_hash"], result["runtime_source_hash"])
                polluted = [
                    path for path in Path(package["directory"]).rglob("*")
                    if path.is_file() and _ignored_tree_path(path, Path(package["directory"]))
                ]
                self.assertEqual(polluted, [])

    def test_marketplace_packages_use_bootstrap_not_system_python(self):
        with tempfile.TemporaryDirectory(dir=str(PROJECT_ROOT)) as temporary:
            result = build(Path(temporary) / "dist", create_archives=False)
            for package in result["packages"]:
                if package["host"] == "generic-agent-skills":
                    continue
                root = Path(package["directory"])
                self.assertTrue((root / "scripts" / "intentrail_bootstrap.py").exists())
                self.assertTrue((root / "skills" / "intentrail" / "scripts" / "intentrail_core" / "cli.py").exists())
                hook_text = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.json") if "hook" in path.name.lower() or "hooks" in path.parts)
                self.assertNotIn("python3 ", hook_text)
                self.assertNotIn("python ", hook_text)
                self.assertNotIn("py -3 ", hook_text)
                self.assertNotIn("{{INTENTRAIL_COMMAND", hook_text)

    def test_public_skills_are_thin_and_release_packages_receive_the_runtime(self):
        self.assertFalse((PROJECT_ROOT / "skills" / "intentrail" / "scripts" / "intentrail_core").exists())
        self.assertTrue((PROJECT_ROOT / "src" / "intentrail_core" / "cli.py").exists())
        with tempfile.TemporaryDirectory(dir=str(PROJECT_ROOT)) as temporary:
            result = build(Path(temporary) / "dist", create_archives=False)
            for package in result["packages"]:
                runtime = Path(package["directory"]) / "skills" / "intentrail" / "scripts" / "intentrail_core"
                self.assertTrue((runtime / "cli.py").is_file())
                self.assertFalse((runtime / "runtime_bundle.json").exists())

    def test_bundled_bootstrap_executes_without_source_tree_imports(self):
        with tempfile.TemporaryDirectory(dir=str(PROJECT_ROOT)) as temporary, tempfile.TemporaryDirectory() as repo_temp:
            result = build(Path(temporary) / "dist", create_archives=False)
            repo = Path(repo_temp)
            marker = repo / "untrusted-locator-ran"
            malicious = repo / ("malicious.cmd" if os.name == "nt" else "malicious")
            if os.name == "nt":
                malicious.write_text('@echo ran>"{0}"\r\n@echo {{}}\r\n'.format(marker), encoding="utf-8")
            else:
                malicious.write_text('#!/bin/sh\nprintf ran > "{0}"\nprintf "{{}}\\n"\n'.format(marker), encoding="utf-8")
                malicious.chmod(malicious.stat().st_mode | stat.S_IXUSR)
            (repo / ".intentrail-cli").write_text(str(malicious) + "\n", encoding="utf-8")
            for package in result["packages"]:
                if package["host"] == "generic-agent-skills":
                    continue
                bootstrap = Path(package["directory"]) / "scripts" / "intentrail_bootstrap.py"
                completed = subprocess.run(
                    [sys.executable, str(bootstrap), "hook", "--host", package["host"], "--event", "PreToolUse"],
                    input=json.dumps({"session_id": "dormant", "cwd": repo_temp, "tool_name": "Read", "tool_input": {"path": "README.md"}}),
                    text=True,
                    capture_output=True,
                    cwd=repo_temp,
                    timeout=10,
                    env={**os.environ, "PATH": ""},
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(json.loads(completed.stdout), {})
            self.assertFalse(marker.exists(), "Marketplace bootstrap trusted a repository-controlled CLI locator")

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "Windows PowerShell bootstrap test")
    def test_windows_marketplace_launcher_forwards_stdin_and_uses_locator(self):
        with tempfile.TemporaryDirectory(dir=str(PROJECT_ROOT)) as temporary, tempfile.TemporaryDirectory() as repo_temp:
            result = build(Path(temporary) / "dist", create_archives=False)
            package = next(item for item in result["packages"] if item["host"] == "codex")
            repo = Path(repo_temp)
            cli = _managed_cli(repo)
            data_root = repo / "appdata"
            locator = data_root / "IntentRail" / "cli-path.txt"
            locator.parent.mkdir(parents=True)
            locator.write_text(str(cli) + "\n", encoding="utf-8")
            script = Path(package["directory"]) / "scripts" / "intentrail_bootstrap.ps1"
            completed = subprocess.run(
                [shutil.which("powershell"), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "hook", "--host", "codex", "--event", "PreToolUse"],
                input=json.dumps({"session_id": "gui-path", "cwd": str(repo), "tool_name": "Read", "tool_input": {"path": "README.md"}}),
                text=True,
                capture_output=True,
                cwd=repo,
                timeout=15,
                env={**os.environ, "PATH": "", "LOCALAPPDATA": str(data_root)},
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout), {})

    def test_repo_install_writes_absolute_cli_runs_doctor_and_preserves_state(self):
        with tempfile.TemporaryDirectory(dir=str(PROJECT_ROOT)) as build_temp, tempfile.TemporaryDirectory() as repo_temp:
            build(Path(build_temp) / "dist", create_archives=False)
            repo = Path(repo_temp)
            cli = _managed_cli(repo)
            state = repo / ".intentrail"
            state.mkdir()
            sentinel = state / "do-not-delete.json"
            sentinel.write_text("{}", encoding="utf-8")
            hosts = ["codex", "claude-code", "copilot-cli", "generic-agent-skills"]
            preview = install_or_upgrade("install", hosts, "repo", repo, cli, dry_run=True)
            self.assertTrue(any(item["state"] == "create" for item in preview["operations"]))
            installed = install_or_upgrade("install", hosts, "repo", repo, cli)
            self.assertTrue(Path(installed["manifest"]).exists())
            self.assertEqual(Path(installed["cli_path"]), cli.resolve())
            repeated = install_or_upgrade("install", hosts, "repo", repo, cli, dry_run=True)
            self.assertTrue(all(item["state"] == "preserve-identical" for item in repeated["operations"]))
            report = doctor("auto", "repo", repo, cli_path=cli)
            self.assertTrue(all(item["support"] != "Unsupported" for item in report["hosts"]), report)
            with patch.dict(os.environ, {"PATH": ""}):
                gui_report = doctor("auto", "repo", repo, cli_path=cli)
                upgraded = install_or_upgrade("upgrade", "auto", "repo", repo, cli)
            self.assertTrue(all(item["support"] != "Unsupported" for item in gui_report["hosts"]), gui_report)
            self.assertTrue(all(item["support"] != "Unsupported" for item in upgraded["doctor"]["hosts"]), upgraded)
            hook = json.loads((repo / "plugins" / "intentrail" / "hooks" / "hooks.json").read_text(encoding="utf-8"))
            command = hook["hooks"]["PreToolUse"][0]["hooks"][0]["commandWindows" if os.name == "nt" else "command"]
            self.assertIn('"{0}" hook --host codex'.format(cli.resolve().as_posix()), command)
            self.assertNotIn("python", command.lower())
            manifest = json.loads(Path(installed["manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], "1.0.0")
            self.assertEqual(manifest["runtime_backend"], "managed-cli")
            self.assertEqual(Path(manifest["cli_path"]), cli.resolve())
            uninstall(hosts, "repo", repo)
            self.assertTrue(sentinel.exists())
            self.assertFalse((repo / "plugins" / "intentrail").exists())

    def test_failed_doctor_rolls_back_files_manifest_and_locator(self):
        with tempfile.TemporaryDirectory() as repo_temp:
            repo = Path(repo_temp)
            bad_cli = repo / ("intentrail.cmd" if os.name == "nt" else "intentrail")
            bad_cli.write_text("@exit /b 7\r\n" if os.name == "nt" else "#!/bin/sh\nexit 7\n", encoding="utf-8")
            if os.name != "nt":
                bad_cli.chmod(bad_cli.stat().st_mode | stat.S_IXUSR)
            with self.assertRaises(InstallationError):
                install_or_upgrade("install", ["codex"], "repo", repo, bad_cli)
            self.assertFalse((repo / "plugins" / "intentrail").exists())
            self.assertFalse((repo / ".intentrail-install.json").exists())
            self.assertFalse((repo / ".intentrail-cli").exists())

    def test_python_interpreter_cannot_be_bound_as_the_managed_cli(self):
        with self.assertRaises(InstallationError):
            resolve_cli_path(sys.executable)

    def test_installer_stops_on_unowned_collision(self):
        with tempfile.TemporaryDirectory() as repo_temp:
            repo = Path(repo_temp)
            cli = _managed_cli(repo)
            collision = repo / ".claude" / "skills" / "intentrail"
            collision.mkdir(parents=True)
            (collision / "SKILL.md").write_text("user-owned", encoding="utf-8")
            with self.assertRaises(Exception):
                install_or_upgrade("install", ["claude-code"], "repo", repo, cli)

    def test_codex_user_marketplace_paths_follow_plugin_contract(self):
        with tempfile.TemporaryDirectory() as home_temp, tempfile.TemporaryDirectory() as package_temp:
            home = Path(home_temp).resolve()
            with patch("intentrail_core.installer.user_home", return_value=home):
                plan = target_plan("codex", "user", home, Path(package_temp))
            self.assertEqual(plan["copies"][0][1], home / "plugins" / "intentrail")
            self.assertEqual(plan["configs"][0][1], home / ".agents" / "plugins" / "marketplace.json")
            self.assertEqual(plan["configs"][0][2], "./plugins/intentrail")

    def test_forged_manifest_cannot_escape_owned_install_paths(self):
        with tempfile.TemporaryDirectory() as repo_temp:
            repo = Path(repo_temp)
            cli = _managed_cli(repo)
            installed = install_or_upgrade("install", ["codex"], "repo", repo, cli)
            manifest_path = Path(installed["manifest"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            victim = repo / "victim"
            victim.mkdir()
            (victim / ".intentrail-owned.json").write_text("{}", encoding="utf-8")
            (victim / "keep.txt").write_text("user data", encoding="utf-8")
            manifest["installed"][0]["path"] = str(victim)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(InstallationError):
                uninstall(["codex"], "repo", repo)
            self.assertTrue((victim / "keep.txt").is_file())


def _managed_cli(directory):
    directory = Path(directory)
    source_cli = PROJECT_ROOT / "skills" / "intentrail" / "scripts" / "intentrail.py"
    if os.name == "nt":
        path = directory / "intentrail.cmd"
        path.write_text('@echo off\r\n"{0}" "{1}" %*\r\n'.format(sys.executable, source_cli), encoding="utf-8")
    else:
        path = directory / "intentrail"
        path.write_text('#!/bin/sh\nexec "{0}" "{1}" "$@"\n'.format(sys.executable, source_cli), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path.resolve()
