"""Получение метаданных и субтитров через один сетевой клиент yt-dlp."""

import logging
import os
from pathlib import Path
import re

import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget

logger = logging.getLogger(__name__)


def video_error_message(error: Exception) -> str:
    """Готовит сообщение для Telegram без ANSI-кодов и длинных трассировок."""
    detail = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', str(error))
    if 'impersonate target' in detail.lower() and 'not available' in detail.lower():
        return (
            '❌ В окружении запущенного бота недоступен браузерный TLS.\n\n'
            'Остановите эту копию и запустите бот через start_bot.bat в папке проекта. '
            'Для ручного запуска используйте .venv/Scripts/python.exe. '
            'Зависимости устанавливайте тем же Python, которым запускаете бот.'
        )
    if any(marker in detail.lower() for marker in (
        'invalid_session_id', 'ssl', 'tls', 'curl: (35)', 'curl: (60)',
    )):
        return (
            '❌ Не удалось установить защищённое соединение с видеосервисом.\n\n'
            'Проверьте доступ к нему с компьютера, где запущен бот. '
            'Если нужен VPN, включите его для всего компьютера или задайте '
            'VIDEO_PROXY в настройках бота и перезапустите бот. '
            'Подробности ошибки записаны в журнал.'
        )
    return (
        f'❌ Произошла ошибка при обработке видео:\n{detail[:2500]}\n\n'
        'Убедитесь, что ссылка корректна и видео доступно.'
    )


class VideoProcessor:
    """Получает информацию о видео и русские субтитры без загрузки видео."""

    def __init__(self):
        self.download_path = Path('./downloads')
        self.download_path.mkdir(exist_ok=True)

    def extract_video_info(self, url: str) -> dict:
        opts = {
            'quiet': True,
            'no_warnings': True,
            'color': 'never',
            'noplaylist': True,
            'extract_flat': False,
            'skip_download': True,
            'socket_timeout': 30,
            'extractor_retries': 2,
            'outtmpl': str(self.download_path / '%(id)s.%(ext)s'),
        }
        # curl_cffi использует браузерный TLS вместо стандартного Python SSL.
        target = os.getenv('VIDEO_IMPERSONATE', 'chrome').strip()
        if target.lower() not in ('', 'none'):
            opts['impersonate'] = ImpersonateTarget.from_str(target)
        proxy = os.getenv('VIDEO_PROXY', '').strip()
        if proxy:
            opts['proxy'] = proxy

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            subtitles_text = ''
            subtitle_error = None
            for source in ('subtitles', 'automatic_captions'):
                tracks = (info.get(source) or {}).get('ru', [])
                if tracks:
                    try:
                        subtitles_text = self._extract_subtitle_text(ydl, tracks)
                    except Exception as error:
                        subtitle_error = error
                if subtitles_text:
                    break
            if not subtitles_text and subtitle_error is not None:
                raise subtitle_error

            return {
                'title': info.get('title', 'Без названия'),
                'description': info.get('description', ''),
                'duration': info.get('duration', 0),
                'thumbnail': info.get('thumbnail', ''),
                'subtitles': subtitles_text,
                'uploader': info.get('uploader', 'Неизвестно'),
                'view_count': info.get('view_count', 0),
                'tags': info.get('tags', []),
            }

    def _extract_subtitle_text(self, ydl, subtitle_list: list) -> str:
        last_error = None
        for sub in subtitle_list:
            if sub.get('ext') not in ('vtt', 'srv3') or not sub.get('url'):
                continue
            try:
                # Сохраняем тот же прокси, cookies, TLS и тайм-аут для субтитров.
                with ydl.urlopen(sub['url']) as response:
                    text = response.read().decode('utf-8-sig')
                text = re.sub(r'<[^>]+>', '', text)
                text = re.sub(
                    r'\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}',
                    '', text,
                )
                text = re.sub(r'WEBVTT.*?\n\n', '', text, flags=re.DOTALL)
                text = '\n'.join(
                    line.strip() for line in text.split('\n')
                    if line.strip() and not line.strip().isdigit()
                )
                if text:
                    return text[:60000]
            except Exception as error:
                logger.warning('Не удалось загрузить формат субтитров %s: %s', sub.get('ext'), error)
                last_error = error
        if last_error is not None:
            raise last_error
        return ''
