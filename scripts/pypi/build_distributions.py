"""Build the Swiss TIP PyPI distributions from the component source trees.

    python scripts/pypi/build_distributions.py --version 0.2.0              stage and build all three
    python scripts/pypi/build_distributions.py --version 0.2.0 --stage-only print the staged pyproject files
    python scripts/pypi/build_distributions.py --version 0.2.0 --only swisstip-mcp

The repository keeps one pyproject.toml per component (packages/core,
apps/mcp-server, ...) for editable development installs. PyPI gets fewer,
larger distributions, defined in scripts/pypi/distributions.toml. For each
distribution the script copies the components' src/ trees into
build/pypi/<distribution>/, writes a pyproject.toml whose dependencies,
scripts, optional dependencies and package data are the union of the
components' declarations, and runs "python -m build" there, which builds the
sdist and then the wheel from the sdist. Module names do not change: the
swisstip-core distribution ships swisstip.core and swisstip.runtime.

A dependency on a component in the same distribution is dropped; a dependency
on a component in another distribution becomes "<distribution>==<version>", so
the server and the builder always run on the core they were released with.
Requirements on the same project from several components are merged into one.

Needs the "build" and "packaging" packages (pip install build).
"""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info")


def load_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def repository_components() -> set[str]:
    return {path.parent.relative_to(ROOT).as_posix() for pattern in ("packages/*/pyproject.toml", "apps/*/pyproject.toml")
            for path in ROOT.glob(pattern)}


def component_owners(definitions: dict) -> dict[str, str]:
    """Map every component directory to its distribution; fail on a component left out or listed twice."""
    owners = {}
    for distribution, spec in definitions["distributions"].items():
        for component in spec["components"]:
            if component in owners:
                raise SystemExit(f"{component} is listed in both {owners[component]} and {distribution}")
            owners[component] = distribution
    missing = sorted(repository_components() - set(owners))
    unknown = sorted(set(owners) - repository_components())
    if missing or unknown:
        raise SystemExit(f"distributions.toml does not match the repository: missing {missing}, unknown {unknown}")
    return owners


def merge_requirements(requirements: list[Requirement]) -> list[str]:
    merged: dict[tuple, Requirement] = {}
    for requirement in requirements:
        key = (canonicalize_name(requirement.name), tuple(sorted(requirement.extras)), str(requirement.marker))
        if key in merged:
            merged[key].specifier &= requirement.specifier
        else:
            merged[key] = Requirement(str(requirement))
    for requirement in merged.values():
        # An exact pin that satisfies every other bound is the whole constraint.
        pins = [spec.version for spec in requirement.specifier if spec.operator == "==" and "*" not in spec.version]
        if len(pins) == 1 and requirement.specifier.contains(pins[0], prereleases=True):
            requirement.specifier = type(requirement.specifier)(f"=={pins[0]}")
    return sorted((str(requirement) for requirement in merged.values()), key=str.lower)


def distribution_project(name: str, spec: dict, definitions: dict, owners: dict[str, str], version: str) -> dict:
    """The [project] and [tool.setuptools] content of one distribution, merged from its components."""
    component_names = {load_toml(ROOT / component / "pyproject.toml")["project"]["name"]: owner
                        for component, owner in owners.items()}
    dependencies, extras, scripts, package_data, python = [], {}, {}, {}, set()
    for component in spec["components"]:
        pyproject = load_toml(ROOT / component / "pyproject.toml")
        project = pyproject["project"]
        python.add(project["requires-python"])
        for text in project.get("dependencies", []):
            requirement = Requirement(text)
            if requirement.name.startswith("swisstip-"):
                owner = component_names.get(requirement.name)
                if owner is None:
                    raise SystemExit(f"{component} depends on unknown component {requirement.name}")
                if owner != name:
                    dependencies.append(Requirement(f"{owner}=={version}"))
                continue
            dependencies.append(requirement)
        for extra, texts in project.get("optional-dependencies", {}).items():
            if extra not in spec.get("drop-extras", []):
                extras.setdefault(extra, []).extend(Requirement(text) for text in texts)
        for script, target in project.get("scripts", {}).items():
            if scripts.setdefault(script, target) != target:
                raise SystemExit(f"script {script} has two targets in {name}")
        for package, patterns in pyproject.get("tool", {}).get("setuptools", {}).get("package-data", {}).items():
            package_data.setdefault(package, []).extend(pattern for pattern in patterns
                                                        if pattern not in package_data.get(package, []))
    if len(python) != 1:
        raise SystemExit(f"the components of {name} disagree on requires-python: {sorted(python)}")
    repository = definitions["repository"]
    project = {
        "name": name,
        "version": version,
        "description": spec["description"],
        "readme": "README.md",
        "requires-python": python.pop(),
        "license": "Apache-2.0",
        "license-files": ["LICENSE", "NOTICE"],
        "authors": [{"name": definitions["author"]}],
        "keywords": spec.get("keywords", []),
        "classifiers": sorted(set(definitions.get("classifiers", []) + spec.get("classifiers", []))),
        "dependencies": merge_requirements(dependencies),
        "urls": {"Homepage": repository, "Source": repository, "Issues": f"{repository}/issues"},
    }
    if extras:
        project["optional-dependencies"] = {extra: merge_requirements(texts) for extra, texts in sorted(extras.items())}
    if scripts:
        project["scripts"] = dict(sorted(scripts.items()))
    setuptools = {"packages": {"find": {"where": ["src"], "namespaces": True}}}
    if package_data:
        setuptools["package-data"] = package_data
    return {"build-system": {"requires": ["setuptools>=77"], "build-backend": "setuptools.build_meta"},
            "project": project, "tool": {"setuptools": setuptools}}


def toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, list):
        if not value:
            return "[]"
        return "[\n" + "".join(f"    {toml_value(item)},\n" for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{toml_key(key)} = {toml_value(item)}" for key, item in value.items()) + " }"
    raise TypeError(f"cannot write {type(value).__name__} to TOML")


def toml_key(key: str) -> str:
    return key if key.replace("-", "").replace("_", "").isalnum() else toml_value(key)


def dump_toml(table: dict, name: str = "") -> str:
    """Enough TOML for a pyproject file: nested tables of strings, lists and inline tables in lists."""
    scalars = {key: value for key, value in table.items() if not isinstance(value, dict)}
    subtables = {key: value for key, value in table.items() if isinstance(value, dict)}
    text = f"\n[{name}]\n" if name and (scalars or not subtables) else ""
    text += "".join(f"{toml_key(key)} = {toml_value(value)}\n" for key, value in scalars.items())
    for key, value in subtables.items():
        text += dump_toml(value, f"{name}.{toml_key(key)}" if name else toml_key(key))
    return text


def stage(name: str, spec: dict, definitions: dict, owners: dict[str, str], version: str, stage_root: Path) -> Path:
    target = stage_root / name
    if target.exists():
        shutil.rmtree(target)
    (target / "src").mkdir(parents=True)
    for component in spec["components"]:
        shutil.copytree(ROOT / component / "src", target / "src", ignore=COPY_IGNORE, dirs_exist_ok=True)
    for legal in ("LICENSE", "NOTICE"):
        shutil.copy2(ROOT / legal, target / legal)
    shutil.copy2(HERE / "readme" / f"{name}.md", target / "README.md")
    document = distribution_project(name, spec, definitions, owners, version)
    pyproject = dump_toml(document).lstrip("\n")
    if tomllib.loads(pyproject) != document:  # the writer is minimal; prove its output reads back unchanged
        raise SystemExit(f"staged pyproject.toml of {name} does not round-trip")
    (target / "pyproject.toml").write_text(f"# Generated by scripts/pypi/build_distributions.py; do not edit.\n{pyproject}",
                                           encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", required=True, help="the version of every distribution, PEP 440 normalised, e.g. 0.2.0")
    parser.add_argument("--only", action="append", help="build only this distribution; repeatable")
    parser.add_argument("--stage-dir", type=Path, default=ROOT / "build" / "pypi", help="default build/pypi")
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="directory for sdists and wheels; default dist")
    parser.add_argument("--stage-only", action="store_true", help="stage and print the pyproject files, do not build")
    args = parser.parse_args(argv)

    try:
        if str(Version(args.version)) != args.version:
            parser.error(f"--version {args.version} is not normalised; use {Version(args.version)}")
    except InvalidVersion:
        parser.error(f"--version {args.version} is not a PEP 440 version")
    definitions = load_toml(HERE / "distributions.toml")
    owners = component_owners(definitions)
    selected = args.only or list(definitions["distributions"])
    unknown = sorted(set(selected) - set(definitions["distributions"]))
    if unknown:
        parser.error(f"unknown distributions: {unknown}")

    args.out.mkdir(parents=True, exist_ok=True)
    for name in selected:
        target = stage(name, definitions["distributions"][name], definitions, owners, args.version, args.stage_dir)
        if args.stage_only:
            print(f"==> {target / 'pyproject.toml'}\n{(target / 'pyproject.toml').read_text(encoding='utf-8')}")
            continue
        print(f"==> building {name} {args.version}", flush=True)
        subprocess.run([sys.executable, "-m", "build", "--outdir", str(args.out.resolve()), str(target)], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
