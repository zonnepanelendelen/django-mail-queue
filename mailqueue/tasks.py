from __future__ import absolute_import

from celery import shared_task

from .models import MailerMessage


@shared_task(name="tasks.send_mail", default_retry_delay=5, max_retries=5)
def send_mail(pk):
    message = MailerMessage.objects.get(pk=pk)
    try:
        message._send()
    except Exception as e:
        # Assuming that Django project uses anymail.backends.postmark.EmailBackend
        if e.__class__.__name__ == "AnymailRecipientsRefused":
            # Postmark API had refused recipient's email address.
            # Do not retry the task, in this case there's no need to hit API repeatedly.
            # Do not report this to Sentry. Refused recipients are tracked in Postmark UI as suppressed.
            return
        else:
            # On any other exception proceed further to retry.
            pass

    # Retry when message is not sent
    if not message.sent:
        send_mail.retry([message.pk])


@shared_task()
def clear_sent_messages():
    from mailqueue.models import MailerMessage
    MailerMessage.objects.clear_sent_messages()
