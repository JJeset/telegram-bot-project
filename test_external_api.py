import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from openai import APITimeoutError, APIStatusError

with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}):
    import telegram_video_bot as bot


class ExternalApiTests(unittest.TestCase):
    @patch.object(bot.client.chat.completions, 'create')
    def test_timeout_stops_analysis_instead_of_creating_error_note(self, create):
        create.side_effect = APITimeoutError(request=httpx.Request('POST', 'https://example.com'))
        video = {'title': 'Example', 'description': '', 'subtitles': 'Transcript'}
        with patch.object(bot, 'translate_to_russian') as translate:
            with self.assertRaises(bot.AnalysisError):
                bot.AIAnalyzer.analyze_video(video)
            translate.assert_not_called()

    @patch.object(bot.client.chat.completions, 'create')
    def test_billing_error_explains_balance(self, create):
        response = httpx.Response(402, request=httpx.Request('POST', 'https://example.com'))
        create.side_effect = APIStatusError('Payment required', response=response, body=None)
        with self.assertRaises(bot.AnalysisError) as caught:
            bot.analyze_with_external_api('Example')
        self.assertIn('баланс', str(caught.exception))

    @patch.object(bot.client.chat.completions, 'create')
    def test_empty_response_is_an_error(self, create):
        create.return_value = SimpleNamespace(choices=[])
        with self.assertRaises(bot.AnalysisError):
            bot.analyze_with_external_api('Example')

    @patch.object(bot.client.chat.completions, 'create')
    def test_successful_response(self, create):
        create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=' Summary '))])
        self.assertEqual(bot.analyze_with_external_api('Example'), 'Summary')
        self.assertEqual(create.call_args.kwargs['model'], bot.EXTERNAL_API_MODEL)

    @patch.object(bot.client.chat.completions, 'create')
    def test_truncation_is_detected_before_parsing_even_for_empty_content(self, create):
        for content in ('{"kind":"recipe","title":"', None):
            with self.subTest(content=content):
                create.return_value = SimpleNamespace(choices=[SimpleNamespace(
                    finish_reason='length', message=SimpleNamespace(content=content),
                )])
                with self.assertRaises(bot.IncompleteAnalysisError):
                    bot.analyze_with_external_api('Example')


if __name__ == '__main__':
    unittest.main()
