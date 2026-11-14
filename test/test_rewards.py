"""奖励收集手势测试，鼠标使用 Mock。"""

from math import hypot
import unittest
from unittest.mock import Mock, call, patch

from game.input import InputUncertain
from flow.rewards import TRACE, collect
from service import Session
from game.slots import REWARD_CORNERS, REWARD_START


class RewardTests(unittest.TestCase):
    def setUp(self):
        self.controller = Mock()
        for name in ("post_touch_down", "post_touch_move", "post_touch_up"):
            getattr(self.controller, name).return_value.wait.return_value.succeeded = True
        self.capture = Mock(return_value={"screenshot": "after.png"})

    def test_holds_once_through_path_then_releases_and_captures(self):
        with patch("flow.rewards.sleep") as sleep:
            result = collect(self.controller, self.capture)
        self.assertEqual(self.controller.method_calls, [call.post_touch_down(*REWARD_START)] +
                         [call.post_touch_move(*point) for point in TRACE] + [call.post_touch_up()])
        self.assertEqual(sleep.call_count, len(TRACE))
        self.capture.assert_called_once_with()
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(result["screenshot"], "after.png")

    def test_path_reaches_all_turns_with_short_steps(self):
        self.assertTrue(all(point in TRACE for point in REWARD_CORNERS))
        self.assertTrue(all(hypot(x1 - x0, y1 - y0) <= 51
                            for (x0, y0), (x1, y1) in zip(TRACE, TRACE[1:])))

    def test_down_failure_releases_and_stops(self):
        self.controller.post_touch_down.return_value.wait.return_value.succeeded = False
        with self.assertRaisesRegex(InputUncertain, "按下失败"):
            collect(self.controller, self.capture)
        self.controller.post_touch_up.assert_called_once_with()
        self.controller.post_touch_move.assert_not_called()
        self.capture.assert_not_called()

    def test_move_failure_releases_and_stops(self):
        self.controller.post_touch_move.return_value.wait.return_value.succeeded = False
        with self.assertRaisesRegex(InputUncertain, "移动至"):
            collect(self.controller, self.capture)
        self.assertEqual(self.controller.post_touch_move.call_count, 1)
        self.controller.post_touch_up.assert_called_once_with()
        self.capture.assert_not_called()

    def test_move_exception_releases_and_keeps_error_context(self):
        self.controller.post_touch_move.side_effect = OSError("input error")
        with self.assertRaisesRegex(InputUncertain, "OSError: input error"):
            collect(self.controller, self.capture)
        self.controller.post_touch_up.assert_called_once_with()
        self.capture.assert_not_called()

    def test_release_failure_is_reported(self):
        self.controller.post_touch_up.return_value.wait.return_value.succeeded = False
        with patch("flow.rewards.sleep"), self.assertRaisesRegex(InputUncertain, "释放失败"):
            collect(self.controller, self.capture)
        self.capture.assert_not_called()

    def test_capture_failure_is_uncertain_after_release(self):
        self.capture.side_effect = ValueError("image error")
        with patch("flow.rewards.sleep"), self.assertRaisesRegex(InputUncertain, "ValueError: image error"):
            collect(self.controller, self.capture)
        self.controller.post_touch_up.assert_called_once_with()

    def test_other_page_returns_frame_without_input(self):
        session = Session(1, "framepool")
        session.controller = self.controller
        session.preparation_frame = Mock(return_value="frame")
        session.preparation = Mock()
        session.preparation.page.return_value = {"result": "uncertain", "screen": "未知"}
        session.save_image = Mock(return_value={"screenshot": "before.png"})
        result = session.collect_rewards()
        self.assertEqual(result["screenshot"], "before.png")
        self.assertEqual(result["result"], "uncertain")
        session.preparation.page.assert_called_once_with("frame")
        self.controller.post_touch_down.assert_not_called()

    def test_collect_confirms_page_without_matching_characters(self):
        session = Session(1, "framepool")
        session.controller = self.controller
        session.preparation_frame = Mock(return_value="frame")
        session.preparation = Mock()
        session.preparation.page.return_value = {"result": "success", "screen": "备战"}
        with patch("flow.rewards.collect", return_value={"result": "uncertain"}) as action:
            session.collect_rewards()
        session.preparation.page.assert_called_once_with("frame")
        session.preparation.recognize.assert_not_called()
        action.assert_called_once_with(self.controller, session.capture)


if __name__ == "__main__":
    unittest.main()
