import socket
import unittest
from urllib import error

from oss_client import is_retryable_upload_error


class OssClientTest(unittest.TestCase):
    def test_10054_is_retryable(self):
        self.assertTrue(is_retryable_upload_error(ConnectionResetError(10054, "reset")))

    def test_timeout_is_retryable(self):
        self.assertTrue(is_retryable_upload_error(TimeoutError("timeout")))
        self.assertTrue(is_retryable_upload_error(socket.timeout("timeout")))

    def test_http_500_is_retryable(self):
        exc = error.HTTPError("https://example.com", 500, "server", {}, None)
        self.assertTrue(is_retryable_upload_error(exc))

    def test_http_400_is_not_retryable(self):
        exc = error.HTTPError("https://example.com", 400, "bad", {}, None)
        self.assertFalse(is_retryable_upload_error(exc))


if __name__ == "__main__":
    unittest.main()
