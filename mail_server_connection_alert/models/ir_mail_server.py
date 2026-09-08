# Copyright 2026 CIT-Services
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from markupsafe import Markup

from odoo import Command, api, models

_logger = logging.getLogger(__name__)


class IrMailServer(models.Model):
    _inherit = "ir.mail_server"

    @api.model
    def _process_reminders(self):
        outgoing_failures = []
        outgoing_mail_url = []
        incoming_failures = []
        incoming_mail_url = []

        base_url = self.get_base_url()

        active_outgoing = self.search([("active", "=", True)])
        if not active_outgoing:
            outgoing_failures.append(
                "No active outgoing mail servers found in system settings."
            )
        else:
            for server in active_outgoing:
                try:
                    server.test_smtp_connection()
                except Exception as e:
                    _logger.warning(
                        "Outgoing mail server %s (ID: %s) connection failed: %s",
                        server.name,
                        server.id,
                        e,
                    )
                    outgoing_failures.append(
                        f"{server.name} (ID: {server.id}) is not working properly."
                    )
                    outgoing_mail_url.append(
                        f"{base_url}/odoo/{server._name}/{server.id}"
                    )

        incoming_servers = self.env["fetchmail.server"].search(
            [
                ("active", "=", True),
                ("server_type", "!=", "local"),
            ]
        )
        for inc_server in incoming_servers:
            connection = None
            try:
                connection = inc_server.connect()
            except Exception as e:
                _logger.warning(
                    "Incoming mail server %s (ID: %s) connection failed: %s",
                    inc_server.name,
                    inc_server.id,
                    e,
                )
                incoming_failures.append(
                    f"{inc_server.name} (ID: {inc_server.id}) is not working properly."
                )
                incoming_mail_url.append(
                    f"{base_url}/odoo/{inc_server._name}/{inc_server.id}"
                )
            finally:
                try:
                    if connection:
                        connection_type = inc_server.server_type
                        if connection_type == "imap":
                            connection.close()
                        elif connection_type == "pop":
                            connection.quit()
                except Exception as e:
                    _logger.debug("Failed to close incoming mail connection: %s", e)

        if outgoing_failures or incoming_failures:
            self._notify_system_admins(
                outgoing_failures,
                incoming_failures,
                outgoing_mail_url,
                incoming_mail_url,
            )

    def _notify_system_admins(
        self, outgoing_failures, incoming_failures, outgoing_mail_url, incoming_mail_url
    ):
        admin_group = self.env.ref("base.group_system")
        admin_partners = admin_group.users.mapped("partner_id")

        if not admin_partners:
            return

        odoobot_partner = self.env.ref("base.partner_root")
        all_members = admin_partners | odoobot_partner

        outgoing_bus_items = []
        incoming_bus_items = []
        if outgoing_failures:
            for msg in outgoing_failures:
                outgoing_bus_items.append(f"[Outgoing] {msg}")
        if incoming_failures:
            for msg in incoming_failures:
                incoming_bus_items.append(f"[Incoming] {msg}")
        outgoing_notification_title = "⚠️ Outgoing Mail System Warning"
        outgoing_notification_message = Markup("<br/>").join(outgoing_bus_items)

        incoming_notification_title = "⚠️ Incoming Mail System Warning"
        incoming_notification_message = Markup("<br/>").join(incoming_bus_items)

        for partner in admin_partners:
            if outgoing_failures:
                self.env["bus.bus"]._sendone(
                    partner,
                    "simple_notification",
                    {
                        "title": outgoing_notification_title,
                        "message": outgoing_notification_message,
                        "type": "danger",
                        "sticky": True,
                    },
                )
            if incoming_failures:
                self.env["bus.bus"]._sendone(
                    partner,
                    "simple_notification",
                    {
                        "title": incoming_notification_title,
                        "message": incoming_notification_message,
                        "type": "danger",
                        "sticky": True,
                    },
                )

        channel = self.env.ref(
            "mail_server_connection_alert.channel_system_mail_alerts"
        ).sudo()

        channel.channel_partner_ids = [Command.link(pid) for pid in all_members.ids]

        html_sections = []
        if outgoing_failures:
            items = "".join(
                [
                    Markup(f'<li><a href="{url}">{msg}</a></li>')
                    for msg, url in zip(
                        outgoing_failures, outgoing_mail_url, strict=False
                    )
                ]
            )
            html_sections.append(
                f"<p><b>Outgoing Mail Server(s):</b></p><ul>{items}</ul>"
            )
        if incoming_failures:
            items = "".join(
                [
                    Markup(f'<li><a href="{url}">{msg}</a></li>')
                    for msg, url in zip(
                        incoming_failures, incoming_mail_url, strict=False
                    )
                ]
            )
            html_sections.append(
                f"<br/><p><b>Incoming Mail Server(s):</b></p><ul>{items}</ul>"
            )

        formatted_body = Markup(
            "<h3>⚠️ Mail System Warning</h3>" + "".join(html_sections)
        )

        channel.sudo().message_post(
            body=formatted_body,
            author_id=odoobot_partner.id,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
