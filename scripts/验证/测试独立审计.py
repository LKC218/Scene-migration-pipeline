"""不启动 Max，验证独立审计的源身份与迁移阻塞规则。"""

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "诊断" / "运行独立审计.py"
SPEC = importlib.util.spec_from_file_location("scene_audit_runner", MODULE_PATH)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class AuditGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scene = self.root / "目标.max"
        self.report = {
            "status": "ok", "scene": {"path": str(self.root), "name": self.scene.name,
                                       "renderer_class": "Default_Scanline_Renderer"},
            "materials": [], "texture_graph": {"textures": [], "errors": []},
            "nodes": {"missing_class_nodes": []}, "bitmap_textures": [], "summary": {},
        }

    def assess(self):
        return RUNNER.assess(self.report, self.scene, self.root)

    def test_clean_audit_is_not_conversion_success(self):
        result = self.assess()
        self.assertEqual(result["status"], "needs_material_mapping_review")
        self.assertFalse(result["conversion_performed"])

    def test_wrong_scene_blocks(self):
        self.report["scene"]["name"] = "另一工程.max"
        self.assertEqual(self.assess()["status"], "blocked")

    def test_missing_material_blocks(self):
        self.report["materials"] = [{"name": "材质", "class": "Missing_Mtl"}]
        self.assertEqual(len(self.assess()["missing_materials"]), 1)
        self.assertEqual(self.assess()["status"], "blocked")

    def test_missing_map_blocks(self):
        self.report["texture_graph"]["textures"] = [{"class": "Missing_TextureMap"}]
        self.assertEqual(self.assess()["status"], "blocked")

    def test_empty_bitmap_is_not_resolved(self):
        self.report["bitmap_textures"] = [{"filename": "", "exists": False}]
        self.assertEqual(self.assess()["resources"][0]["resolution"], "缺失")

    def test_unique_candidate_requires_relink(self):
        (self.root / "贴图.png").touch()
        self.report["bitmap_textures"] = [{"filename": "贴图.png", "exists": False}]
        self.assertEqual(self.assess()["resources"][0]["resolution"], "唯一候选待重链接")
        self.assertFalse(self.assess()["resources"][0]["exists"])

    def test_duplicate_candidates_block(self):
        for directory in ("甲", "乙"):
            (self.root / directory).mkdir()
            (self.root / directory / "贴图.png").touch()
        self.report["bitmap_textures"] = [{"filename": "贴图.png", "exists": False}]
        self.assertEqual(self.assess()["resources"][0]["resolution"], "歧义")
        self.assertEqual(self.assess()["status"], "blocked")

    def test_asset_manager_missing_resource_blocks(self):
        self.report["summary"]["missing_asset_count"] = 1
        self.assertEqual(self.assess()["status"], "blocked")

    def test_read_error_blocks(self):
        self.report["texture_graph"]["errors"] = [{"error": "读取失败"}]
        self.assertEqual(self.assess()["status"], "blocked")

    def test_partial_material_parameters_block(self):
        self.report["material_detail_status"] = "partial"
        self.assertEqual(self.assess()["status"], "blocked")


if __name__ == "__main__":
    unittest.main(verbosity=2)
