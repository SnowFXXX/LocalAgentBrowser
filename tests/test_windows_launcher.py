# cloud:start
"""The Windows launcher's two shortcuts: the plain one always uses LM Studio, the cloud one only cloud models set up in
setup. Run against a copy of the launcher in a temporary program folder, so nothing here starts the agent."""
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(ROOT, "tools", "windows", "LocalAgentBrowser.cmd")
INSTALLER = os.path.join(ROOT, "installer", "LocalAgentBrowser.iss")


def launcher_text():
    with open(LAUNCHER, encoding="utf-8") as f:
        return f.read()


class TestLauncherText:
    def test_cloud_settings_are_read_only_when_started_with_cloud(self):
        lines = launcher_text().splitlines()
        calls = [i for i, line in enumerate(lines) if "settings\\cloud.cmd" in line and "call " in line]
        assert len(calls) == 1
        # The one call sits inside the block that starts with the "cloud" argument check
        opening = max(i for i, line in enumerate(lines[:calls[0]]) if line.startswith("if "))
        assert lines[opening].strip() == 'if /i "%~1"=="cloud" ('

    def test_a_cloud_setting_left_in_the_environment_is_cleared(self):
        text = launcher_text()
        assert text.index('set "LAB_CLOUD="') < text.index("if defined LAB_CLOUD")


class TestInstallerShortcuts:
    def test_the_cloud_shortcuts_pass_cloud_and_only_when_chosen(self):
        with open(INSTALLER, encoding="utf-8") as f:
            lines = [line for line in f.read().splitlines() if "(cloud)\"; Filename:" in line]
        assert len(lines) == 2
        for line in lines:
            assert 'Parameters: "cloud"' in line and "Check: CloudChosen" in line

    def test_the_plain_shortcuts_take_no_argument(self):
        with open(INSTALLER, encoding="utf-8") as f:
            lines = [line for line in f.read().splitlines() if line.startswith('Name: "{group}\\{#AppTitle}";')
                     or line.startswith('Name: "{autodesktop}\\{#AppTitle}";')]
        assert len(lines) == 2
        assert not any("Parameters:" in line for line in lines)


@pytest.mark.skipif(sys.platform != "win32", reason="runs the Windows launcher")
class TestLauncherRuns:
    @pytest.fixture
    def app(self, tmp_path):
        folder = tmp_path / "app"
        (folder / "tools" / "windows").mkdir(parents=True)
        (folder / "settings").mkdir()
        shutil.copy(LAUNCHER, folder / "tools" / "windows" / "LocalAgentBrowser.cmd")
        return folder

    def run(self, app, *args):
        # The output, not the exit code: "cmd /c" reports 0 for a batch file's "exit /b 1" inside a block
        env = {k: v for k, v in os.environ.items() if k not in ("OPENROUTER_API_KEY", "LAB_CLOUD")}
        result = subprocess.run(["cmd", "/c", str(app / "tools" / "windows" / "LocalAgentBrowser.cmd"), *args],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=60)
        return result.stdout

    @staticmethod
    def stopped_before_any_run(out):
        return "LM Studio is not answering" not in out and "Reports for this run" not in out

    def test_cloud_without_cloud_set_up_says_so_and_stops(self, app):
        out = self.run(app, "cloud")
        assert "Cloud models were not turned on in setup" in out and self.stopped_before_any_run(out)

    def test_cloud_without_a_key_says_so_and_stops(self, app):
        (app / "settings" / "cloud.cmd").write_text('set "LAB_CLOUD=1"\r\nset "LMSTUDIO_MODEL=microsoft/phi-4"\r\n')
        out = self.run(app, "cloud")
        assert "OPENROUTER_API_KEY environment variable is not set" in out and self.stopped_before_any_run(out)


class TestInstallerKeepsChoicesOnUpdate:
    """An update over an earlier install starts from that install's choices (the BB3 update test, 2026-11-18: 1.67
    started from the defaults, so clicking Next turned cloud models off and could switch the model)."""

    def text(self):
        with open(INSTALLER, encoding="utf-8") as f:
            return f.read()

    def code(self):
        text = self.text()
        start = text.index("procedure PrefillFromEarlierInstall;")
        return text[start:text.index("\nend;", start)]

    def test_the_model_and_cloud_choice_are_read_from_the_earlier_settings(self):
        code = self.code()
        assert "WizardDirValue + '\\settings'" in code
        assert "SettingFromFile(Dir + '\\model.cmd', 'LMSTUDIO_MODEL')" in code
        # The cloud choice only in the full edition
        assert "#ifndef CleanRoom\n  OptionalPage.Values[0] := FileExists(Dir + '\\cloud.cmd');\n#endif" in code

    def test_a_fresh_install_keeps_the_defaults(self):
        code = self.code()
        assert code.index("if not DirExists(Dir) then\n    Exit;") < code.index("ModelPage.Values[Found] := True;")

    def test_the_pages_are_filled_once_when_the_model_page_first_shows(self):
        assert "if (CurPageID = ModelPage.ID) and not Prefilled then\n    PrefillFromEarlierInstall;" in self.text()

    def test_a_model_set_by_hand_is_kept(self):
        assert "KeptModel := Id;" in self.code()
        assert "'set \"LMSTUDIO_MODEL=' + KeptModel + '\"'" in self.text()
# cloud:end
