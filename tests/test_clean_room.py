"""Clean Room, a private edition (tools/clean_room.py): the cloud code is left out, none of it is left, and what is
left works."""
import importlib.util
import os
import py_compile
import shutil
import subprocess
import sys

import pytest

from conftest import TESTS_DIR

ROOT = os.path.dirname(TESTS_DIR)
spec = importlib.util.spec_from_file_location("clean_room", os.path.join(ROOT, "tools", "clean_room.py"))
cr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cr)


class TestStrip:
    def test_a_region_and_its_markers_are_left_out(self):
        text = "a = 1\n# cloud:start\nb = 2\n    # cloud:end\nc = 3\n"
        assert cr.strip(text, "#") == "a = 1\nc = 3\n"

    def test_cmd_regions_use_rem(self):
        text = "echo one\r\nrem cloud:start\r\necho two\r\nrem cloud:end\r\necho three\r\n"
        assert cr.strip(text, "rem") == "echo one\r\necho three\r\n"

    def test_a_marker_mentioned_in_a_line_is_not_a_marker(self):
        text = 'x = "# cloud:start"\n'
        assert cr.strip(text, "#") == text

    @pytest.mark.parametrize("text", ["# cloud:start\nx\n", "x\n# cloud:end\n", "# cloud:start\n# cloud:start\n# cloud:end\n"])
    def test_unbalanced_regions_are_an_error_not_a_guess(self, text):
        with pytest.raises(ValueError):
            cr.strip(text, "#")

    def test_the_package_gets_clean_rooms_name_and_description(self):
        with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
            settings = cr.clean_room_pyproject(f.read())
        assert 'name = "localagentbrowser-cleanroom"' in settings and "Clean Room, a private edition" in settings
        assert not cr.CLOUD_WORDS.search(settings)


class TestInstallerScript:
    def test_clean_room_blocks_are_decided_and_others_kept(self):
        text = ("#ifndef AppVersion\n  #define AppVersion \"dev\"\n#endif\n#ifdef CleanRoom\nC\n#else\nF\n#endif\n"
                "#ifndef CleanRoom\ncloud\n#endif\nend\n")
        assert cr.preprocess_clean_room(text) == "#ifndef AppVersion\n  #define AppVersion \"dev\"\n#endif\nC\nend\n"

    def test_the_clean_room_installer_has_no_cloud_parts_and_its_own_place(self):
        with open(os.path.join(ROOT, "installer", "LocalAgentBrowser.iss"), encoding="utf-8") as f:
            full = f.read()
        script = cr.preprocess_clean_room(full)
        code = [line for line in script.splitlines() if not line.lstrip().startswith(";")]
        assert [line for line in code if cr.CLOUD_WORDS.search(line)] == []
        assert "OptionalPage" not in script and '#define AppTitle "LocalAgentBrowser Clean Room"' in script
        # Its own AppId, so it installs beside the full edition instead of replacing it
        assert "6E1E4C2A" not in script and "6E1E4C2A" in full


@pytest.fixture(scope="module")
def clean_room_copy(tmp_path_factory):
    """The app and its tests, copied and stripped as the Clean Room package is."""
    if not os.path.isdir(os.path.join(ROOT, ".git")) and not os.path.isfile(os.path.join(ROOT, ".git")):
        pytest.skip("needs the git repository (the package is built from its tracked files)")
    folder = tmp_path_factory.mktemp("cleanroom")
    cr.copy_app(str(folder))
    shutil.copy(os.path.join(ROOT, "installer", "run_suite.cmd"), folder / "tools" / "windows" / "run_suite.cmd")
    changed = cr.strip_tree(str(folder))
    return folder, changed


class TestCleanRoom:
    def test_no_cloud_code_or_wording_is_left(self, clean_room_copy):
        folder, changed = clean_room_copy
        assert cr.cloud_lines(str(folder)) == []
        assert os.path.join("localagentbrowser", "agent.py") in changed
        for left_out in ("tools/clean_room.py", "tests/test_clean_room.py", "CHANGELOG.md", "cleanroom"):
            assert not (folder / left_out).exists()
        assert (folder / "README.md").read_text(encoding="utf-8").startswith("# LocalAgentBrowser Clean Room")

    def test_every_python_file_still_compiles(self, clean_room_copy, tmp_path):
        folder, _ = clean_room_copy
        for dirpath, _, files in os.walk(folder):
            for name in files:
                if name.endswith(".py"):
                    py_compile.compile(os.path.join(dirpath, name), doraise=True, cfile=str(tmp_path / "compiled.pyc"))

    def test_the_agent_works_without_the_cloud_code(self, clean_room_copy):
        folder, _ = clean_room_copy
        check = ("import agent_browser as ab\n"
                 "from localagentbrowser import agent, config\n"
                 "assert not any(hasattr(m, n) for m in (agent, config) for n in ('EXTRA_BODY', 'EXTRA_HEADERS', 'RATE_LIMIT_WAITS'))\n"
                 "assert 'LM Studio' in agent.failure_advice(RuntimeError('x'))\n"
                 "assert callable(ab.with_retries)\n")
        result = subprocess.run([sys.executable, "-c", check], cwd=folder, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def test_it_only_uses_a_model_server_on_this_pc_or_the_local_network(self, clean_room_copy):
        folder, _ = clean_room_copy
        check = ("from localagentbrowser import agent\n"
                 "assert agent.server_allowed()\n"
                 "agent.BASE_URL = 'http://192.168.1.20:1234/v1'\n"
                 "assert agent.server_allowed()\n"
                 "agent.BASE_URL = 'https://8.8.8.8/v1'\n"
                 "assert not agent.server_allowed()\n")
        result = subprocess.run([sys.executable, "-c", check], cwd=folder, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    def test_the_full_edition_takes_any_server(self, monkeypatch):
        from localagentbrowser import agent
        monkeypatch.setattr(agent, "BASE_URL", "https://8.8.8.8/v1")
        assert agent.server_allowed()

    def test_the_test_suite_passes_on_clean_room(self, clean_room_copy, tmp_path):
        folder, _ = clean_room_copy
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--basetemp={tmp_path / 'pt'}"],
                                cwd=folder, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
