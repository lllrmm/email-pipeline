from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from email_pipeline.imap_backend import IMAP_SOCKET_TIMEOUT_SECONDS, connect_imap


class ImapBackendTests(unittest.TestCase):
    def test_connect_imap_sets_socket_timeout(self) -> None:
        client = types.SimpleNamespace(
            oauth2_login=lambda *_args: None,
            logout=lambda: None,
        )
        constructor = unittest.mock.Mock(return_value=client)
        fake_module = types.SimpleNamespace(IMAPClient=constructor)
        config = {"imap": {"username": "user", "token_command": "/bin/true"}}

        with patch.dict(sys.modules, {"imapclient": fake_module}):
            with patch("email_pipeline.imap_backend.access_token", return_value="token"):
                with connect_imap(config) as actual:
                    self.assertIs(actual, client)

        constructor.assert_called_once_with(
            "outlook.office365.com",
            port=993,
            ssl=True,
            use_uid=True,
            timeout=IMAP_SOCKET_TIMEOUT_SECONDS,
        )


if __name__ == "__main__":
    unittest.main()
