"""Clean Room, a private edition: a copy of the app with the cloud code left out, checked to have none of it left.

Usage:
    python tools/clean_room.py stage FOLDER     strip a staged copy of the app in place (installer\\build.ps1 -CleanRoom)
    python tools/clean_room.py package FOLDER   copy the repository's app files to an empty FOLDER and strip them there,
                                                ready for "python -m build FOLDER" (the localagentbrowser-cleanroom package)
Exit status 1 if any cloud code or cloud wording is left, with each line that has it.

The cloud code is marked in the source, so the editions are one code base. A region is the lines between
"# cloud:start" and "# cloud:end" (in Python files) or "rem cloud:start" and "rem cloud:end" (in .cmd files), each
on a line of its own; stripping removes the region and both marker lines. The installer script uses Inno Setup's
own "#ifndef CleanRoom" instead (build.ps1 -CleanRoom defines CleanRoom).

The documents that describe the cloud option are swapped for Clean Room's own: its README (cleanroom/README.md)
replaces the project's, the package settings get Clean Room's name and description, and the changelog, security
policy, contributor guide and llms.txt are left out. Then every text file of the copy is read for the words the cloud
code uses (CLOUD_WORDS): a marker that was left out, or cloud code added later outside a region, fails the build
instead of shipping.
"""
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKERS = {".py": "#", ".cmd": "rem", ".bat": "rem"}
TEXT_FILES = (".py", ".cmd", ".bat", ".ps1", ".md", ".txt", ".toml", ".in", ".json", ".yml", ".cfg", ".html", ".js", ".css")
# Words only the cloud code and its documents use (case is ignored): the service, its settings and its failures
CLOUD_WORDS = re.compile(r"cloud|openrouter|extra_body|extra_headers|data_collection|ratelimit|rate_limit|"
                         r"retry_after|modelrefused|content_filter|unsupported_parameters", re.I)
# Left out of Clean Room: the build tools, and the documents written for the full edition and its development
LEFT_OUT = ("tools/clean_room.py", "tests/test_clean_room.py", "cleanroom", "CHANGELOG.md", "SECURITY.md",
            "CONTRIBUTING.md", "llms.txt", "tools/compat_models_cloud.txt", "localagentbrowser/cloud_models.json")
# The repository's files that are not part of the app or its package (installer\build.ps1 also leaves out tests/)
NOT_APP = re.compile(r"^(docs|benchmarks|installer|\.github)/|^\.")
PACKAGE_NAME = "localagentbrowser-cleanroom"
DESCRIPTION = ("Clean Room, a private edition of LocalAgentBrowser: an autonomous web research agent with checked answers, "
               "on a local LLM (LM Studio) only, and Playwright.")


def strip(text, comment):
    """The text without its cloud regions. A region left open, or an end with no start, is an error: the edition
    would otherwise ship with half a region in it, or without code that was never meant to go."""
    start = re.compile(rf"^\s*{comment}\s*cloud:start\s*$", re.I)
    end = re.compile(rf"^\s*{comment}\s*cloud:end\s*$", re.I)
    kept, opened = [], None
    for number, line in enumerate(text.splitlines(keepends=True), start=1):
        if start.match(line):
            if opened:
                raise ValueError(f"line {number}: cloud:start inside the region opened on line {opened}")
            opened = number
        elif end.match(line):
            if not opened:
                raise ValueError(f"line {number}: cloud:end with no cloud:start")
            opened = None
        elif not opened:
            kept.append(line)
    if opened:
        raise ValueError(f"line {opened}: cloud:start with no cloud:end")
    return "".join(kept)


def preprocess_clean_room(text):
    """An Inno Setup script as its preprocessor reads it with CleanRoom defined: only the "#ifdef CleanRoom" and
    "#ifndef CleanRoom" blocks are decided (with their "#else"); every other directive is kept as it is."""
    kept, stack = [], []  # stack: for each open #if, whether it is ours and whether its lines are kept now
    for line in text.splitlines(keepends=True):
        directive = line.strip().lower()
        keeping = all(keep for _, keep in stack)
        if re.match(r"#if(n?)def\s+cleanroom\b", directive):
            stack.append((True, directive.startswith("#ifdef")))
            continue
        if directive.startswith("#if"):
            stack.append((False, True))
        elif directive.startswith("#else") and stack and stack[-1][0]:
            stack[-1] = (True, not stack[-1][1])
            continue
        elif directive.startswith("#endif") and stack:
            ours, _ = stack.pop()
            if ours:
                continue
        if keeping:
            kept.append(line)
    return "".join(kept)


def clean_room_pyproject(text):
    """The package settings with Clean Room's name and description, without the cloud keyword or the changelog link."""
    text = re.sub(r'(?m)^name = ".*"$', f'name = "{PACKAGE_NAME}"', text, count=1)
    text = re.sub(r'(?m)^description = ".*"$', f'description = "{DESCRIPTION}"', text, count=1)
    text = re.sub(r',\s*"openrouter"', ', "privacy"', text)
    text = re.sub(r'(?m)^Changelog = .*\n', "", text)
    return text


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def strip_tree(folder):
    """Makes a copy of the app into Clean Room in place: swaps the documents, strips every code file and removes what
    is left out. Returns the files changed."""
    changed = []
    for path in LEFT_OUT:
        full = os.path.join(folder, path)
        if os.path.isdir(full):
            shutil.rmtree(full)
        elif os.path.exists(full):
            os.remove(full)
    shutil.copy(os.path.join(ROOT, "cleanroom", "README.md"), os.path.join(folder, "README.md"))
    changed.append("README.md")
    for name, change in (("pyproject.toml", clean_room_pyproject),
                         ("MANIFEST.in", lambda t: re.sub(r"(?m)^include CHANGELOG\.md\n", "", t))):
        path = os.path.join(folder, name)
        if os.path.exists(path):
            write(path, change(read(path)))
            changed.append(name)
    for dirpath, dirs, files in os.walk(folder):
        if dirpath == folder:
            dirs[:] = [d for d in dirs if d != "python"]  # the staged Python and its packages are not the app's code
        for name in files:
            comment = MARKERS.get(os.path.splitext(name)[1].lower())
            if not comment:
                continue
            path = os.path.join(dirpath, name)
            text = read(path)
            try:
                stripped = strip(text, comment)
            except ValueError as err:
                raise ValueError(f"{os.path.relpath(path, folder)}: {err}") from None
            if stripped != text:
                write(path, stripped)
                changed.append(os.path.relpath(path, folder))
    return sorted(changed)


def cloud_lines(folder, skip=("python",)):
    """Every line of a text file under the folder that still uses a cloud word, as "file:line: text". The folder's
    own Python (the installer stages one, with its packages) is not the app's and is skipped."""
    found = []
    for dirpath, dirs, files in os.walk(folder):
        if dirpath == folder:
            dirs[:] = [d for d in dirs if d not in skip]
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if not (name.lower().endswith(TEXT_FILES) or name == "LICENSE"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8", errors="replace") as f:
                for number, line in enumerate(f, start=1):
                    if CLOUD_WORDS.search(line):
                        found.append(f"{os.path.relpath(path, folder)}:{number}: {line.strip()[:160]}")
    return found


def copy_app(folder):
    """Copies the repository's tracked app files and tests (what the package is built from) to the folder."""
    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
    for name in filter(None, files):
        if NOT_APP.match(name):
            continue
        os.makedirs(os.path.join(folder, os.path.dirname(name)), exist_ok=True)
        shutil.copy(os.path.join(ROOT, name), os.path.join(folder, name))


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("stage", "package"):
        sys.exit(__doc__)
    mode, folder = sys.argv[1], sys.argv[2]
    if mode == "package":
        if os.path.exists(folder) and os.listdir(folder):
            sys.exit(f"{folder} is not empty")
        os.makedirs(folder, exist_ok=True)
        copy_app(folder)
    elif not os.path.isdir(folder):
        sys.exit(f"{folder} is not a folder")
    changed = strip_tree(folder)
    print(f"Clean Room: {len(changed)} files changed ({', '.join(changed)})")
    found = cloud_lines(folder)
    if found:
        print("Cloud code or wording is still in Clean Room:", *found, sep="\n  ")
        sys.exit(1)
    print("Checked: no cloud code or wording left in any text file.")


if __name__ == "__main__":
    main()
