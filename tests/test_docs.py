"""Pin the load-bearing commands in the scheduled-run prompt and README (they are executed verbatim)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPT = (ROOT / "docs" / "weekly-run-prompt.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
INSTALL = ("unzip -o <TOOLKIT_ZIP_PATH> -d toolkit && "
           "pip install toolkit/fundkit-*.whl --break-system-packages")


def test_prompt_installs_the_wheel_from_the_release_zip():
    assert INSTALL in PROMPT
    assert "pip install <TOOLKIT_ZIP_PATH>" not in PROMPT


def test_readme_documents_install_from_release_zip():
    assert "unzip -o fundkit-<version>.zip -d toolkit" in README
    assert "pip install toolkit/fundkit-*.whl --break-system-packages" in README


def test_prompt_previews_dry_then_publishes_the_live_run():
    dry = "fundkit run --data ./data --today <today> --dry-run --json run.json --out memo.md"
    live = "fundkit run --data ./data --today <today> --notes notes.json --out memo.md --json run.json"
    assert dry in PROMPT and live in PROMPT
    assert PROMPT.index(dry) < PROMPT.index("Write `notes.json`") < PROMPT.index(live)
    assert "--dry-run --notes" not in PROMPT  # the published memo is the live one
