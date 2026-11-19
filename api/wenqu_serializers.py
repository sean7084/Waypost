"""Input serializers for the Wenqu customer API (PR4).

Shapes mirror the helpdesk pydantic models (app/schemas.py): MessageIn,
RegenerateIn, SpeakIn and ChatSessionUpdateIn. ``content`` stays free-form JSON
(a dict for text/mixed/form payloads, or a raw string for plain text) exactly
like the helpdesk's ``content: Any``.

Validation failures answer HTTP 422 (FastAPI's pydantic status) rather than
DRF's default 400 - see api.wenqu_views.WenquPayloadInvalid.
"""
from rest_framework import serializers


class WenquMessageInSerializer(serializers.Serializer):
    """POST /chat and /chat/stream: {type, content, session_id?}."""

    type = serializers.CharField()
    content = serializers.JSONField(required=False, default=dict)
    session_id = serializers.CharField(required=False, allow_null=True, allow_blank=True, default=None)


class WenquRegenerateInSerializer(serializers.Serializer):
    """POST /chat/regenerate and /chat/regenerate/stream: {session_id, message_id?}."""

    session_id = serializers.CharField()
    message_id = serializers.CharField(required=False, allow_null=True, allow_blank=True, default=None)


class WenquSpeakInSerializer(serializers.Serializer):
    """POST /speak and /speak/stream: {text, message_id?, language_type?}.

    ``text`` allows blank so the view can answer the helpdesk's explicit 422
    "Empty text" instead of a serializer error.
    """

    text = serializers.CharField(allow_blank=True)
    message_id = serializers.CharField(required=False, allow_null=True, allow_blank=True, default='')
    language_type = serializers.CharField(required=False, allow_null=True, allow_blank=True, default=None)


class WenquSessionUpdateSerializer(serializers.Serializer):
    """PATCH /chat/sessions/<id>: {title?} (null/blank clears the custom title)."""

    title = serializers.CharField(required=False, allow_null=True, allow_blank=True, default=None)