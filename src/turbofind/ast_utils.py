import os
import tree_sitter
import networkx as nx

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

# Tree-sitter node types for extracting definitions
_DEFINITION_TYPES = {
    "python": {"function_definition", "class_definition", "import_from_statement"},
    "java": {"method_declaration", "class_declaration", "import_declaration"},
    "javascript": {"function_declaration", "class_declaration", "import_statement", "export_statement"},
    "typescript": {"function_declaration", "class_declaration", "import_statement", "export_statement"},
}

# Tree-sitter node types for extracting call sites
_CALL_TYPES = {
    "python": {"call"},
    "java": {"method_invocation"},
    "javascript": {"call_expression"},
    "typescript": {"call_expression"},
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


def _get_node_name(node, source_bytes):
    """Extract the name identifier from a definition or call node."""
    for child in node.children:
        if child.type in ("identifier", "name", "property_identifier"):
            return child.text.decode("utf-8", errors="replace")
    return None


def _get_call_name(node, source_bytes):
    """Extract the called function/method name from a call node."""
    # Handle attribute access: obj.method()
    func_node = node.children[0] if node.children else None
    if not func_node:
        return None

    if func_node.type in ("identifier", "name"):
        return func_node.text.decode("utf-8", errors="replace")

    # For attribute access like obj.method(), extract "method"
    if func_node.type in ("attribute", "member_expression", "field_access"):
        for child in func_node.children:
            if child.type in ("identifier", "name", "property_identifier"):
                # Return the last identifier (the method name)
                pass
        # Get the rightmost identifier
        parts = func_node.text.decode("utf-8", errors="replace").split(".")
        return parts[-1] if parts else None

    return func_node.text.decode("utf-8", errors="replace") if func_node.type == "identifier" else None


def _walk_for_types(node, target_types):
    """Walk the AST and yield nodes matching any of the target types."""
    if node.type in target_types:
        yield node
    for child in node.children:
        yield from _walk_for_types(child, target_types)


def _get_enclosing_class(node):
    """Walk up to find the enclosing class name, if any."""
    current = node.parent
    while current:
        if current.type in ("class_definition", "class_declaration"):
            for child in current.children:
                if child.type in ("identifier", "name"):
                    return child.text.decode("utf-8", errors="replace")
        current = current.parent
    return None


def extract_definitions(filepath, content):
    """Extract top-level definitions (classes, functions, imports) from a source file.

    Returns a list of dicts: [{"id": "Class.method", "file": path, "type": "def"|"class"|"import", "line": n}]
    """
    ext = os.path.splitext(filepath)[1]
    language = EXTENSION_TO_LANGUAGE.get(ext)
    if not language:
        return []

    parser = get_parser(language)
    if not parser:
        return []

    source_bytes = content.encode("utf-8")
    tree = parser.parse(source_bytes)

    def_types = _DEFINITION_TYPES.get(language, set())
    definitions = []

    for node in _walk_for_types(tree.root_node, def_types):
        name = _get_node_name(node, source_bytes)
        if not name:
            continue

        # Determine type
        if "class" in node.type:
            node_type = "class"
        elif "import" in node.type:
            node_type = "import"
        else:
            node_type = "def"

        # Build globally unique ID: file::Class.method or file::function
        enclosing = _get_enclosing_class(node)
        local_name = f"{enclosing}.{name}" if enclosing and node_type == "def" else name
        qualified = f"{filepath}::{local_name}"

        definitions.append({
            "id": qualified,
            "file": filepath,
            "type": node_type,
            "line": node.start_point[0] + 1,
        })

    return definitions


def extract_calls(filepath, content):
    """Extract call sites from a source file.

    Returns a list of dicts: [{"caller_file": path, "callee_name": "func", "line": n}]
    """
    ext = os.path.splitext(filepath)[1]
    language = EXTENSION_TO_LANGUAGE.get(ext)
    if not language:
        return []

    parser = get_parser(language)
    if not parser:
        return []

    source_bytes = content.encode("utf-8")
    tree = parser.parse(source_bytes)

    call_types = _CALL_TYPES.get(language, set())
    calls = []

    for node in _walk_for_types(tree.root_node, call_types):
        name = _get_call_name(node, source_bytes)
        if not name:
            continue

        calls.append({
            "caller_file": filepath,
            "callee_name": name,
            "line": node.start_point[0] + 1,
        })

    return calls


def build_topology(all_definitions, all_calls):
    """Build a NetworkX DiGraph from extracted definitions and calls.

    Edges are added on a best-effort basis by matching call-site names
    to known definition IDs.
    """
    G = nx.DiGraph()

    # Index definitions by their short name for call-site matching
    name_to_ids = {}
    for defn in all_definitions:
        G.add_node(defn["id"], file=defn["file"], type=defn["type"], line=defn["line"])
        # Extract local name after "::" then get the short name (last component)
        local_name = defn["id"].split("::")[-1] if "::" in defn["id"] else defn["id"]
        short_name = local_name.split(".")[-1]
        name_to_ids.setdefault(short_name, []).append(defn["id"])

    # Best-effort edge resolution
    for call in all_calls:
        callee_name = call["callee_name"]
        targets = name_to_ids.get(callee_name, [])
        if len(targets) == 1:
            # Unambiguous match — find the caller definition
            caller_file = call["caller_file"]
            caller_defs = [d["id"] for d in all_definitions if d["file"] == caller_file and d["type"] == "def"]
            # Find the most likely enclosing definition (closest line before the call)
            caller_id = None
            for d in all_definitions:
                if d["file"] == caller_file and d["type"] in ("def", "class") and d["line"] <= call["line"]:
                    caller_id = d["id"]
            if caller_id and caller_id != targets[0]:
                G.add_edge(caller_id, targets[0])

    return G
