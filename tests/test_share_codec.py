import unittest

from share_codec import SharePayload, decode_share_text, encode_share_text


class ShareCodecTest(unittest.TestCase):
    def test_round_trip(self):
        payload = SharePayload(
            url="https://example.com/a/b/c.png",
            name="资料.zip",
            size=123456,
        )

        text = encode_share_text(payload)
        decoded = decode_share_text(text)

        self.assertTrue(text.startswith("xfb1."))
        self.assertEqual(decoded.url, payload.url)
        self.assertEqual(decoded.name, payload.name)
        self.assertEqual(decoded.size, payload.size)

    def test_rejects_invalid_prefix(self):
        with self.assertRaises(ValueError) as context:
            decode_share_text("bad.abc")

        self.assertIn("分享文本前缀错误", str(context.exception))

    def test_rejects_invalid_payload(self):
        with self.assertRaises(ValueError) as context:
            decode_share_text("xfb1.invalid")

        self.assertIn("分享文本解析失败", str(context.exception))

    def test_rejects_unsupported_version(self):
        text = encode_share_text(SharePayload(url="https://example.com/file.png", name="file.bin", size=1))
        raw = decode_share_text(text)
        self.assertEqual(raw.url, "https://example.com/file.png")


if __name__ == "__main__":
    unittest.main()
