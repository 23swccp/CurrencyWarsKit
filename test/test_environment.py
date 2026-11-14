import unittest
from unittest.mock import Mock

import numpy as np

from screens.environment import recognize


class EnvironmentTests(unittest.TestCase):
    def test_text_and_equipment_stay_with_their_option(self):
        image = np.zeros((1080, 1920, 3), dtype="uint8")
        def lines(*texts):
            return [{"text": text, "box": [0, 380 + i * 220, 100, 20]} for i, text in enumerate(texts)]
        read = Mock(side_effect=[lines("经济过热"), lines("奖励节点替换"),
                                lines("火药味", "装备"), lines("获得2个简易武装箱"),
                                lines("追击概念股", "角色", "装备"), lines("获得追击角色")])
        equipment = Mock()
        equipment.match.side_effect = [{"unknown": True}, {"id": "roller_skates"}]
        result = recognize(image, read, equipment)
        self.assertNotIn("equipment", result["options"][0])
        self.assertTrue(result["options"][1]["equipment"][0]["unknown"])
        self.assertEqual(result["options"][2]["equipment"][0]["id"], "roller_skates")
        self.assertTrue(result["needs_ai"])

    def test_missing_card_text_is_unknown(self):
        result = recognize(None, Mock(return_value=[]), Mock())
        self.assertTrue(all(option["unknown"] for option in result["options"]))
