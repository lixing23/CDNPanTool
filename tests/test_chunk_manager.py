import tempfile
import unittest
from pathlib import Path

from chunk_manager import cleanup_dir, merge_chunks, sha256_file, split_file


class ChunkManagerTest(unittest.TestCase):
    def test_split_and_merge_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.bin"
            chunks_dir = root / "chunks"
            merged = root / "merged.bin"
            source.write_bytes(bytes(range(256)) * 10)

            chunks = split_file(source, chunks_dir, 300)
            merge_chunks([chunk.path for chunk in chunks], merged, sha256_file(source))

            self.assertEqual(len(chunks), 9)
            self.assertEqual(merged.read_bytes(), source.read_bytes())
            self.assertEqual(chunks[0].index, 0)
            self.assertEqual(chunks[-1].size, 160)

    def test_merge_rejects_bad_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.bin"
            chunks_dir = root / "chunks"
            merged = root / "merged.bin"
            source.write_bytes(b"abcdef")
            chunks = split_file(source, chunks_dir, 2)

            with self.assertRaises(ValueError) as context:
                merge_chunks([chunk.path for chunk in chunks], merged, "0" * 64)

            self.assertIn("原文件 SHA-256 校验失败", str(context.exception))

    def test_cleanup_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "target"
            target.mkdir()
            (target / "a.txt").write_text("a", encoding="utf-8")

            cleanup_dir(target)

            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
