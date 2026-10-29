"""Estimate map-pixel scenarios locally; not an OpenAI usage measurement.

Usage: .venv/bin/python scripts/estimate_weather_image_cost.py --archive .local/weather/briollay
Prices checked 2026-10-29. The 1.2 multiplier is a disclosed assumption for the
requested GPT-6 model IDs, not a confirmed model-specific billing formula.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--multiplier", type=float, default=1.2)
    args = parser.parse_args()
    if not math.isfinite(args.multiplier) or args.multiplier <= 0:
        parser.error("multiplier must be finite and positive")
    manifest = json.loads((args.archive / "latest.json").read_text())
    sizes = []
    for source in manifest["sources"].values():
        for record in source.get("maps", []):
            with Image.open(args.archive / record["path"]) as img:
                sizes.append(img.size)
    scenarios = []
    for limit in [None, 640, 512, 384]:
        pixels, tokens = 0, 0
        for width, height in sizes:
            scale = min(1, limit / max(width, height)) if limit else 1
            w, h = max(1, round(width * scale)), max(1, round(height * scale))
            pixels += w * h
            tokens += math.ceil(math.ceil(w / 32) * math.ceil(h / 32) * args.multiplier)
        scenarios.append(
            {
                "max_side_px": limit,
                "pixels": pixels,
                "assumed_image_tokens": tokens,
                "image_input_usd": {
                    model: round(tokens * rate / 1e6, 6)
                    for model, rate in [
                        ("gpt-6.1-sol", 2),
                        ("gpt-6.1-terra", 1),
                        ("gpt-6-luna", 0.1),
                    ]
                },
            }
        )
    print(
        json.dumps(
            {
                "image_count": len(sizes),
                "multiplier_assumption": args.multiplier,
                "note": "Conditional estimate. No image upload or API call; excludes text, reasoning and output.",
                "scenarios": scenarios,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
