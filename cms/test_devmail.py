from unittest import mock

from django.core.mail import EmailMessage
from django.test import SimpleTestCase, override_settings

from cms.devmail import AllowlistBackend


class Allowlist(SimpleTestCase):
    def run_backend(self, *messages, allow="me@example.com"):
        with mock.patch.dict("os.environ", {"TEST_REAL_MAIL_TO": allow}), \
             mock.patch("cms.devmail.PostmarkBackend") as pm, mock.patch("cms.devmail.FileBackend") as fb, \
             override_settings(EMAIL_FILE_PATH="/tmp/x"):
            pm.return_value.send_messages.side_effect = lambda ms: len(ms)
            fb.return_value.send_messages.side_effect = lambda ms: len(ms)
            AllowlistBackend().send_messages(list(messages))
            return pm.return_value.send_messages.call_args, fb.return_value.send_messages.call_args

    def test_only_listed_addresses_are_really_sent(self):
        mine, other = EmailMessage("a", "b", to=["ME@example.com"]), EmailMessage("a", "b", to=["ada@acme.com"])
        real, filed = self.run_backend(mine, other)
        self.assertEqual(real[0][0], [mine])
        self.assertEqual(filed[0][0], [other])

    def test_a_message_with_any_unlisted_recipient_stays_a_file(self):
        mixed = EmailMessage("a", "b", to=["me@example.com"], cc=["ada@acme.com"])
        real, filed = self.run_backend(mixed)
        self.assertIsNone(real)
        self.assertEqual(filed[0][0], [mixed])

    def test_nothing_listed_means_nothing_is_sent(self):
        real, filed = self.run_backend(EmailMessage("a", "b", to=["me@example.com"]), allow="")
        self.assertIsNone(real)
        self.assertIsNotNone(filed)
