"""Pin the load-bearing commands in the scheduled-run prompt and README (they are executed verbatim)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPT = (ROOT / "docs" / "weekly-run-prompt.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
INSTALL = ("cp <that path> fundkit-0.1.0-py3-none-any.whl && "
           "pip install fundkit-0.1.0-py3-none-any.whl --break-system-packages")


def test_prompt_installs_from_the_clone_with_the_drive_wheel_as_fallback():
    assert "pip install <clone> --break-system-packages" in PROMPT
    assert INSTALL in PROMPT and "1NG9sZm8OAEPymfNuZbsZAeIyyZUBPCWu" in PROMPT
    assert PROMPT.index("pip install <clone>") < PROMPT.index(INSTALL)
    assert "pip install <TOOLKIT_ZIP_PATH>" not in PROMPT and "<SHEET_FILE_ID>" not in PROMPT


def test_prompt_pulls_the_sheet_through_sheet_import():
    assert "fundkit sheet-import <that path> --data ./data" in PROMPT
    assert "fundkit sheet-export out.xlsx --data ./data" in PROMPT


def test_prompt_runs_from_the_github_feed_with_web_quotes_as_last_resort():
    block = PROMPT[PROMPT.index("3. **Get market data"):PROMPT.index("4. **Write the notes.**")]
    assert "Earikan21/Oaklink" in block and "feed/market.json" in block
    assert "FUNDKIT_MARKET=fake FUNDKIT_FAKE_MARKET_JSON=<clone>/feed/market.json" in block
    assert "actions/workflows/market-feed.yml/dispatches" in block
    assert block.index("feed/market.json") < block.index("web-quoted mode")
    assert "nothing but CASH, `index_etf` and `bond_etf`" in block and "--no-review" in block


def test_readme_documents_install_from_release_zip():
    assert "unzip -o fundkit-<version>.zip -d toolkit" in README
    assert "pip install toolkit/fundkit-*.whl --break-system-packages" in README


def test_prompt_previews_dry_then_publishes_the_live_run():
    dry = "fundkit run --data ./data --today <today> --dry-run --json run.json --out memo.md"
    live = "fundkit run --data ./data --today <today> --notes notes.json --out memo.md --json run.json"
    assert dry in PROMPT and live in PROMPT
    assert PROMPT.index(dry) < PROMPT.index("Write `notes.json`") < PROMPT.index(live)
    assert "--dry-run --notes" not in PROMPT  # the published memo is the live one


def test_prompt_and_readme_require_iso_dates_and_plain_numbers():
    assert "ISO dates (`YYYY-MM-DD`) and plain numbers" in PROMPT
    assert "YYYY-MM-DD" in README and "no thousands separators" in README


def test_prompt_maps_schema_and_validation_errors_to_sheet_formatting():
    assert "`error: schema mismatch`" in PROMPT and "validation" in PROMPT
    assert "Do not retry blindly" in PROMPT


def test_readme_spread_rule_is_strict():
    assert "spread < 0.5%" in README and "spread at most 0.5%" not in README
