"""Identify a DBVictory build from its commit and its immutable release history."""

import os
from pathlib import Path
import re
import shutil
import subprocess


# Commit of the published v1.0.0 tag. Local release bundles omit tag refs.
V1_COMMIT = "c319808d8c260e240f6704fd2038c5340e8086ce"
VERSION_PATTERN = r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


def git_executable():
    found = shutil.which("git")
    if found:
        return found
    for folder in (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git",
        Path.home() / "AppData/Local/Programs/Git",
        Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/git",
    ):
        binary = folder / "cmd/git.exe"
        if binary.is_file():
            return str(binary)
    raise RuntimeError("Brak programu Git potrzebnego do sprawdzenia wydania.")


def git_output(root, *args, timeout=None):
    result = subprocess.run(
        [git_executable(), "-c", f"safe.directory={root.as_posix()}", "-c",
         "protocol.allow=never", "-c", "protocol.file.allow=always",
         "-c", f"core.hooksPath={os.devnull}", "-C", str(root), *args],
        stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"Git odrzucił operację {args[0]}.")
    return result.stdout.rstrip("\r\n")


def read_head_revision(root: Path) -> str | None:
    git_dir = root / ".git"
    try:
        if git_dir.is_file():
            pointer = git_dir.read_text(encoding="utf-8").strip()
            if not pointer.startswith("gitdir: "):
                return None
            git_dir = root / pointer.removeprefix("gitdir: ")

        value = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if value.startswith("ref: "):
            ref = value.removeprefix("ref: ")
            if (not ref.startswith("refs/") or any(c in ref for c in "\\:\x00")
                    or any(part in ("", ".", "..") for part in ref.split("/"))):
                return None
            common = git_dir
            if (git_dir / "commondir").is_file():
                common = git_dir / (git_dir / "commondir").read_text(encoding="utf-8").strip()
            try:
                value = (common / ref).read_text(encoding="ascii").strip()
            except FileNotFoundError:
                with (common / "packed-refs").open(encoding="utf-8") as packed:
                    for line in packed:
                        sha, _, name = line.rstrip("\r\n").partition(" ")
                        if name == ref:
                            value = sha
                            break
    except (OSError, UnicodeError, ValueError):
        return None
    return value if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) else None


def checkout_revision(root):
    revision = read_head_revision(root)
    if revision or not (root / ".git").exists():
        return revision
    try:
        value = git_output(root, "rev-parse", "--verify", "HEAD", timeout=2)
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return None
    return value if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) else None


def version_for(root, revision):
    if not revision:
        return None
    versions = {V1_COMMIT: (1, 0, 0)}
    if revision != V1_COMMIT:
        try:
            history = git_output(root, "log", "--no-show-signature", "--no-notes", "--no-decorate",
                                 "--reverse", "--topo-order", "--ancestry-path", "-z",
                                 "--format=%H%x00%P%x00%B", f"{V1_COMMIT}..{revision}", timeout=4)
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            return None
        fields = history.removesuffix("\0").split("\0")
        for commit, parents, message in zip(fields[::3], fields[1::3], fields[2::3]):
            previous = [versions[parent] for parent in parents.split() if parent in versions]
            if not previous:
                continue
            major, minor, patch = max(previous)
            subject, _, body = message.partition("\n")
            if (re.match(r"^[\w-]+(?:\([^()]+\))?!:\s", subject)
                    or re.search(r"^BREAKING[ -]CHANGE:\s*\S", body, re.MULTILINE)):
                versions[commit] = major + 1, 0, 0
            elif re.match(r"^feat(?:\([^()]+\))?:\s", subject):
                versions[commit] = major, minor + 1, 0
            else:
                versions[commit] = major, minor, patch + 1
    number = versions.get(revision)
    return "v%d.%d.%d" % number if number else None


def revision_label(revision, version):
    if not revision:
        return "wersja nieznana"
    return f"{version} ({revision[:7]})" if version else revision[:7]
