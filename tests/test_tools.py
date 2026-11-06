"""Tests for tools/check_imports.py and tools/promptlog.py (Phase 0)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tools import check_imports

ROOT = check_imports.repo_root()
WORK = os.path.join(ROOT, ".torture-work")  # real disk, inside the repo, never /tmp


def make_tree(files):
    """Create a throwaway repository-like folder with the given {relpath: source}."""
    os.makedirs(WORK, exist_ok=True)
    root = tempfile.mkdtemp(prefix="test-tools-", dir=WORK)
    for rel, source in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(source)
    return root


class CheckImportsTest(unittest.TestCase):
    def tearDown(self):
        if getattr(self, "root", None):
            shutil.rmtree(self.root, ignore_errors=True)

    def problems(self, files):
        self.root = make_tree(files)
        return check_imports.check(self.root)

    def test_real_repository_is_ok(self):
        self.assertEqual(check_imports.check(ROOT), [])

    def test_stdlib_and_own_imports_are_allowed(self):
        self.assertEqual(self.problems({
            "a.py": "import os, struct, zlib\nfrom concurrent.futures import ThreadPoolExecutor\n",
            "tester/oracle.py": "from tester import workload\nfrom . import modes\n",
            "tester/__init__.py": "",
        }), [])

    def test_banned_modules_are_reported_with_file_and_line(self):
        found = self.problems({"a.py": "import os\nimport sqlite3\nfrom dbm import open\nimport shelve as s\n"})
        self.assertEqual(len(found), 3)
        self.assertTrue(any(p.startswith("a.py:2:") and "sqlite3" in p for p in found))
        self.assertTrue(any(p.startswith("a.py:3:") and "dbm" in p for p in found))
        self.assertTrue(any(p.startswith("a.py:4:") and "shelve" in p for p in found))

    def test_banned_module_through_dynamic_import(self):
        found = self.problems({"a.py": "import importlib\nimportlib.import_module('sqlite3')\n"})
        self.assertTrue(any("a.py:2:" in p and "sqlite3" in p for p in found))

    def test_outside_package_is_reported(self):
        found = self.problems({"a.py": "import requests\n"})
        self.assertEqual(len(found), 1)
        self.assertIn("requests", found[0])

    def test_tester_importing_engine_is_reported(self):
        found = self.problems({
            "torture.py": "import src.kv\n",
            "tester/__init__.py": "",
            "tester/oracle.py": "from src import kv\n",
            "tester/modes.py": "from ..src.kv import db\n",
            "tester/workload.py": "import kvdb\n",
        })
        engine = [p for p in found if "must not import the engine" in p]
        self.assertEqual(len(engine), 4, found)

    def test_engine_importing_src_is_fine(self):
        self.assertEqual(self.problems({"kvdb.py": "from src.kv import db\n"}), [])

    def test_syntax_error_is_reported(self):
        found = self.problems({"a.py": "def (:\n"})
        self.assertEqual(len(found), 1)
        self.assertIn("cannot parse", found[0])


class PromptLogTest(unittest.TestCase):
    def setUp(self):
        self.root = make_tree({})

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_hook(self, stdin_bytes, name="tester"):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.root)
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "promptlog.py"), name],
            input=stdin_bytes, capture_output=True, env=env, timeout=30)

    def log_text(self, name="tester"):
        path = os.path.join(self.root, "prompts", "claude-code-{}.md".format(name))
        with open(path, "r", encoding="utf-8", newline="") as f:
            return f.read()

    def test_prompt_is_logged_exactly_and_stdout_is_empty(self):
        prompt = "logger test — ünïcode ✓\n  indented line\nlast line"
        result = self.run_hook(json.dumps({"prompt": prompt, "cwd": "x"}).encode("utf-8"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        text = self.log_text()
        self.assertTrue(text.startswith("### "))
        self.assertIn(" · Claude Code · tester\n~~~~~\n" + prompt + "\n~~~~~\n", text)

    def test_entries_are_appended_in_order(self):
        for p in ("first", "second"):
            self.assertEqual(self.run_hook(json.dumps({"prompt": p}).encode()).returncode, 0)
        text = self.log_text()
        self.assertLess(text.index("~~~~~\nfirst\n"), text.index("~~~~~\nsecond\n"))
        self.assertEqual(text.count("### "), 2)

    def test_prompt_containing_a_tilde_fence_gets_a_longer_fence(self):
        prompt = "before\n~~~~~~\ninside\n~~~~~~\nafter"
        self.run_hook(json.dumps({"prompt": prompt}).encode())
        self.assertIn("\n~~~~~~~\n" + prompt + "\n~~~~~~~\n", self.log_text())

    def test_bad_input_never_blocks_and_never_prints_to_stdout(self):
        for bad in (b"not json", b"", json.dumps({"no_prompt": 1}).encode()):
            result = self.run_hook(bad)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertNotEqual(result.stderr, b"")

    def test_name_cannot_escape_the_prompts_folder(self):
        self.run_hook(json.dumps({"prompt": "x"}).encode(), name="../../evil")
        self.assertEqual(os.listdir(os.path.join(self.root, "prompts")), ["claude-code-evil.md"])


if __name__ == "__main__":
    unittest.main()
