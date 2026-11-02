"""Collect reviews or analyze a local JSON file; demo requires no API key."""
import argparse
import csv
import json
import os
from pathlib import Path

from review_insights import classify_reviews, match_labels, summarize

BASE = Path(__file__).resolve().parent


def write_reports(rows, output):
    output.mkdir(parents=True, exist_ok=True)
    for name, data in [("classified_reviews.json", rows), ("summary.json", summarize(rows))]:
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    # Protect spreadsheet users from review text that starts with a formula.
    fields = ["review_id", "platform", "date", "rating", "text", "category", "topic"]
    with (output / "classified_reviews.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v)
                             for k, v in row.items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    demo = subs.add_parser("demo", help="Use synthetic, pre-labelled fixtures; no model call")
    demo.add_argument("--output", type=Path, default=Path("reports/demo"))
    analyze = subs.add_parser("analyze", help="Send review text and IDs to the configured Google model")
    analyze.add_argument("input", type=Path)
    analyze.add_argument("--model", default=None)
    analyze.add_argument("--batch-size", type=int, default=20)
    analyze.add_argument("--output", type=Path, default=Path("reports/analysis"))
    collect = subs.add_parser("collect")
    collect.add_argument("--platform", choices=["android", "ios"], required=True)
    collect.add_argument("--app-id", required=True)
    collect.add_argument("--country", default="tr")
    collect.add_argument("--language", default="tr")
    collect.add_argument("--limit", type=int, default=100)
    collect.add_argument("--pages", type=int, default=1)
    collect.add_argument("--output", type=Path, default=Path("data/reviews.json"))
    args = parser.parse_args()
    if args.command == "collect":
        from collect_reviews import collect_android, collect_ios
        rows = (collect_android(args.app_id, args.limit, args.country, args.language)
                if args.platform == "android" else collect_ios(args.app_id, args.pages, args.country))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
        print(f"Saved {len(rows)} reviews to {args.output}")
        return
    if args.command == "demo":
        rows = json.loads((BASE / "examples/reviews.synthetic.json").read_text())
        labels = json.loads((BASE / "examples/labels.synthetic.json").read_text())
        result = match_labels(rows, labels)
    else:
        from dotenv import load_dotenv
        from google import genai
        load_dotenv()
        key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMMA_API_KEY")
        model = args.model or os.getenv("REVIEW_MODEL")
        if not key or not model:
            parser.error("Set GOOGLE_API_KEY and REVIEW_MODEL (or --model).")
        rows = json.loads(args.input.read_text())
        with genai.Client(api_key=key) as client:
            result = classify_reviews(rows, client, model, args.batch_size)
    write_reports(result, args.output)
    print(json.dumps(summarize(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
