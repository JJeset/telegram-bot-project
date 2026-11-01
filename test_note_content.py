import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from note_content import parse_note_response
import test_external_api

bot = test_external_api.bot
RECIPE = {
    'kind': 'recipe',
    'title': 'Пельмени в сметанно-грибном соусе',
    'products': ['Пельмени — 400 г', 'Большой лук', 'Шампиньоны', 'Сметана — на глаз'],
    'steps': ['Нарезать лук и грибы.', 'Обжарить пельмени, добавить сметану.'],
}


class NoteContentTests(unittest.TestCase):
    @patch.object(bot, 'analyze_with_external_api')
    def test_malformed_reply_is_retried_without_saving_partial_recipe(self, analyze):
        analyze.side_effect = ['{"kind":"recipe","title":"', json.dumps(RECIPE)]
        note = bot.AIAnalyzer.analyze_video({})
        self.assertEqual(note['title'], RECIPE['title'])
        self.assertEqual([call.kwargs['max_tokens'] for call in analyze.call_args_list], [4096, 8192])

    @patch.object(bot, 'analyze_with_external_api')
    def test_generation_length_limit_is_retried(self, analyze):
        analyze.side_effect = [bot.IncompleteAnalysisError('length'), json.dumps(RECIPE)]
        self.assertEqual(bot.AIAnalyzer.analyze_video({})['title'], RECIPE['title'])

    @patch.object(bot, 'analyze_with_external_api', return_value='{"title":"')
    def test_retries_are_bounded(self, analyze):
        with self.assertRaises(bot.AnalysisError):
            bot.AIAnalyzer.analyze_video({})
        self.assertEqual(analyze.call_count, 2)

    def test_recipe_preserves_quantities_and_builds_only_required_sections(self):
        note = parse_note_response(json.dumps(RECIPE, ensure_ascii=False))
        self.assertEqual(note['title'], RECIPE['title'])
        self.assertIn('Пельмени — 400 г', note['content'])
        self.assertIn('Большой лук', note['content'])
        self.assertIn('Сметана — на глаз', note['content'])
        self.assertIn('• Шампиньоны\n', note['content'])
        self.assertEqual(note['content'].count('Продукты:'), 1)
        self.assertEqual(note['content'].count('Приготовление:'), 1)
        self.assertNotIn(RECIPE['title'], note['content'])
        self.assertNotIn('Рецепт:', note['content'])

    def test_non_recipe_does_not_get_ingredient_sections(self):
        note = parse_note_response(json.dumps({'kind': 'summary', 'title': 'Уход за деревом', 'content': 'Шлифовать поверхность.'}))
        self.assertEqual(note['content'], 'Шлифовать поверхность.')

    def test_invalid_structure_is_rejected(self):
        for response in ('{}', 'Not JSON', json.dumps({**RECIPE, 'products': 'Лук'})):
            with self.subTest(response=response), self.assertRaises(ValueError):
                parse_note_response(response)


class NoteFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_ai_error_does_not_blame_video_link_or_offer_to_save(self):
        processing = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
        message = SimpleNamespace(text='https://example.com/video', reply_text=AsyncMock(return_value=processing))
        update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=123))
        context = SimpleNamespace(user_data={})
        with patch.object(bot.video_processor, 'extract_video_info', return_value={'duration': 77}), \
             patch.object(bot.ai_analyzer, 'analyze_video', side_effect=bot.AnalysisError('Ошибка AI')):
            await bot.process_video_link(update, context)
        self.assertEqual(processing.edit_text.await_args.args[0], '❌ Ошибка AI')
        self.assertNotIn('pending_note', context.user_data)
        self.assertEqual(message.reply_text.await_count, 1)

    async def test_generated_dish_title_is_used_in_message_and_saved_note(self):
        processing = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
        message = SimpleNamespace(text='https://example.com/video', reply_text=AsyncMock(return_value=processing))
        update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=123))
        context = SimpleNamespace(user_data={})
        video = {'title': 'Делитесь своими рецептами!', 'duration': 77, 'description': 'Реклама', 'subtitles': 'Текст'}
        with patch.object(bot.video_processor, 'extract_video_info', return_value=video), \
             patch.object(bot, 'analyze_with_external_api', return_value=json.dumps(RECIPE)), \
             patch.object(bot, 'generate_tags_from_content', return_value='#пельмени') as tags, \
             patch.object(bot, 'build_folder_picker_keyboard', return_value=None):
            await bot.process_video_link(update, context)
        note = context.user_data['pending_note']
        self.assertEqual(note['title'], RECIPE['title'])
        self.assertIn('Продукты:', note['content'])
        self.assertEqual(tags.call_args.args[0], RECIPE['title'])
        result = message.reply_text.await_args_list[1].args[0]
        self.assertTrue(result.startswith(RECIPE['title'] + '\n\nПродукты:'))
        self.assertNotIn(video['title'], result)
        self.assertNotIn('**', result)


if __name__ == '__main__':
    unittest.main()
