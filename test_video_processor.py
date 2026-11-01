import io
import os
import unittest
from unittest.mock import patch

from video_processor import VideoProcessor, video_error_message


class VideoProcessorTests(unittest.TestCase):
    @patch.dict(os.environ, {'VIDEO_PROXY': 'socks5://127.0.0.1:1080', 'VIDEO_IMPERSONATE': 'chrome'})
    @patch('video_processor.yt_dlp.YoutubeDL')
    def test_subtitles_use_same_client_and_proxy_as_metadata(self, factory):
        ydl = factory.return_value.__enter__.return_value
        ydl.extract_info.return_value = {
            'title': 'Example',
            'subtitles': {'ru': [{'ext': 'vtt', 'url': 'https://example.com/subs'}]},
        }
        ydl.urlopen.return_value = io.BytesIO(
            'WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.000\nПример\n'.encode()
        )
        result = VideoProcessor().extract_video_info('https://example.com/video')
        self.assertEqual(result['subtitles'], 'Пример')
        ydl.urlopen.assert_called_once_with('https://example.com/subs')
        opts = factory.call_args.args[0]
        self.assertEqual(opts['proxy'], 'socks5://127.0.0.1:1080')
        self.assertEqual(opts['impersonate'].client, 'chrome')
        self.assertFalse(opts.get('nocheckcertificate', False))

    @patch.dict(os.environ, {'VIDEO_PROXY': '', 'VIDEO_IMPERSONATE': 'none'})
    @patch('video_processor.yt_dlp.YoutubeDL')
    def test_automatic_captions_and_disabled_impersonation(self, factory):
        ydl = factory.return_value.__enter__.return_value
        ydl.extract_info.return_value = {
            'automatic_captions': {'ru': [{'ext': 'vtt', 'url': 'https://example.com/auto'}]},
        }
        ydl.urlopen.return_value = io.BytesIO(b'WEBVTT\n\nAutomatic captions')
        result = VideoProcessor().extract_video_info('https://example.com/video')
        self.assertEqual(result['subtitles'], 'Automatic captions')
        self.assertNotIn('proxy', factory.call_args.args[0])
        self.assertNotIn('impersonate', factory.call_args.args[0])

    def test_failed_subtitles_do_not_silently_produce_description_only(self):
        from unittest.mock import Mock
        ydl = Mock()
        ydl.urlopen.side_effect = RuntimeError('[SSL: INVALID_SESSION_ID]')
        with self.assertRaisesRegex(RuntimeError, 'INVALID_SESSION_ID'):
            VideoProcessor()._extract_subtitle_text(
                ydl, [{'ext': 'vtt', 'url': 'https://example.com/subs'}],
            )

    @patch('video_processor.yt_dlp.YoutubeDL')
    def test_automatic_captions_are_tried_when_manual_subtitles_fail(self, factory):
        ydl = factory.return_value.__enter__.return_value
        ydl.extract_info.return_value = {
            'subtitles': {'ru': [{'ext': 'vtt', 'url': 'https://example.com/manual'}]},
            'automatic_captions': {'ru': [{'ext': 'vtt', 'url': 'https://example.com/auto'}]},
        }
        ydl.urlopen.side_effect = [RuntimeError('HTTP Error 403'), io.BytesIO(b'WEBVTT\n\nFallback')]
        self.assertEqual(VideoProcessor().extract_video_info('https://example.com/video')['subtitles'], 'Fallback')

    def test_ssl_error_explains_connection_settings(self):
        message = video_error_message(Exception('\x1b[0;31mERROR: [SSL: INVALID_SESSION_ID]\x1b[0m'))
        self.assertIn('VIDEO_PROXY', message)
        self.assertNotIn('\x1b', message)
        self.assertNotIn('report this issue', message)

    def test_missing_browser_transport_explains_correct_launcher(self):
        message = video_error_message(Exception('Impersonate target "chrome" is not available.'))
        self.assertIn('start_bot.bat', message)
        self.assertNotIn('Impersonate target', message)

    def test_other_errors_are_readable_and_fit_telegram_limit(self):
        message = video_error_message(Exception('\x1b[31mVideo unavailable\x1b[0m' + 'x' * 5000))
        self.assertIn('Video unavailable', message)
        self.assertNotIn('\x1b', message)
        self.assertLess(len(message), 4096)


if __name__ == '__main__':
    unittest.main()
