import tempfile
import unittest
from pathlib import Path

from stego_container import create_container, extract_container, inspect_container


class StegoContainerTest(unittest.TestCase):
    def test_create_inspect_extract_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cover = root / "cover.png"
            source = root / "资料.bin"
            output = root / "output.png"
            extracted = root / "extracted.bin"

            cover.write_bytes(b"\x89PNG\r\n\x1a\ncover-bytes")
            source.write_bytes(b"hello\x00world" * 20)

            metadata = create_container(cover, source, output)
            inspected = inspect_container(output)
            extracted_metadata = extract_container(output, extracted)

            self.assertEqual(metadata.name, "资料.bin")
            self.assertEqual(inspected.name, "资料.bin")
            self.assertEqual(inspected.size, source.stat().st_size)
            self.assertEqual(extracted_metadata.sha256, metadata.sha256)
            self.assertEqual(extracted.read_bytes(), source.read_bytes())

    def test_extract_rejects_invalid_container(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invalid = root / "invalid.png"
            output = root / "out.bin"
            invalid.write_bytes(b"not-a-container")

            with self.assertRaises(ValueError) as context:
                extract_container(invalid, output)

            self.assertIn("不是有效伪装图片", str(context.exception))

    def test_extract_rejects_tampered_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cover = root / "cover.png"
            source = root / "source.txt"
            output = root / "output.png"
            extracted = root / "extracted.txt"

            cover.write_bytes(b"cover")
            source.write_text("hello", encoding="utf-8")
            create_container(cover, source, output)

            data = bytearray(output.read_bytes())
            data[-80] = data[-80] ^ 1
            output.write_bytes(bytes(data))

            with self.assertRaises(ValueError) as context:
                extract_container(output, extracted)

            self.assertTrue("校验失败" in str(context.exception) or "不是有效伪装图片" in str(context.exception))


if __name__ == "__main__":
    unittest.main()
