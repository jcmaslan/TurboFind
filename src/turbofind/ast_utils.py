import os
import tree_sitter

try:
    import tree_sitter_python
except ImportError:
    tree_sitter_python = None

try:
    import tree_sitter_java
except ImportError:
    tree_sitter_java = None

try:
    import tree_sitter_javascript
except ImportError:
    tree_sitter_javascript = None

try:
    import tree_sitter_typescript
except ImportError:
    tree_sitter_typescript = None

EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}

_LANGUAGE_MODULES = {
    "python": tree_sitter_python,
    "java": tree_sitter_java,
    "javascript": tree_sitter_javascript,
    "typescript": tree_sitter_typescript,
}

def get_parser(language: str):
    module = _LANGUAGE_MODULES.get(language)
    if not module:
        return None
    parser = tree_sitter.Parser()
    if language == "typescript":
        parser.language = tree_sitter.Language(module.language_typescript())
    else:
        parser.language = tree_sitter.Language(module.language())
    return parser

def simplify_ast(node, source_bytes, max_depth, current_depth=0):
    if current_depth > max_depth:
        return None

    result = {
        "type": node.type,
        "start": node.start_point,
        "end": node.end_point,
    }

    if len(node.children) == 0:
        result["text"] = node.text.decode('utf-8', errors='replace')

    children = []
    for child in node.children:
        child_res = simplify_ast(child, source_bytes, max_depth, current_depth + 1)
        if child_res:
            children.append(child_res)

    if children:
        result["children"] = children

    return result

def extract_ast(filepath: str, content: str, max_depth: int = 5):
    """
    Extracts a simplified AST from a file's content up to max_depth.
    Returns a dictionary structure, or empty dict if the language is unsupported.
    """
    ext = os.path.splitext(filepath)[1]
    language = EXTENSION_TO_LANGUAGE.get(ext)
    if not language:
        return {}

    parser = get_parser(language)
    if not parser:
        return {}

    source_bytes = content.encode('utf-8')
    tree = parser.parse(source_bytes)

    return simplify_ast(tree.root_node, source_bytes, max_depth)
