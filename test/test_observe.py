"""统一观察分流，同帧只读且不启动战斗监测。"""
import unittest
from unittest.mock import Mock, patch

from flow.observe import observe


class ObserveTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.image = object()
        self.session.preparation_frame.return_value = self.image
        self.session.save_image.return_value = {'screenshot': 'same.png'}
        self.preparation = self.session.preparation_reader.return_value
        self.preparation.score.return_value = 0
        self.preparation.action_button.return_value = {'next_action': None}
        self.session.protagonist_reader.return_value.selection_page.return_value = False
        for method in ('supply_reader', 'encounter_reader', 'strategy_reader'):
            getattr(self.session, method).return_value.page.return_value = False
        self.layout = patch('flow.preparation_scene.layout', return_value=None).start()
        self.addCleanup(patch.stopall)

    def assert_read_only(self):
        self.session.preparation_frame.assert_called_once_with()
        self.session.save_image.assert_called_once_with(self.image)
        self.session.controller.post_click.assert_not_called()
        self.session.watch_battle.assert_not_called()
        self.session.battle_reader.return_value.begin.assert_not_called()

    def test_closed_preparation_returns_same_frame(self):
        self.layout.return_value = {'result': 'success', 'screen': '备战', 'shop': 'closed'}
        self.preparation.observe.return_value = {'slots': {'front_1': None}, 'gold': 5}
        result = observe(self.session)
        self.assertEqual(result['gold'], 5)
        self.assertEqual(result['screenshot'], 'same.png')
        self.preparation.observe.assert_called_once_with(self.image)
        self.assertNotIn('shop_slots', result)
        self.assert_read_only()

    def test_open_shop_reads_five_names_on_the_returned_frame(self):
        self.layout.return_value = {'result': 'success', 'screen': '备战', 'shop': 'open'}
        self.preparation.text.side_effect = ['白露', '流萤', '银狼 LV.999', '', '罗刹']
        info = {'refresh_mode': 'paid', 'refresh_cost': 2, 'free_refreshes': 0, 'gold': 9}
        with patch('flow.observe.read_refresh', return_value=info) as read:
            result = observe(self.session)
        self.assertEqual(result['shop'], 'open')
        self.assertEqual(result['gold'], 9)
        self.assertEqual(result['shop_slots'], dict(zip(
            ('shop_1', 'shop_2', 'shop_3', 'shop_4', 'shop_5'),
            ('白露', '流萤', '银狼LV.999', None, '罗刹'))))
        self.assertTrue(all(call.args[0] is self.image for call in self.preparation.text.call_args_list))
        read.assert_called_once_with(self.image, self.preparation)
        self.preparation.observe.assert_not_called()
        self.assert_read_only()

    def test_screens_dispatch_and_preserve_partial_results(self):
        cases = [('supply_reader', {'screen': '补给', 'selection': {'current': 0, 'limit': 1},
                                     'options': [{'unknown': True}]}),
                 ('encounter_reader', {'screen': '遭遇', 'options': [{'unknown': True}]}),
                 ('strategy_reader', {'screen': '投资策略', 'options': [{'unknown': True}]})]
        for name, observation in cases:
            with self.subTest(name=name):
                self.session.reset_mock()
                reader = getattr(self.session, name).return_value
                reader.page.return_value = True
                reader.read.return_value = observation.copy()
                result = observe(self.session)
                self.assertEqual(result['screen'], observation['screen'])
                self.assertEqual(result['result'], 'uncertain')
                reader.read.assert_called_once_with(self.image)
                self.assert_read_only()
                reader.page.return_value = False

    def test_protagonist_entry_does_not_open_it(self):
        protagonist = self.session.protagonist_reader.return_value
        self.layout.return_value = {'result': 'success', 'screen': '备战', 'shop': 'closed', 'node': '1-9'}
        self.preparation.action_button.return_value = {'next_action': 'protagonist-open'}
        result = observe(self.session)
        self.assertEqual((result['stage'], result['node'], result['next_action']),
                         ('entry', '1-9', 'protagonist-open'))
        protagonist.selection_page.assert_called_once_with(self.image)
        self.preparation.observe.assert_not_called()
        self.assert_read_only()

    def test_battle_recognition_never_starts_monitor(self):
        self.session.battle_reader.return_value.recognize.return_value = {'result': 'success', 'screen': '战斗'}
        result = observe(self.session)
        self.assertEqual(result['screen'], '战斗')
        self.session.battle_reader.return_value.recognize.assert_called_once_with(self.image)
        self.assert_read_only()

    def test_unknown_keeps_frame_and_needs_ai(self):
        self.session.battle_reader.return_value.recognize.return_value = {'result': 'uncertain', 'screen': '未知'}
        result = observe(self.session)
        self.assertTrue(result['needs_ai'])
        self.assertEqual(result['screenshot'], 'same.png')
        self.assert_read_only()
