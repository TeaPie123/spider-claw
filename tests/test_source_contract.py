"""
作用：确保项目源文件只在文件开头保留作用和流程说明。
流程：解析所有自有 Python 文件 → 检查模块头说明 → 拒绝函数文档字符串和正文注释。
"""

import ast
import io
import tokenize
from pathlib import Path


def test_source_files_have_only_module_header_comments():
    root = Path(__file__).resolve().parents[1]
    for path in root.rglob("*.py"):
        if any(part in {".venv", "__pycache__"} for part in path.relative_to(root).parts):
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        header = ast.get_docstring(tree)
        assert header and "作用：" in header and "流程：" in header, str(path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                assert ast.get_docstring(node) is None, f"{path}:{node.lineno}"
        comments = [
            token for token in tokenize.generate_tokens(io.StringIO(source).readline) if token.type == tokenize.COMMENT
        ]
        assert not comments, str(path)
