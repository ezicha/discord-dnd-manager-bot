"""
Сторож правила «ядро не зависит от платформ».

Ни один файл в папке core/ не должен импортировать discord (а позже —
и библиотеки Telegram и т.п.). Если тест упал — значит, в ядро попал
платформенный код, и его нужно вынести в cogs/ (или в модуль своей платформы).
"""

import ast
from pathlib import Path

import pytest

CORE_DIR = Path(__file__).resolve().parent.parent / "core"
FORBIDDEN = {"discord", "aiogram", "telegram"}


def imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("path", sorted(CORE_DIR.rglob("*.py")), ids=lambda p: p.name)
def test_core_file_has_no_platform_imports(path):
    bad = imported_top_level_modules(path) & FORBIDDEN
    assert not bad, f"{path.name} импортирует платформенные библиотеки: {bad}"
