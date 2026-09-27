from __future__ import annotations

import os
import io
import tempfile
import unittest
import zipfile
from email.message import EmailMessage
from pathlib import Path

from email_pipeline.mime_extract import extract_message


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
)


def extract(message: EmailMessage, root: Path, **kwargs):
    return extract_message(message.as_bytes(), root / "attachments", **kwargs)


def zip_payload(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return buffer.getvalue()


class MimeExtractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_html_only_removes_css_and_keeps_link(self) -> None:
        message = EmailMessage()
        message["Subject"] = "Design competition"
        message.set_content(
            """
            <html><head><style>.hero{background:data:image/png;base64,AAAA}</style></head>
            <body><h1>PAGCO Design Competition</h1>
            <p>Deadline: 2026-10-05</p>
            <a href="https://example.org/register">Register now</a></body></html>
            """,
            subtype="html",
        )

        result = extract(message, self.root)

        self.assertEqual(result["text_source"], "text/html")
        self.assertIn("PAGCO Design Competition", result["text"])
        self.assertIn("https://example.org/register", result["text"])
        self.assertNotIn("base64", result["text"])
        self.assertNotIn("background", result["text"])
        self.assertEqual(result["links"][0]["url"], "https://example.org/register")

    def test_empty_plain_falls_back_to_richer_html(self) -> None:
        message = EmailMessage()
        message.set_content("View this email in your browser")
        message.add_alternative(
            "<html><body><p>Scholarship applications close on 2026-10-12.</p></body></html>",
            subtype="html",
        )

        result = extract(message, self.root)

        self.assertEqual(result["text_source"], "text/html")
        self.assertIn("Scholarship applications", result["text"])

    def test_linked_image_is_identified_without_fetching(self) -> None:
        message = EmailMessage()
        message.set_content(
            '<html><body><a href="https://events.example.edu/apply">'
            '<img src="https://cdn.example.edu/poster.png"></a></body></html>',
            subtype="html",
        )

        result = extract(message, self.root)

        self.assertTrue(result["image_only"])
        self.assertEqual(result["content_kind"], "image_link")
        self.assertEqual(result["images"][0]["click_url"], "https://events.example.edu/apply")
        self.assertTrue(result["images"][0]["remote"])
        self.assertEqual(result["links"][0]["source"], "linked_image")

    def test_inline_image_manifest_is_preserved(self) -> None:
        message = EmailMessage()
        message.set_content("Fallback")
        message.add_alternative('<html><body><img src="cid:poster"></body></html>', subtype="html")
        html_part = message.get_payload()[1]
        html_part.add_related(PNG_1X1, maintype="image", subtype="png", cid="<poster>", filename="poster.png")

        result = extract(message, self.root)

        image_attachment = next(item for item in result["attachments"] if item["filename"] == "poster.png")
        self.assertTrue(image_attachment["inline"])
        self.assertEqual(image_attachment["content_id"], "poster")
        self.assertTrue(Path(image_attachment["stored_path"]).exists())
        self.assertTrue(result["has_inline_images"])

    def test_text_attachment_is_saved_and_extracted(self) -> None:
        message = EmailMessage()
        message.set_content("Please review the attached schedule.")
        message.add_attachment(
            b"Deadline,2026-10-08\nPresentation,2026-10-15\n",
            maintype="text",
            subtype="csv",
            filename="schedule.csv",
        )

        result = extract(message, self.root)

        attachment = result["attachments"][0]
        self.assertEqual(attachment["status"], "extracted")
        self.assertIn("2026-10-08", result["attachment_text"])
        self.assertTrue(Path(attachment["text_path"]).exists())

    def test_executable_attachment_is_never_written(self) -> None:
        message = EmailMessage()
        message.set_content("Invoice attached")
        message.add_attachment(
            b"MZ-not-a-real-executable",
            maintype="application",
            subtype="octet-stream",
            filename="invoice.exe",
        )

        result = extract(message, self.root)

        attachment = result["attachments"][0]
        self.assertEqual(attachment["status"], "blocked_executable_type")
        self.assertIsNone(attachment["stored_path"])

    def test_office_attachments_are_extracted_without_running_office(self) -> None:
        message = EmailMessage()
        message.set_content("Documents attached")
        message.add_attachment(
            zip_payload({
                "word/document.xml": '<w:document xmlns:w="urn:w"><w:p><w:t>DOCX deadline 2026-10-20</w:t></w:p></w:document>',
            }),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename="brief.docx",
        )
        message.add_attachment(
            zip_payload({
                "xl/sharedStrings.xml": '<sst xmlns="urn:x"><si><t>Budget</t></si></sst>',
                "xl/worksheets/sheet1.xml": '<worksheet xmlns="urn:x"><sheetData><row><c r="A1" t="s"><v>0</v></c><c r="B1"><v>42</v></c></row></sheetData></worksheet>',
            }),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="budget.xlsx",
        )
        message.add_attachment(
            zip_payload({
                "ppt/slides/slide1.xml": '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:t>Presentation date 2026-10-21</a:t></p:sld>',
            }),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.presentationml.presentation",
            filename="slides.pptx",
        )

        result = extract(message, self.root)

        self.assertTrue(all(item["status"] == "extracted" for item in result["attachments"]))
        self.assertIn("DOCX deadline 2026-10-20", result["attachment_text"])
        self.assertIn("A1: Budget", result["attachment_text"])
        self.assertIn("Slide 1", result["attachment_text"])

    def test_inline_images_do_not_consume_document_attachment_limit(self) -> None:
        message = EmailMessage()
        message.set_content("Newsletter with a useful document")
        for index in range(12):
            message.add_attachment(
                PNG_1X1 + bytes([index]),
                maintype="image",
                subtype="png",
                filename=f"inline-{index}.png",
                disposition="inline",
                cid=f"<image-{index}>",
            )
        message.add_attachment(
            zip_payload({
                "word/document.xml": '<w:document xmlns:w="urn:w"><w:t>Important attached brief</w:t></w:document>',
            }),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename="important.docx",
        )

        result = extract(message, self.root, max_attachment_count=1, max_inline_image_count=2)

        document = next(item for item in result["attachments"] if item["filename"] == "important.docx")
        inline_images = [item for item in result["attachments"] if item["filename"].startswith("inline-")]
        self.assertEqual(document["status"], "extracted")
        self.assertEqual(sum(item["status"] == "saved" for item in inline_images), 2)
        self.assertEqual(sum(item["status"] == "skipped_inline_image_limit" for item in inline_images), 10)

    def test_attachment_filename_cannot_escape_output_directory(self) -> None:
        message = EmailMessage()
        message.set_content("Attached")
        message.add_attachment(
            b"safe",
            maintype="text",
            subtype="plain",
            filename="../../outside.txt",
        )

        result = extract(message, self.root)

        attachment = result["attachments"][0]
        self.assertEqual(attachment["filename"], "outside.txt")
        stored_path = Path(attachment["stored_path"]).resolve()
        self.assertTrue(stored_path.is_relative_to((self.root / "attachments").resolve()))

    @unittest.skipIf(os.name == "nt", "Windows does not enforce POSIX mode bits")
    def test_written_attachments_are_private(self) -> None:
        message = EmailMessage()
        message.set_content("Attached")
        message.add_attachment(b"private", maintype="text", subtype="plain", filename="private.txt")

        result = extract(message, self.root)
        mode = Path(result["attachments"][0]["stored_path"]).stat().st_mode & 0o777

        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
