import tempfile
import unittest
from pathlib import Path

from manifest_codec import ChunkManifest, ChunkRecord, load_manifest_file, save_manifest_file, validate_manifest


class ManifestCodecTest(unittest.TestCase):
    def test_save_load_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "xfb_manifest.json"
            manifest = ChunkManifest(
                name="big.zip",
                size=10,
                sha256="a" * 64,
                chunk_size=5,
                chunks=[
                    ChunkRecord(index=0, size=5, sha256="b" * 64, url="https://example.com/0", stego=True),
                    ChunkRecord(index=1, size=5, sha256="c" * 64, url="https://example.com/1", stego=False),
                ],
            )

            save_manifest_file(manifest, path)
            loaded = load_manifest_file(path)

            self.assertEqual(loaded.name, "big.zip")
            self.assertEqual(len(loaded.chunks), 2)
            self.assertTrue(loaded.chunks[0].stego)
            self.assertFalse(loaded.chunks[1].stego)

    def test_rejects_bad_chunk_count(self):
        manifest = ChunkManifest(name="x", size=1, sha256="a" * 64, chunk_size=1, chunks=[])
        data = manifest.to_dict()
        data["chunk_count"] = 2

        with self.assertRaises(ValueError) as context:
            validate_manifest(data)

        self.assertIn("分片数量不一致", str(context.exception))

    def test_rejects_non_continuous_index(self):
        manifest = ChunkManifest(
            name="x",
            size=2,
            sha256="a" * 64,
            chunk_size=1,
            chunks=[ChunkRecord(index=1, size=1, sha256="b" * 64, url="https://example.com/1", stego=True)],
        )

        with self.assertRaises(ValueError) as context:
            validate_manifest(manifest.to_dict())

        self.assertIn("分片序号不连续", str(context.exception))


if __name__ == "__main__":
    unittest.main()
