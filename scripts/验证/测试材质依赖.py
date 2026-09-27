"""验证依赖链分析不会把未知或有效分支错误判定为可忽略。"""

import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("dependency_analysis", Path(__file__).resolve().parents[1] / "诊断" / "分析材质依赖.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DependencyTests(unittest.TestCase):
    def row(self, props):
        return {"properties": props}

    def test_refs_include_array_slots(self):
        self.assertEqual(list(MODULE.iter_refs({"materialList": [None, {"ref": 12}]})), [("12", "materialList[1]")])

    def test_false_switch_disables(self):
        controls, disabled = MODULE.edge_controls(self.row({"texmap_bump_on": False}), "texmap_bump")
        self.assertTrue(disabled)
        self.assertFalse(controls["texmap_bump_on"])

    def test_zero_multiplier_disables(self):
        self.assertTrue(MODULE.edge_controls(self.row({"texmap_bump_multiplier": 0}), "texmap_bump")[1])

    def test_enabled_mask_not_disabled_by_mix_amount_zero(self):
        self.assertFalse(MODULE.edge_controls(self.row({"maskEnabled": True, "mixAmount": 0}), "Mask")[1])

    def test_unknown_controls_remain_potentially_active(self):
        self.assertEqual(MODULE.edge_controls(self.row({}), "coatMtl[0]"), ({}, False))

    def test_disabled_edge_still_records_owner(self):
        graph = {"1": self.row({}), "2": self.row({"texmap_bump": {"ref": 1}, "texmap_bump_on": False})}
        result = MODULE.trace("1", MODULE.build_reverse(graph), {"2": [{"handle": 10}]})
        self.assertEqual(result["bound_owner_count"], 1)
        self.assertEqual(result["enabled_or_unknown_owner_count"], 0)

    def test_active_alternative_prevents_false_dismissal(self):
        graph = {"1": self.row({}), "2": self.row({"texmap_bump": {"ref": 1}, "texmap_bump_on": False,
                                                  "texmap_diffuse": {"ref": 1}, "texmap_diffuse_on": True})}
        result = MODULE.trace("1", MODULE.build_reverse(graph), {"2": [{"handle": 10}]})
        self.assertEqual(result["enabled_or_unknown_owner_count"], 1)

    def test_cycles_terminate(self):
        graph = {"1": self.row({"next": {"ref": 2}}), "2": self.row({"next": {"ref": 1}})}
        result = MODULE.trace("1", MODULE.build_reverse(graph), {"2": [{"handle": 10}]})
        self.assertEqual(result["bound_owner_count"], 1)

    def test_unbound_is_not_declared_unused(self):
        result = MODULE.trace("1", {}, {})
        self.assertEqual(result["classification"], "no_object_route_found")


if __name__ == "__main__":
    unittest.main(verbosity=2)
