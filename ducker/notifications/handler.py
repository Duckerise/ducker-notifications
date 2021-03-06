import logging
import re
import sys
from functools import cached_property
from typing import Callable, Dict, TypeVar

from django.db.models import Model

from .exceptions import (
    RelatedEventNotFound,
    RelatedSenderClassNotFound,
    RelatedUserNotFound,
    SenderSlugNotFoundException,
)
from .models import Medium, NotificationEvent
from .utils import UserModel, is_installed

if sys.version_info >= (3, 3):
    from collections.abc import Iterable
else:
    from collections import Iterable


SenderType = TypeVar("SenderType", bound="NotificationSender")


class NotificationHandler:
    def __init__(self, ref_obj: Model, identifier: str) -> None:
        self.ref_obj = ref_obj
        self.identifier = identifier

    @cached_property
    def user(self) -> UserModel:
        if isinstance(self.ref_obj, UserModel):
            return self.ref_obj

        user = getattr(self.ref_obj, "user", None)

        if not user:
            raise RelatedUserNotFound("Reference object has not `user` attribute. ")

        return user

    @cached_property
    def event(self) -> NotificationEvent:
        notification = (
            NotificationEvent.objects.filter(identifier=self.identifier)
            .prefetch_related("mediums")
            .first()
        )

        if not notification:
            raise RelatedEventNotFound(
                "No event found for identifier {}".format(self.identifier)
            )

        return notification

    def replace_variables(self, text: str) -> str:
        replaced_text = text

        # Match `{{     ANY_TEXT }}`
        pattern = re.compile(r"({{\s*)(\w+)(\s*}})")
        matches = pattern.finditer(replaced_text)

        for match in matches:
            replaced_str = "".join(match.groups())
            field_name = match.group(2)

            replaced_text = replaced_text.replace(
                replaced_str,
                getattr(self.ref_obj, field_name, ""),
            )

        return replaced_text

    def generate_text_for_medium(self, medium: Medium):
        raw_text = self.event.get_text_for_medium(medium)
        return self.replace_variables(raw_text)

    @cached_property
    def all_senders(self) -> Dict[str, SenderType]:
        return {klass.SLUG: klass for klass in NotificationSender.__subclasses__()}

    def get_sender(self, medium: Medium) -> SenderType:
        klass = self.all_senders.get(medium.slug)

        if not klass:
            raise RelatedSenderClassNotFound(
                "Class for sending notification via {} not found. "
                "Class should have class attribute SLUG of {}, and it "
                "should be a subclass of NotificationSender class".format(
                    medium.label,
                    medium.slug,
                )
            )

        # return instance of sender class
        return klass()

    def get_sender_function(self, medium: Medium) -> Callable:
        func = getattr(self.get_sender(medium), "send", None)

        if not func:
            raise Exception

        if not callable(func):
            raise Exception

        return func

    def handle_followup_events(self):
        pass

    def send(self) -> None:
        for medium in self.event.mediums.all():
            text = self.generate_text_for_medium(medium)
            sender_function = self.get_sender_function(medium)
            sender_function(self.user, text)
            self.handle_followup_events()


class NotificationSender:
    SLUG = None

    def __init__(self) -> None:
        if not self.SLUG:
            raise SenderSlugNotFoundException("Class did not define SLUG!")

    def send(self, user, text):
        raise NotImplementedError

    def bulk_send(self, users, texts):
        for user, text in zip(users, texts):
            self.send(user, text)
