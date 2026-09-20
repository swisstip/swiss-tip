"""Read and write a pack's acceptance suite, `releases/<pack>/acceptance.yaml`, and its regression pack.

The models live in `swisstip.core.acceptance` and the check in
`swisstip.runtime.acceptance`; the YAML lives here because the serving side
carries no PyYAML.
"""

from pathlib import Path

import yaml

from swisstip.core.acceptance import AcceptanceFile


def parse_acceptance(text: str) -> AcceptanceFile:
    return AcceptanceFile.model_validate(yaml.safe_load(text))


def load_acceptance(path: Path) -> AcceptanceFile:
    return parse_acceptance(Path(path).read_text(encoding="utf-8"))


REGRESSION_FILE = "regression.yaml"


def load_regression(pack_dir: Path) -> tuple[AcceptanceFile, AcceptanceFile, AcceptanceFile]:
    """The acceptance suite, the regression suite and the regression pack that combines them.

    `releases/<pack>/regression.yaml` holds the questions beyond the acceptance-test documents in the suite format;
    the pack replays the acceptance cases first and the regression cases after them, under the acceptance suite's
    policy, so a UAT case is referenced rather than copied. A case ID may appear in only one of the two files."""
    acceptance = load_acceptance(Path(pack_dir) / "acceptance.yaml")
    regression = load_acceptance(Path(pack_dir) / REGRESSION_FILE)
    if regression.pack != acceptance.pack:
        raise ValueError(f"{REGRESSION_FILE} is for pack {regression.pack}, the acceptance suite for {acceptance.pack}")
    combined = AcceptanceFile(pack=acceptance.pack, policy=acceptance.policy, cases=acceptance.cases + regression.cases)
    return acceptance, regression, combined


def dump_acceptance(suite: AcceptanceFile) -> str:
    return yaml.safe_dump(suite.model_dump(mode="json", exclude_none=True, exclude_defaults=True),
                          allow_unicode=True, sort_keys=False, width=110)


def save_acceptance(path: Path, suite: AcceptanceFile) -> None:
    Path(path).write_text(dump_acceptance(suite), encoding="utf-8", newline="\n")
