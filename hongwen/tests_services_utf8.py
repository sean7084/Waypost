"""Regression: provider responses must decode as UTF-8, not requests' Latin-1 default.

The httpx->requests port introduced a mojibake regression: these LLM APIs return
UTF-8 but omit charset (``text/event-stream`` / ``text/plain``), and requests
defaults such responses to ISO-8859-1. ``iter_lines(decode_unicode=True)`` then
reads UTF-8 Chinese bytes as Latin-1 and the SSE layer re-encodes them to UTF-8,
double-encoding every character (你 e4bda0 -> c3a4c2bdc2a0 = "ä½ å¥½"). The fix is a
session response hook forcing ``encoding='utf-8'`` on every response.
"""
import requests
from django.test import SimpleTestCase

from hongwen.services.llm import dashscope_client, volcengine_client


def _sse_response():
    """A real requests.Response shaped like a charset-less UTF-8 SSE reply."""
    response = requests.Response()
    response.status_code = 200
    response.headers['Content-Type'] = 'text/event-stream'  # no charset
    response._content = 'data: {"text": "你好，泓问"}\n\n'.encode('utf-8')
    response._content_consumed = True  # iter_lines reads cached _content, not raw
    # Mimic what requests.Session.send does: derive encoding from headers.
    response.encoding = requests.utils.get_encoding_from_headers(response.headers)
    return response


class ProviderUtf8DecodingTests(SimpleTestCase):
    def test_requests_default_reproduces_mojibake(self):
        response = _sse_response()
        self.assertEqual(response.encoding, 'ISO-8859-1')
        line = next(response.iter_lines(decode_unicode=True))
        self.assertIn('ä½', line)  # the bug: UTF-8 read as Latin-1 (你 -> ä½\xa0)
        self.assertNotIn('你好', line)

    def test_force_utf8_hook_fixes_streaming_decode(self):
        for module in (dashscope_client, volcengine_client):
            response = _sse_response()
            module._force_utf8(response)
            self.assertEqual(response.encoding, 'utf-8')
            line = next(response.iter_lines(decode_unicode=True))
            self.assertIn('你好，泓问', line)
            self.assertNotIn('ä½', line)

    def test_force_utf8_fixes_text_and_json(self):
        response = _sse_response()
        response.headers['Content-Type'] = 'text/plain'
        response._content = '你好'.encode('utf-8')
        response.encoding = requests.utils.get_encoding_from_headers(response.headers)
        dashscope_client._force_utf8(response)
        self.assertEqual(response.text, '你好')

        json_response = requests.Response()
        json_response.status_code = 200
        json_response.headers['Content-Type'] = 'text/plain'  # not application/json
        json_response._content = '{"reply": "你好"}'.encode('utf-8')
        json_response.encoding = requests.utils.get_encoding_from_headers(json_response.headers)
        volcengine_client._force_utf8(json_response)
        self.assertEqual(json_response.json()['reply'], '你好')

    def test_sessions_register_the_hook(self):
        for module in (dashscope_client, volcengine_client):
            module.close_client()
            session = module._get_session()
            self.assertIn(module._force_utf8, session.hooks['response'])
            module.close_client()