import datetime
import logging
import os


from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMultiAlternatives
from django.db import models
from django.utils.encoding import python_2_unicode_compatible
from django.utils.translation import ugettext_lazy as _
from django.utils import timezone

from . import defaults
from .utils import get_storage, upload_to

logger = logging.getLogger(__name__)


class MailerMessageManager(models.Manager):
    def send_queued(self, limit=None):
        if limit is None:
            limit = getattr(settings, 'MAILQUEUE_LIMIT', defaults.MAILQUEUE_LIMIT)

        for email in self.filter(sent=False)[:limit]:
            email.send_mail()

    def clear_sent_messages(self, offset=None):
        """ Deletes sent MailerMessage records """
        if offset is None:
            offset = getattr(settings, 'MAILQUEUE_CLEAR_OFFSET', defaults.MAILQUEUE_CLEAR_OFFSET)

        if type(offset) is int:
            offset = datetime.timedelta(hours=offset)

        delete_before = timezone.now() - offset
        self.filter(sent=True, last_attempt__lte=delete_before).delete()


@python_2_unicode_compatible
class MailerMessage(models.Model):
    created = models.DateTimeField(_('Created'), auto_now_add=True, auto_now=False,
                                   editable=False, null=True)
    subject = models.CharField(_('Subject'), max_length=250, blank=True)
    to_address = models.TextField(_('To'))
    cc_address = models.TextField(_('CC'), blank=True)
    bcc_address = models.TextField(_('BCC'), blank=True)
    from_address = models.EmailField(_('From'), max_length=250)
    reply_to = models.TextField(_('Reply to'), max_length=250, blank=True, null=True)
    content = models.TextField(_('Content'), blank=True)
    html_content = models.TextField(_('HTML Content'), blank=True)
    app = models.CharField(_('App'), max_length=250, blank=True)
    sent = models.BooleanField(_('Sent'), default=False, editable=False)
    last_attempt = models.DateTimeField(_('Last attempt'), auto_now=False, auto_now_add=False,
                                        blank=True, null=True, editable=False)

    objects = MailerMessageManager()

    class Meta:
        verbose_name = _('Message')
        verbose_name_plural = _('Messages')

    def __str__(self):
        return self.subject

    def add_attachment(self, attachment):
        """
        Takes a Django `File` object and creates an attachment for this mailer message.
        """
        if self.pk is None:
            self._save_without_sending()

        original_filename = attachment.file.name.split(os.sep)[-1]
        file_content = ContentFile(attachment.read())

        new_attachment = Attachment()
        new_attachment.file_attachment.save(original_filename, file_content, save=False)
        new_attachment.email = self
        new_attachment.original_filename = original_filename
        try:
            new_attachment.save()
        except Exception as e:
            logger.error(e)
            new_attachment.file_attachment.delete()

    def _save_without_sending(self, *args, **kwargs):
        """
        Saves the MailerMessage instance without sending the e-mail. This ensures
        other models (e.g. `Attachment`) have something to relate to in the database.
        """
        self.do_not_send = True
        super(MailerMessage, self).save(*args, **kwargs)

    def send_mail(self):
        """ Public api to send mail.  Makes the determinination
         of using celery or not and then calls the appropriate methods.
        """

        if getattr(settings, 'MAILQUEUE_CELERY', defaults.MAILQUEUE_CELERY):
            from mailqueue.tasks import send_mail
            send_mail.delay(self.pk)
        else:
            self._send()

    def _send(self):
        if not self.sent:
            self.last_attempt = timezone.now()

            subject, from_email = self.subject, self.from_address
            text_content = self.content

            msg = EmailMultiAlternatives(subject, text_content, from_email)

            if self.reply_to:
                msg.reply_to = [email.strip() for email in self.reply_to.split(',')
                                if email.strip()]

            if self.html_content:
                html_content = self.html_content
                msg.attach_alternative(html_content, "text/html")

            msg.to = [email.strip() for email in self.to_address.split(',') if email.strip()]
            msg.cc = [email.strip() for email in self.cc_address.split(',') if email.strip()]
            msg.bcc = [email.strip() for email in self.bcc_address.split(',') if email.strip()]

            # Add any additional attachments
            for attachment in self.attachment_set.all():

                # django-storages S3Boto3Storage compatibility
                if attachment.file_attachment.file.__class__.__name__ == 'S3Boto3StorageFile':
                    self._attach_s3_file(msg, attachment)
                else:
                    self._attach_regular_file(msg, attachment)
            try:
                msg.send()
                self.sent = True
            except Exception as e:
                self.do_not_send = True
                logger.error('Mail Queue Exception: {0}'.format(e))
            self.save()

    def _attach_s3_file(self, msg, attachment):
        content = attachment.file_attachment.read()
        msg.attach(attachment.original_filename, content, None)

    def _attach_regular_file(self, msg, attachment):
        path = attachment.file_attachment.path
        if os.path.isfile(path):
            with open(path, 'rb') as f:
                content = f.read()
            msg.attach(attachment.original_filename, content, None)


@python_2_unicode_compatible
class Attachment(models.Model):
    file_attachment = models.FileField(storage=get_storage(), upload_to=upload_to,
                                       blank=True, null=True)
    # Note: for original_filename, null=True is defined only to simplify the migration to the new version of this 
    # package, having legacy data set. original_filename is actually required when adding attachment to an email 
    # (it is used in the _send method)
    original_filename = models.CharField(default=None, max_length=250, blank=False, null=True)
    email = models.ForeignKey(MailerMessage, on_delete=models.CASCADE, blank=True, null=True)

    class Meta:
        verbose_name = _('Attachment')
        verbose_name_plural = _('Attachments')

    def __str__(self):
        return str(self.original_filename)
