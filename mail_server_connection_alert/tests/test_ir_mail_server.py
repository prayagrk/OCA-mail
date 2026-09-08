# Copyright 2026 CIT-Services
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase


class TestIrMailServerReminders(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin_group = cls.env.ref("base.group_system")
        cls.admin_user = cls.env["res.users"].create(
            {
                "name": "Mail Test Admin",
                "login": "mail_test_admin",
                "email": "admin@example.com",
                "groups_id": [(6, 0, [cls.admin_group.id])],
            }
        )

        cls.outgoing_server = cls.env["ir.mail_server"].create(
            {
                "name": "Test Outgoing Server",
                "smtp_host": "smtp.test.com",
                "smtp_port": 25,
                "active": True,
            }
        )

        cls.incoming_server = cls.env["fetchmail.server"].create(
            {
                "name": "Test Incoming Server",
                "server_type": "imap",
                "server": "imap.test.com",
                "active": True,
            }
        )

    def test_process_reminders_detects_failures_and_notifies(self):
        """Simulate SMTP and IMAP connection errors and
        verify alerts are created."""
        mail_server_cls = type(self.env["ir.mail_server"])
        fetchmail_cls = type(self.env["fetchmail.server"])
        bus_cls = type(self.env["bus.bus"])

        with (
            patch.object(
                mail_server_cls,
                "test_smtp_connection",
                side_effect=Exception("SMTP Auth Failed"),
            ),
            patch.object(
                fetchmail_cls, "connect", side_effect=Exception("IMAP Timeout")
            ),
            patch.object(bus_cls, "_sendone") as mock_sendone,
        ):
            self.env["ir.mail_server"]._process_reminders()

            self.assertTrue(mock_sendone.called)
            sent_types = [call[0][1] for call in mock_sendone.call_args_list]
            self.assertIn("simple_notification", sent_types)

            channel = self.env["discuss.channel"].search(
                [("name", "=", "⚠️ System Mail Alerts")], limit=1
            )
            self.assertTrue(channel, "Alert channel should be created.")
            self.assertIn(
                self.admin_user.partner_id,
                channel.channel_partner_ids,
                "Admin partner must be in the channel.",
            )

            latest_message = channel.message_ids.sorted("id", reverse=True)[:1]
            self.assertTrue(latest_message, "A warning message should be posted.")
            self.assertIn("Test Outgoing Server", latest_message.body)
            self.assertIn("Test Incoming Server", latest_message.body)

    def test_process_reminders_success_no_alerts(self):
        """Verify no notifications are dispatched when all servers connect cleanly."""
        mail_server_cls = type(self.env["ir.mail_server"])
        fetchmail_cls = type(self.env["fetchmail.server"])
        bus_cls = type(self.env["bus.bus"])

        with (
            patch.object(mail_server_cls, "test_smtp_connection", return_value=True),
            patch.object(fetchmail_cls, "connect", return_value=MagicMock()),
            patch.object(bus_cls, "_sendone") as mock_sendone,
        ):
            self.env["ir.mail_server"]._process_reminders()

            mock_sendone.assert_not_called()

    def test_incoming_mail_connection_cleanup_imap(self):
        """Ensure active IMAP connection is properly closed in the finally block."""
        fetchmail_cls = type(self.env["fetchmail.server"])
        mock_connection = MagicMock()

        with (
            patch.object(
                type(self.env["ir.mail_server"]),
                "test_smtp_connection",
                return_value=True,
            ),
            patch.object(fetchmail_cls, "search", return_value=self.incoming_server),
            patch.object(fetchmail_cls, "connect", return_value=mock_connection),
        ):
            self.env["ir.mail_server"]._process_reminders()

            mock_connection.close.assert_called_once()
            mock_connection.quit.assert_not_called()

    def test_incoming_mail_connection_cleanup_error_handled(self):
        """Ensure errors during connection cleanup are safely logged and silenced."""
        fetchmail_cls = type(self.env["fetchmail.server"])
        mock_connection = MagicMock()
        mock_connection.close.side_effect = Exception("Socket teardown failed")

        with (
            patch.object(
                type(self.env["ir.mail_server"]),
                "test_smtp_connection",
                return_value=True,
            ),
            patch.object(fetchmail_cls, "connect", return_value=mock_connection),
        ):
            # Must run cleanly without raising the close() exception
            try:
                self.env["ir.mail_server"]._process_reminders()
            except Exception as exc:
                self.fail(
                    f"_process_reminders raised an exception during cleanup: {exc}"
                )
