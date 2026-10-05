import smtplib

from django.conf import settings
from django.core.mail import send_mail, BadHeaderError
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Send a quick test email using current SMTP settings"

    def add_arguments(self, parser):
        parser.add_argument(
            "--to",
            required=True,
            help="Destination email address",
        )
        parser.add_argument(
            "--subject",
            default="Auto Reminder SMTP test",
            help="Subject line for the test email",
        )
        parser.add_argument(
            "--body",
            default="This is a test email from Auto Reminder using current SMTP settings.",
            help="Body content for the test email",
        )

    def handle(self, *args, **options):
        to_addr = options["to"]
        subject = options["subject"]
        body = options["body"]

        if not settings.EMAIL_HOST_USER:
            raise CommandError("EMAIL_HOST_USER is not configured")

        try:
            sent = send_mail(
                subject,
                body,
                settings.EMAIL_FROM_ADDRESS or settings.EMAIL_HOST_USER,
                [to_addr],
                fail_silently=False,
            )
        except BadHeaderError as exc:
            raise CommandError(f"Invalid email header: {exc}") from exc
        except smtplib.SMTPException as exc:
            raise CommandError(f"SMTP error: {exc}") from exc
        except ValueError as exc:
            raise CommandError(f"Email error: {exc}") from exc

        if sent:
            self.stdout.write(self.style.SUCCESS(f"Test email sent to {to_addr}"))
        else:
            raise CommandError("Send mail returned 0; no email sent")
