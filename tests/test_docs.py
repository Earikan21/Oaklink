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
