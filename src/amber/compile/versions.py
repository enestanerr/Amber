"""Program versions: ``programs/<workflow>/v<N>/{program.py, manifest.json, tests/test_replay.py,
REPORT.md}`` (``docs/04-compiler.md`` §D5)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from amber.compile.manifest import Manifest
from amber.errors import AmberError
from amber.util.json import read_json, write_json
from amber.util.paths import programs_dir

_VERSION_RE = re.compile(r"^v(\d+)$")


class ProgramNotFound(AmberError):
    exit_code = 1


@dataclass(frozen=True)
class VersionInfo:
    workflow: str
    version: int
    path: Path

    @property
    def program_path(self) -> Path:
        return self.path / "program.py"

    @property
    def manifest_path(self) -> Path:
        return self.path / "manifest.json"

    def manifest(self) -> Manifest | None:
        if not self.manifest_path.is_file():
            return None
        return Manifest.model_validate(read_json(self.manifest_path))


def workflow_dir(root: Path, workflow: str) -> Path:
    return programs_dir(root) / workflow


def list_versions(root: Path, workflow: str) -> list[VersionInfo]:
    folder = workflow_dir(root, workflow)
    if not folder.is_dir():
        return []
    out: list[VersionInfo] = []
    for child in folder.iterdir():
        m = _VERSION_RE.match(child.name)
        if m and child.is_dir():
            out.append(VersionInfo(workflow, int(m.group(1)), child))
    return sorted(out, key=lambda v: v.version)


def list_workflows(root: Path) -> list[str]:
    folder = programs_dir(root)
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_dir() and list_versions(root, p.name))


def get_version(
    root: Path, workflow: str, version: int | None = None, *, usable_only: bool = False
) -> VersionInfo:
    versions = list_versions(root, workflow)
    if version is not None:
        for v in versions:
            if v.version == version:
                return v
        raise ProgramNotFound(
            f"no version v{version} of {workflow!r}", hint="See `amber programs ls`."
        )
    if usable_only:
        versions = [v for v in versions if (m := v.manifest()) is not None and m.usable]
    if not versions:
        what = "usable " if usable_only else ""
        raise ProgramNotFound(
            f"no {what}compiled program for {workflow!r}",
            hint=f"Run `amber compile {workflow}` (and `amber verify {workflow}`).",
        )
    return versions[-1]


def next_version(root: Path, workflow: str, skeleton_hash: str, *, refresh: bool) -> int:
    """Bump when the skeleton changed, ``--refresh`` was given or the last version is unusable."""
    versions = list_versions(root, workflow)
    if not versions:
        return 1
    latest = versions[-1]
    manifest = latest.manifest()
    if (
        refresh
        or manifest is None
        or manifest.skeleton_hash != skeleton_hash
        or not manifest.usable
    ):
        return latest.version + 1
    return latest.version


def write_version(
    root: Path,
    manifest: Manifest,
    *,
    program_source: str,
    test_source: str,
    report: str,
) -> VersionInfo:
    info = VersionInfo(
        manifest.workflow,
        manifest.version,
        workflow_dir(root, manifest.workflow) / f"v{manifest.version}",
    )
    (info.path / "tests").mkdir(parents=True, exist_ok=True)
    info.program_path.write_text(program_source, encoding="utf-8", newline="\n")
    (info.path / "tests" / "test_replay.py").write_text(test_source, encoding="utf-8", newline="\n")
    (info.path / "REPORT.md").write_text(report, encoding="utf-8", newline="\n")
    write_json(info.manifest_path, manifest.model_dump(mode="json"))
    return info


def save_manifest(info: VersionInfo, manifest: Manifest) -> None:
    write_json(info.manifest_path, manifest.model_dump(mode="json"))
