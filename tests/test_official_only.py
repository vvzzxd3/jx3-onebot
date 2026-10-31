"""Guard the repository's supported runtime against legacy entry-point regressions."""

from pathlib import Path
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]


class OfficialOnlyRepositoryTests(unittest.TestCase):
    def test_legacy_entry_points_and_configs_are_absent(self) -> None:
        for name in (
            "bot.py",
            "requirements.txt",
            ".env.dev",
            ".env.prod",
            "src/plugins",
        ):
            with self.subTest(path=name):
                self.assertFalse((ROOT / name).exists())

    def test_dependency_metadata_contains_no_legacy_adapter(self) -> None:
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertNotIn("nonebot", config.get("tool", {}))
        dependencies = config["project"]["dependencies"]
        self.assertFalse(any("nonebot" in item.lower() or "onebot" in item.lower() for item in dependencies))
        self.assertTrue((ROOT / "src/jx3_bot/__main__.py").is_file())


if __name__ == "__main__":
    unittest.main()
