import csv
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from cli import write_reports
from collect_reviews import collect_ios
from review_insights import classify_reviews, match_labels, summarize

ROOT = Path(__file__).resolve().parent.parent


class ClassificationTest(unittest.TestCase):
    def setUp(self):
        self.reviews = json.loads((ROOT / "examples/reviews.synthetic.json").read_text())
        self.labels = json.loads((ROOT / "examples/labels.synthetic.json").read_text())

    def test_reordered_labels_preserve_review_text_and_input_order(self):
        result = match_labels(self.reviews, list(reversed(self.labels)))
        self.assertEqual([r["review_id"] for r in result], [r["review_id"] for r in self.reviews])
        for i, row in enumerate(result):
            self.assertEqual(row["text"], self.reviews[i]["text"])
            self.assertEqual(row["category"], self.labels[i]["category"])
        self.assertNotIn("category", self.reviews[0])

    def test_missing_label_fails(self):
        with self.assertRaises(ValueError):
            match_labels(self.reviews, self.labels[:-1])

    def test_duplicate_label_fails(self):
        with self.assertRaises(ValueError):
            match_labels(self.reviews, self.labels + [self.labels[0]])

    def test_unknown_id_fails_even_when_counts_match(self):
        labels = [dict(x) for x in self.labels]
        labels[0]["review_id"] = "unknown"
        with self.assertRaises(ValueError):
            match_labels(self.reviews, labels)

    def test_invalid_schema_and_json_fail(self):
        for bad in ["not JSON", {}, [{**self.labels[0], "category": "invented"}],
                    [{**self.labels[0], "topic": " "}],
                    [{**self.labels[0], "unexpected": "field"}]]:
            with self.subTest(payload=bad), self.assertRaises(ValueError):
                match_labels(self.reviews[:1], bad)

    def test_invalid_input_ids_and_empty_text_fail(self):
        for bad in [[self.reviews[0], self.reviews[0]], [{"review_id": "", "text": "x"}],
                    [{"review_id": "a", "text": " "}]]:
            with self.subTest(reviews=bad), self.assertRaises(ValueError):
                match_labels(bad, [])

    def test_batches_validate_and_omit_metadata_from_prompts(self):
        client = MagicMock()
        client.models.generate_content.side_effect = [
            SimpleNamespace(text=json.dumps(self.labels[i:i+3])) for i in range(0, 8, 3)]
        result = classify_reviews(self.reviews, client, "configured-model", batch_size=3)
        self.assertEqual(len(result), 8)
        self.assertEqual(client.models.generate_content.call_count, 3)
        call = client.models.generate_content.call_args_list[0]
        self.assertEqual(call.kwargs["model"], "configured-model")
        self.assertEqual(call.kwargs["config"]["response_mime_type"], "application/json")
        data = json.loads(call.kwargs["contents"].split("Reviews:\n")[1])
        self.assertEqual(set(data[0]), {"review_id", "text"})

    def test_model_error_or_empty_response_is_not_silently_accepted(self):
        client = MagicMock()
        client.models.generate_content.side_effect = RuntimeError("provider failure")
        with self.assertRaises(RuntimeError):
            classify_reviews(self.reviews, client, "configured-model")
        client.models.generate_content.side_effect = None
        client.models.generate_content.return_value = SimpleNamespace(text=None)
        with self.assertRaises(ValueError):
            classify_reviews(self.reviews, client, "configured-model")

    def test_empty_input_skips_model_and_invalid_batch_size_fails(self):
        client = MagicMock()
        self.assertEqual(classify_reviews([], client, "configured-model"), [])
        client.models.generate_content.assert_not_called()
        with self.assertRaises(ValueError):
            classify_reviews(self.reviews, client, "configured-model", 0)

    def test_summary_counts_and_empty_report(self):
        result = summarize(match_labels(self.reviews, self.labels))
        self.assertEqual(result["total_reviews"], 8)
        self.assertEqual([c["count"] for c in result["categories"]], [2, 3, 2, 1])
        self.assertEqual(sum(c["percentage"] for c in result["categories"]), 100)
        self.assertEqual(sum(t["count"] for t in result["topics"]), 8)
        self.assertTrue(all(c["percentage"] == 0 for c in summarize([])["categories"]))

    def test_csv_formula_handling_keeps_original_json_text(self):
        rows = match_labels(self.reviews[:1], self.labels[:1])
        rows[0]["text"] = " =SUM(1,2)"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_reports(rows, root)
            self.assertEqual(json.loads((root / "classified_reviews.json").read_text())[0]["text"], " =SUM(1,2)")
            with (root / "classified_reviews.csv").open(encoding="utf-8-sig") as f:
                self.assertEqual(next(csv.DictReader(f))["text"], "' =SUM(1,2)")


class CollectorTest(unittest.TestCase):
    def test_ios_skips_metadata_deduplicates_and_omits_authors(self):
        session = MagicMock()
        entry = {"im:rating": {"label": "4"}, "content": {"label": "Örnek yorum"},
                 "updated": {"label": "2026-01-01"}, "id": {"label": "sample-id"},
                 "author": {"name": {"label": "not included"}}}
        session.get.return_value.json.return_value = {"feed": {"entry": [{"title": {}}, entry, entry]}}
        rows = collect_ios("123456", session=session)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rating"], 4)
        self.assertNotIn("author", rows[0])
        self.assertEqual(session.get.call_args.kwargs["timeout"], 30)

    def test_ios_stops_on_empty_feed_and_rejects_bad_arguments(self):
        session = MagicMock()
        session.get.return_value.json.return_value = {"feed": {}}
        self.assertEqual(collect_ios("123456", pages=3, session=session), [])
        self.assertEqual(session.get.call_count, 1)
        for app, pages, country in [("invalid", 1, "tr"), ("123", 0, "tr"),
                                    ("123", 11, "tr"), ("123", 1, "../")]:
            with self.subTest(app=app, pages=pages, country=country), self.assertRaises(ValueError):
                collect_ios(app, pages, country, session)

    def test_ios_http_failure_propagates(self):
        session = MagicMock()
        session.get.return_value.raise_for_status.side_effect = RuntimeError("HTTP failure")
        with self.assertRaises(RuntimeError):
            collect_ios("123456", session=session)


if __name__ == "__main__":
    unittest.main()
