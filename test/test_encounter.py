"""遭遇识别、奖励读取、单次选择及刷新。"""
from itertools import count
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch
from types import SimpleNamespace

import numpy as np
from PIL import Image

from flow.prepare import PreparationFlow
from ocr import ImageController
from screens.encounter import Encounter, EncounterOperation, complete
from screens.preparation import Preparation
from service import Session


def fixture(name):
    return np.asarray(Image.open(Path(__file__).parent / 'fixtures' / 'encounter' / f'{name}.png').convert('RGB'))[:, :, ::-1].copy()


def observation():
    return {'screen': '遭遇', 'title': '遭遇节点', 'selected': None, 'refreshes_left': 1, 'needs_ai': True,
            'options': [{'slot': f'option_{i}', 'title': f'遭遇其{i}', 'difficulty': i,
                         'reward': {'text': '金币', 'count': 2}} for i in (1, 2)]}


class ReaderTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.preparation = Preparation()
        cls.reader = Encounter(cls.preparation)

    def test_sample_reads_both_difficulties_and_rewards(self):
        image = fixture('before')
        self.assertTrue(self.reader.page(image))
        result = self.reader.read(image)
        self.assertEqual([(o['difficulty'], o['reward']) for o in result['options']],
                         [(1, {'text': '金币', 'count': 2}), (3, {'text': '随机4费角色', 'count': 2})])
        self.assertEqual(result['refreshes_left'], 1)
        self.assertIsNone(result['selected'])
        self.assertTrue(complete(result))

    def test_selected_and_title_obscured(self):
        image = fixture('selected')
        self.assertEqual(self.reader.selected(image), 2)
        image[65:135, 880:1050] = 0
        self.assertFalse(self.reader.page(image))

    def test_missing_reward_preserves_partial_data(self):
        reader = Encounter.__new__(Encounter)
        reader.text = Mock(side_effect=['遭遇其一', '', '2', '遭遇其三', '角色', '', '剩余次数：1'])
        reader.selected = Mock(return_value=None)
        result = reader.read('frame')
        self.assertFalse(complete(result))
        self.assertEqual(result['options'][0]['difficulty'], 1)
        self.assertIsNone(result['options'][1]['reward']['count'])


class OperationTests(TestCase):
    def setUp(self):
        self.session = Mock()
        self.reader = Mock()
        self.reader.page.return_value = True
        self.reader.read.return_value = observation()
        job = self.session.controller.post_screencap.return_value.wait.return_value
        job.succeeded = True
        job.get.return_value = 'frame'
        self.session.controller.post_click.return_value.wait.return_value.succeeded = True
        self.op = EncounterOperation(self.session, self.reader)
        self.sleep = patch('screens.encounter.sleep').start()
        patch('screens.encounter.monotonic', side_effect=count()).start()
        self.addCleanup(patch.stopall)

    def test_refresh_count_decrements_and_returns_new_frame(self):
        self.reader.refreshes.return_value = 0
        after = observation(); after['refreshes_left'] = 0
        self.reader.read.side_effect = [observation(), after]
        result = self.op.execute('refresh')
        self.assertEqual(result['refreshes_left'], 0)
        self.session.controller.post_click.assert_called_once_with(672, 900)

    def test_refresh_unknown_or_zero_never_clicks(self):
        for value in (None, 0):
            before = observation(); before['refreshes_left'] = value
            self.reader.read.return_value = before
            self.assertIn(self.op.execute('refresh')['result'], ('failed', 'uncertain'))
        self.session.controller.post_click.assert_not_called()

    def test_refresh_timeout_does_not_click_twice(self):
        self.reader.refreshes.return_value = 1
        self.assertEqual(self.op.execute('refresh')['result'], 'uncertain')
        self.session.controller.post_click.assert_called_once_with(672, 900)

    def test_existing_selection_stops_before_pipeline(self):
        before = observation(); before['selected'] = 2
        self.reader.read.return_value = before
        with patch('screens.encounter.Tasker') as tasker:
            self.assertEqual(self.op.execute('choose', 1)['result'], 'uncertain')
        tasker.assert_not_called()

    def test_failed_pipeline_keeps_details_and_does_not_retry(self):
        with patch('screens.encounter.Tasker') as tasker, patch('screens.encounter.Resource'):
            instance = tasker.return_value
            instance.inited = True
            job = instance.post_task.return_value.wait.return_value
            job.succeeded = False
            job.get.return_value = SimpleNamespace(nodes=[SimpleNamespace(name='failed-node', completed=False)])
            result = self.op.execute('choose', 2)
        self.assertEqual(result['result'], 'uncertain')
        self.assertIn('failed-node', result['message'])
        instance.post_task.assert_called_once()

    def test_encounter_stops_blank_clicks_and_collection(self):
        session = Mock()
        session.protagonist_reader.return_value.selection_page.return_value = False
        session.supply_reader.return_value.page.return_value = False
        session.preparation_reader.return_value.score.return_value = 0
        encounter = session.encounter_reader.return_value
        encounter.page.return_value = True
        encounter.read.return_value = observation()
        session.controller.post_screencap.return_value.wait.return_value.get.return_value = 'frame'
        session.save_image.return_value = {'screenshot': 'frame.png'}
        with patch('flow.prepare.sleep'), patch('flow.prepare.sweep') as sweep:
            result = PreparationFlow(session).enter(delay=8)
        self.assertEqual(result['screen'], '遭遇')
        session.controller.post_click.assert_not_called()
        sweep.assert_not_called()


class PipelineTests(TestCase):
    def test_saved_frame_via_session_observation(self):
        controller = ImageController(fixture('before'))
        controller.set_screenshot_use_raw_size(True)
        self.assertTrue(controller.post_connection().wait().succeeded)
        session = Session(1, 'framepool')
        session.controller = controller
        session.save_image = Mock(return_value={'screenshot': 'saved.png'})
        result = session.run('encounter', session.encounter_action)
        self.assertEqual(result['screen'], '遭遇')
        self.assertEqual(result['options'][1]['reward'], {'text': '随机4费角色', 'count': 2})
        self.assertEqual(result['screenshot'], 'saved.png')

    def test_pipeline_clicks_selected_card_then_confirms(self):
        class Controller(ImageController):
            def __init__(self):
                self.frames = [fixture('before'), fixture('selected'), np.zeros((1080, 1920, 3), dtype='uint8')]
                self.clicks = []
                super().__init__(self.frames[0])
            def click(self, x, y):
                self.clicks.append((x, y))
                self.image = self.frames[min(len(self.clicks), 2)]
                return True
            def touch_down(self, contact, x, y, pressure):
                return self.click(x, y)
            def touch_up(self, contact):
                return True
        controller = Controller()
        self.assertTrue(controller.set_screenshot_use_raw_size(True))
        self.assertTrue(controller.post_connection().wait().succeeded)
        session = Mock()
        session.controller = controller
        session.preparation_scene.return_value = {'result': 'success', 'screen': '备战', 'shop': 'open'}
        reader = Encounter(Preparation())
        op = EncounterOperation(session, reader)
        result = op.execute('choose', 2)
        self.assertEqual(result['screen'], '备战', (result, controller.clicks))
        self.assertEqual(controller.clicks, [(1253, 480), (1082, 900)])

    def test_choice_resumes_existing_flow_with_same_frame(self):
        session = Session(1, 'framepool')
        session.encounter = Mock()
        session.preparation_flow = Mock()
        session.preparation_flow.resume.return_value = {'result': 'success', 'screen': '备战'}
        with patch('screens.encounter.EncounterOperation') as operation:
            operation.return_value.execute.return_value = {'result': 'success', 'screen': '备战'}
            operation.return_value.image = 'after'
            result = session.encounter_action('choose', 2)
        session.preparation_flow.resume.assert_called_once_with('after')
        self.assertEqual(result['option'], 2)
