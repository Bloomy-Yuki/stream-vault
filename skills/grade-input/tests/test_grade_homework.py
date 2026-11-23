import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import grade_homework


class GradeHomeworkTests(unittest.TestCase):
    def test_natural_key_orders_numbered_pages(self):
        names = [Path(name) for name in ("page10.png", "page2.png", "page1.png")]

        self.assertEqual(
            [path.name for path in sorted(names, key=grade_homework.natural_key)],
            ["page1.png", "page2.png", "page10.png"],
        )

    def test_reference_txt_resolves_relative_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "answer.md").write_text("Reference answer", encoding="utf-8")
            (folder / "reference.txt").write_text("answer.md\n", encoding="utf-8")

            self.assertEqual(grade_homework.read_folder_context(folder, "reference"), "Reference answer")

    def test_reference_txt_can_contain_reference_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "reference.txt").write_text("The answer is 42.", encoding="utf-8")

            self.assertEqual(
                grade_homework.read_folder_context(folder, "reference"), "The answer is 42."
            )

    def test_reference_txt_reports_missing_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "reference.txt").write_text("missing-answer.md", encoding="utf-8")

            with self.assertRaisesRegex(FileNotFoundError, "missing-answer.md"):
                grade_homework.read_folder_context(folder, "reference")

    def test_collect_page_images_naturally_orders_images_and_pdf_pages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            for name in ("page10.png", "page2.png", "page1.png"):
                pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2, 2), 0)
                pixmap.save(folder / name)
            document = pymupdf.open()
            document.new_page().insert_text((72, 72), "PDF page")
            document.save(folder / "page3.pdf")
            document.close()

            pages = grade_homework.collect_page_images(folder, dpi=72)

            self.assertEqual(
                [label for label, _ in pages],
                ["page1.png", "page2.png", "page3.pdf, page 1", "page10.png"],
            )
            self.assertTrue(all(content.startswith(b"\x89PNG") for _, content in pages))

    def test_typed_submission_does_not_require_ocr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            (folder / "submission.md").write_text("My answer", encoding="utf-8")
            args = type("Args", (), {"dpi": 180, "force": False})()

            submission, used_ocr = grade_homework.compose_submission(folder, args, "unused")

            self.assertIn("My answer", submission)
            self.assertFalse(used_ocr)

    def test_grade_payload_includes_context_without_prompt_placeholders(self):
        captured = {}

        def fake_post(_api_key, payload, _retries):
            captured.update(payload)
            return "feedback"

        with patch.object(grade_homework, "post", side_effect=fake_post):
            result = grade_homework.grade(
                "key",
                "Question text",
                "Rubric text",
                None,
                "Student answer </student_work>",
                "History",
                "test/model",
                "",
                1,
            )

        self.assertEqual(result, "feedback")
        self.assertEqual(captured["model"], "test/model")
        self.assertEqual(captured["provider"], {"data_collection": "deny"})
        self.assertNotIn("{subject}", captured["messages"][0]["content"])
        context = json.loads(captured["messages"][1]["content"].split("\n", 1)[1])
        self.assertEqual(context["assignment"], "Question text")
        self.assertEqual(context["rubric"], "Rubric text")
        self.assertEqual(context["student_work"], "Student answer </student_work>")

    def test_process_attempt_writes_feedback_for_typed_submission(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir) / "attempt-1"
            folder.mkdir()
            (folder / "submission.md").write_text("My answer", encoding="utf-8")
            args = type(
                "Args",
                (),
                {
                    "force": False,
                    "dpi": 180,
                    "text_model": "test/transcriber",
                    "reasoning": "",
                    "retries": 1,
                    "question": "",
                    "rubric": "",
                    "reference": "",
                    "subject": "",
                    "grade_model": "test/grader",
                },
            )()

            with patch.object(grade_homework, "grade", return_value="## Overall assessment\nScore: 8/10"):
                grade_homework.process_attempt(folder, args, "key")

            feedback = (folder / "feedback.md").read_text(encoding="utf-8")
            self.assertIn("# Homework feedback: attempt-1", feedback)
            self.assertIn("Score: 8/10", feedback)

    def test_post_does_not_retry_permanent_http_errors(self):
        error = urllib.error.HTTPError(
            "https://example.test",
            401,
            "unauthorized",
            {},
            io.BytesIO(b"invalid key"),
        )
        with patch.object(grade_homework.urllib.request, "urlopen", side_effect=error) as urlopen:
            with patch.object(grade_homework.time, "sleep") as sleep:
                with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
                    grade_homework.post("bad-key", {}, retries=4)

        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    def test_post_retries_transient_http_errors(self):
        error = urllib.error.HTTPError(
            "https://example.test",
            503,
            "unavailable",
            {},
            io.BytesIO(b"try again"),
        )

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

        with patch.object(
            grade_homework.urllib.request, "urlopen", side_effect=[error, Response()]
        ) as urlopen:
            with patch.object(grade_homework.time, "sleep"):
                result = grade_homework.post("key", {}, retries=2)

        self.assertEqual(result, "ok")
        self.assertEqual(urlopen.call_count, 2)

    def test_post_allows_empty_transcription(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": ""}}]}).encode()

        with patch.object(grade_homework.urllib.request, "urlopen", return_value=Response()):
            result = grade_homework.post("key", {}, retries=1, allow_empty=True)

        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
