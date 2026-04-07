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

    # For attribute access like obj.method(), extract the rightmost identifier
    if func_node.type in ("attribute", "member_expression", "field_access"):
        parts = func_node.text.decode("utf-8", errors="replace").split(".")
        return parts[-1] if parts else None

    return None


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


def _extract_base_name(arg):
    """Extract the class name from a base class AST node.

    Handles both bare identifiers (Bar) and qualified names (models.Bar)
    by returning the rightmost identifier component.
    """
    if arg.type in ("identifier", "name", "type_identifier"):
        return arg.text.decode("utf-8", errors="replace")
    if arg.type in ("attribute", "member_expression", "field_access", "scoped_type_identifier"):
        # Qualified name like models.Base — take the rightmost component
        parts = arg.text.decode("utf-8", errors="replace").split(".")
        return parts[-1] if parts else None
    return None


def _get_base_class(node, language):
    """Extract the base class name from a class definition node, if any."""
    if language == "python":
        for child in node.children:
            if child.type == "argument_list":
                for arg in child.children:
                    name = _extract_base_name(arg)
                    if name:
                        return name
                break
    elif language in ("javascript", "typescript"):
        for child in node.children:
            if child.type == "class_heritage":
                for arg in child.children:
                    name = _extract_base_name(arg)
                    if name:
                        return name
                break
    elif language == "java":
        for child in node.children:
            if child.type == "superclass":
                for arg in child.children:
                    name = _extract_base_name(arg)
                    if name:
                        return name
                break
    return None


def extract_definitions(filepath, content):
    """Extract top-level definitions (classes, functions, imports) from a source file.

    Returns a list of dicts with keys: id, file, type, line, and optionally extends (for classes).
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

        entry = {
            "id": qualified,
            "file": filepath,
            "type": node_type,
            "line": node.start_point[0] + 1,
        }

        # Extract base class for inheritance edges
        if node_type == "class":
            base = _get_base_class(node, language)
            if base:
                entry["extends"] = base

        definitions.append(entry)

    return definitions


def extract_imports(filepath, content):
    """Extract import relationships from a source file.

    Returns a list of dicts:
    [{"importer_file": str, "imported_name": str, "from_module": str, "line": int}]
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

    imports = []

    if language == "python":
        # Handle "from X import Y" and "from X import Y as Z"
        for node in _walk_for_types(tree.root_node, {"import_from_statement"}):
            module_text = ""
            dots = 0
            imported_names = []

            for child in node.children:
                if child.type == "relative_import":
                    for sub in child.children:
                        if sub.type == "import_prefix":
                            dots = sub.text.count(b".")
                        elif sub.type == "dotted_name":
                            module_text = sub.text.decode("utf-8", errors="replace")
                elif child.type == "dotted_name":
                    if not module_text and dots == 0:
                        module_text = child.text.decode("utf-8", errors="replace")
                    else:
                        imported_names.append(child.text.decode("utf-8", errors="replace"))
                elif child.type == "aliased_import":
                    # "from X import Y as Z" — extract the original name (Y)
                    for sub in child.children:
                        if sub.type == "dotted_name":
                            imported_names.append(sub.text.decode("utf-8", errors="replace"))
                            break

            for name in imported_names:
                from_module = ("." * dots + module_text) if dots else module_text
                imports.append({
                    "importer_file": filepath,
                    "imported_name": name,
                    "from_module": from_module,
                    "line": node.start_point[0] + 1,
                })

        # Handle plain "import X" and "import X.Y"
        for node in _walk_for_types(tree.root_node, {"import_statement"}):
            for child in node.children:
                if child.type == "dotted_name":
                    full_name = child.text.decode("utf-8", errors="replace")
                    # Use the last component as the imported name (e.g., "path" from "os.path")
                    name = full_name.rsplit(".", 1)[-1]
                    imports.append({
                        "importer_file": filepath,
                        "imported_name": name,
                        "from_module": full_name,
                        "line": node.start_point[0] + 1,
                    })
                elif child.type == "aliased_import":
                    for sub in child.children:
                        if sub.type == "dotted_name":
                            full_name = sub.text.decode("utf-8", errors="replace")
                            name = full_name.rsplit(".", 1)[-1]
                            imports.append({
                                "importer_file": filepath,
                                "imported_name": name,
                                "from_module": full_name,
                                "line": node.start_point[0] + 1,
                            })
                            break

    elif language in ("javascript", "typescript"):
        for node in _walk_for_types(tree.root_node, {"import_statement"}):
            source_str = ""
            imported_names = []

            for child in node.children:
                if child.type == "string":
                    # Extract the string content (without quotes)
                    for sub in child.children:
                        if sub.type == "string_fragment":
                            source_str = sub.text.decode("utf-8", errors="replace")
                elif child.type == "import_clause":
                    for sub in child.children:
                        if sub.type == "identifier":
                            imported_names.append(sub.text.decode("utf-8", errors="replace"))
                        elif sub.type == "named_imports":
                            for spec in sub.children:
                                if spec.type == "import_specifier":
                                    for ident in spec.children:
                                        if ident.type == "identifier":
                                            imported_names.append(ident.text.decode("utf-8", errors="replace"))
                                            break

            for name in imported_names:
                imports.append({
                    "importer_file": filepath,
                    "imported_name": name,
                    "from_module": source_str,
                    "line": node.start_point[0] + 1,
                })

    elif language == "java":
        for node in _walk_for_types(tree.root_node, {"import_declaration"}):
            for child in node.children:
                if child.type in ("scoped_identifier", "identifier"):
                    full_path = child.text.decode("utf-8", errors="replace")
                    # Last component is the imported class/name
                    parts = full_path.rsplit(".", 1)
                    name = parts[-1] if len(parts) > 1 else full_path
                    imports.append({
                        "importer_file": filepath,
                        "imported_name": name,
                        "from_module": full_path,
                        "line": node.start_point[0] + 1,
                    })

    return imports


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


def build_topology(all_definitions, all_calls, all_imports=None):
    """Build a NetworkX MultiDiGraph from extracted definitions, calls, and imports.

    Edges are typed: "calls", "imports", or "extends".
    MultiDiGraph allows multiple edge types between the same (u, v) pair
    (e.g., a caller that both imports and calls the same target).
    """
    if all_imports is None:
        all_imports = []

    G = nx.MultiDiGraph()

    # Index definitions by their short name for call-site/import matching
    name_to_ids = {}
    class_name_to_ids = {}
    for defn in all_definitions:
        G.add_node(defn["id"], file=defn["file"], type=defn["type"], line=defn["line"])
        # Extract local name after "::" then get the short name (last component)
        local_name = defn["id"].split("::")[-1] if "::" in defn["id"] else defn["id"]
        short_name = local_name.split(".")[-1]
        name_to_ids.setdefault(short_name, []).append(defn["id"])
        if defn["type"] == "class":
            class_name_to_ids.setdefault(short_name, []).append(defn["id"])

    # Best-effort call edge resolution
    for call in all_calls:
        callee_name = call["callee_name"]
        targets = name_to_ids.get(callee_name, [])
        if len(targets) == 1:
            caller_file = call["caller_file"]
            caller_candidates = [
                d for d in all_definitions
                if d["file"] == caller_file
                and d["type"] in ("def", "class")
                and d["line"] <= call["line"]
            ]
            caller_id = max(caller_candidates, key=lambda d: d["line"])["id"] if caller_candidates else None
            if caller_id and caller_id != targets[0]:
                G.add_edge(caller_id, targets[0], type="calls")

    # Import edge resolution: match imported names to known definitions
    for imp in all_imports:
        imported_name = imp["imported_name"]
        targets = name_to_ids.get(imported_name, [])
        if len(targets) == 1:
            # Anchor import edge to the earliest node in the importing file (any type)
            importer_defs = [d for d in all_definitions if d["file"] == imp["importer_file"]]
            if importer_defs:
                importer_id = min(importer_defs, key=lambda d: d["line"])["id"]
                if importer_id != targets[0]:
                    G.add_edge(importer_id, targets[0], type="imports")

    # Inheritance edge resolution: match extends fields to class definitions
    for defn in all_definitions:
        base_name = defn.get("extends")
        if not base_name:
            continue
        targets = class_name_to_ids.get(base_name, [])
        if len(targets) == 1 and defn["id"] != targets[0]:
            G.add_edge(defn["id"], targets[0], type="extends")

    return G
