"""Static keyboard/form/contrast guardrails for the offline command UI."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def luminance(hex_color: str) -> float:
    values = [int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in values]
    return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]


def contrast(a: str, b: str) -> float:
    first, second = sorted((luminance(a), luminance(b)), reverse=True)
    return (first + .05) / (second + .05)


def main() -> None:
    html = (ROOT / "nexfiremap/static/index.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    ids = [node["id"] for node in soup.select("[id]")]
    assert len(ids) == len(set(ids)), "duplicate element ids break label and keyboard targeting"
    labelled = {label.get("for") for label in soup.find_all("label") if label.get("for")}
    for control in soup.select("input:not([type=hidden]), select, textarea"):
        if control.has_attr("hidden"):
            continue
        wrapped = control.find_parent("label") is not None
        assert wrapped or control.get("id") in labelled or control.get("aria-label"), f"unlabelled control: {control}"
    for button in soup.find_all("button"):
        assert button.get("type") or button.get("id") not in {"auth-form"}, f"button lacks explicit type: {button.get('id')}"
        assert button.get_text(strip=True) or button.get("aria-label") or button.get("title"), f"unnamed button: {button.get('id')}"
    assert soup.select_one("#auth-error[role=alert][aria-live]")
    assert soup.select_one("#map[aria-label]")

    css = (ROOT / "nexfiremap/static/css/app.css").read_text(encoding="utf-8")
    variables = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", css))
    assert contrast(variables["text-primary"], variables["surface-1"]) >= 7
    assert contrast(variables["text-secondary"], variables["surface-1"]) >= 4.5
    operations = (ROOT / "nexfiremap/static/js/operations.js").read_text(encoding="utf-8")
    assert "event.key === \"Escape\"" in operations and "event.key === \"Enter\"" in operations
    print("Accessibility guardrail checks passed.")


if __name__ == "__main__":
    main()
