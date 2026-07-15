import ast
import os
import re
import sys

# Class to file mapping for class diagrams
CLASS_TO_FILE = {
    "ChatSession": "backend/models.py",
    "ChatMessage": "backend/models.py",
    "LLMAuditLog": "backend/models.py",
    "AppManager": "backend/app_manager.py",
    "AppRecordStore": "backend/app_records.py",
    "ContextManager": "backend/context_manager.py",
    "AgentParser": "backend/agent_parser.py",
    "LLMService": "backend/llm_service.py",
    "AgentOrchestrator": "backend/agent/harness.py",
    "IntentRouter": "backend/agent/router.py",
    "BaseLLMProvider": "backend/agent/providers.py",
    "OllamaProvider": "backend/agent/providers.py",
    "CloudLLMProvider": "backend/agent/providers.py",
    "ToolRegistry": "backend/agent/tools.py",
    "PromptManager": "backend/agent/prompts/manager.py",
    "WorkspaceStorage": "backend/workspace_storage.py",
    "IntentPlan": "backend/agent/intent_plan.py",
    "IntentKind": "backend/agent/intent_plan.py",
    "RouterContext": "backend/router_context.py",
    "GraphSnapshot": "backend/router_context.py",
    "MutationTicketManager": "backend/mutation_tickets.py",
    "PlanExecutor": "backend/agent/plan_executor.py",
    "CodingPlanExecutor": "backend/agent/plan_executor.py",
    "MutationPlanExecutor": "backend/agent/plan_executor.py",
    "PlanPhaseResult": "backend/agent/plan_executor.py",
    # Direction A
    "SubIntent": "backend/agent/intent_plan.py",
    "SubIntentKind": "backend/agent/intent_plan.py",
    "VerificationDiff": "backend/schema_diff.py",
    "SchemaVerificationService": "backend/schema_verification.py",
    # Direction B
    "WidgetDAG": "backend/agent/dag.py",
    "TaskNode": "backend/agent/dag.py",
    "TaskResult": "backend/agent/dag.py",
    "BackendManager": "backend/backend_manager.py",
    "StdioJsonRpcClient": "backend/backend_manager.py",
}


def parse_uml_classes(uml_path: str) -> dict[str, dict[str, any]]:
    if not os.path.exists(uml_path):
        print(f"Error: UML file not found at {uml_path}")
        sys.exit(1)

    with open(uml_path, encoding="utf-8") as f:
        content = f.read()

    # Find classDiagram mermaid blocks
    mermaid_blocks = re.findall(r"```mermaid\s*(.*?)\s*```", content, re.DOTALL)
    if not mermaid_blocks:
        print(f"Error: No mermaid blocks found in UML file: {uml_path}")
        sys.exit(1)

    diagram = mermaid_blocks[0]

    # Extract class ClassName { ... } blocks
    class_blocks = re.findall(r"class\s+(\w+)\s*\{(.*?)\}", diagram, re.DOTALL)

    classes = {}
    for class_name, block_content in class_blocks:
        fields = set()
        methods = set()

        # Parse members line-by-line
        for line in block_content.splitlines():
            line = line.strip()
            if not line or line.startswith("<<"):
                continue
            # A line looks like: +id: str (PK) or +list_apps() List~dict~
            # or #_log_to_db(...) void
            is_public = True
            member_str = line
            if line.startswith("+"):
                is_public = True
                member_str = line[1:].strip()
            elif line.startswith("-") or line.startswith("#"):
                is_public = False
                member_str = line[1:].strip()

            # Check if it is a method
            if "(" in member_str:
                name_match = re.match(r"^([a-zA-Z0-9_]+)\(", member_str)
                if name_match:
                    method_name = name_match.group(1)
                    methods.add((method_name, is_public))
            else:
                name_match = re.match(r"^([a-zA-Z0-9_]+)", member_str)
                if name_match:
                    field_name = name_match.group(1)
                    fields.add((field_name, is_public))

        classes[class_name] = {"fields": fields, "methods": methods}
    return classes


def parse_python_class(file_path: str, target_class_name: str) -> tuple[set[str], set[str]]:
    if not os.path.exists(file_path):
        print(f"Error: Python file not found at {file_path}")
        return set(), set()

    with open(file_path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=file_path)

    fields = set()
    methods = set()

    class_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == target_class_name:
            class_node = node
            break

    if not class_node:
        return set(), set()

    # Parse methods and attributes
    for item in class_node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.add(item.name)
            # If it is __init__, we also look for self.attribute assignments
            if item.name == "__init__":
                for subnode in ast.walk(item):
                    if isinstance(subnode, ast.Assign):
                        for target in subnode.targets:
                            if (
                                isinstance(target, ast.Attribute)
                                and isinstance(target.value, ast.Name)
                                and target.value.id == "self"
                            ):
                                fields.add(target.attr)
                    elif isinstance(subnode, ast.AnnAssign):
                        target = subnode.target
                        if (
                            isinstance(target, ast.Attribute)
                            and isinstance(target.value, ast.Name)
                            and target.value.id == "self"
                        ):
                            fields.add(target.attr)
        # Look for class level variables (SQLModel fields or class constants)
        elif isinstance(item, ast.AnnAssign):
            if isinstance(item.target, ast.Name):
                fields.add(item.target.id)
        elif isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name):
                    fields.add(target.id)

    return fields, methods


def locate_file_in_backend(file_ref: str, search_dir: str = "backend") -> str:
    filename = file_ref if file_ref.endswith(".py") else f"{file_ref}.py"
    for root, dirs, files in os.walk(search_dir):
        if filename in files:
            return os.path.join(root, filename)
    return None


def verify_flowchart_symbols(md_path: str) -> list[str]:
    if not os.path.exists(md_path):
        return [f"File {md_path} not found"]

    with open(md_path, encoding="utf-8") as f:
        content = f.read()

    # Find all mermaid blocks
    mermaid_blocks = re.findall(r"```mermaid\s*(.*?)\s*```", content, re.DOTALL)
    mismatches = []

    node_pattern = r"\b(\w+)(?:\[\s*|\[\s*\(\s*|\[\s*\(\s*|\(\s*\[\s*|\(\s*\[\s*\(\s*|\{\s*|\(\s*|\(\(\s*)([a-zA-Z0-9_\-\.]+)\s*:\s*([a-zA-Z0-9_]+)"

    for block in mermaid_blocks:
        # Only flowcharts (line starts with `graph TD/LR/BT` or `flowchart ...`)
        first_line = block.strip().splitlines()[0] if block.strip() else ""
        if not re.match(r"^\s*(graph|flowchart)\b", first_line):
            continue

        nodes = re.findall(node_pattern, block)
        for node_id, file_ref, symbol_name in nodes:
            file_path = locate_file_in_backend(file_ref)
            if not file_path:
                mismatches.append(
                    f"Flowchart in {md_path}: node '{node_id}' references file '{file_ref}', "
                    f"but no matching file was found under 'backend/'."
                )
                continue

            # Parse python file AST
            try:
                with open(file_path, encoding="utf-8") as py_f:
                    tree = ast.parse(py_f.read(), filename=file_path)
            except Exception as e:
                mismatches.append(f"Failed to parse python file {file_path}: {e}")
                continue

            # Look for symbol_name at top level definitions OR inside classes.
            found = False
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name == symbol_name:
                        found = True
                        break
                if isinstance(node, ast.ClassDef):
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if child.name == symbol_name:
                                found = True
                                break
                    if found:
                        break
            if not found:
                mismatches.append(
                    f"Flowchart in {md_path}: node '{node_id}' references symbol '{symbol_name}' in {file_path}, "
                    f"but that class or function was not found at the top level."
                )

    return mismatches


def main():
    uml_files = ["backend/UML.md", "backend/agent/harness.md"]
    mismatches = []

    print("Starting UML-Code contract verification...")
    print("-" * 50)

    # 1. Verify Class Diagrams
    for uml_path in uml_files:
        print(f"Parsing UML Class Diagrams in {uml_path}...")
        uml_classes = parse_uml_classes(uml_path)

        for class_name, uml_data in uml_classes.items():
            if class_name not in CLASS_TO_FILE:
                mismatches.append(
                    f"Class '{class_name}' in UML ({uml_path}) is not mapped to any Python file in verify_uml.py."
                )
                continue

            file_path = CLASS_TO_FILE[class_name]
            py_fields, py_methods = parse_python_class(file_path, class_name)

            if not py_fields and not py_methods:
                mismatches.append(f"Class '{class_name}' documented in UML ({uml_path}) was not found in {file_path}.")
                continue

            print(f"  - Verifying class '{class_name}' against {file_path}...")

            # Verify fields (subset verification)
            for uml_field, is_public in uml_data["fields"]:
                matched = False
                candidates = [uml_field]
                if not is_public:
                    candidates.extend([f"_{uml_field}", f"__{uml_field}"])

                for c in candidates:
                    if c in py_fields:
                        matched = True
                        break
                if not matched:
                    mismatches.append(
                        f"Class '{class_name}' in {uml_path}: documented field '{uml_field}' "
                        f"({'public' if is_public else 'private/internal'}) not found in code. Expected one of: {candidates}"
                    )

            # Verify methods (subset verification)
            for uml_method, is_public in uml_data["methods"]:
                matched = False
                expected_py_name = uml_method
                if not is_public and not uml_method.startswith("_"):
                    expected_py_name = f"_{uml_method}"
                elif is_public and uml_method.startswith("_"):
                    expected_py_name = uml_method[1:]

                if expected_py_name in py_methods:
                    matched = True

                if not matched and uml_method in py_methods:
                    print(
                        f"Warning: Class '{class_name}' method '{uml_method}' matched in code, but visibility prefix mismatch."
                    )
                    matched = True

                if not matched:
                    mismatches.append(
                        f"Class '{class_name}' in {uml_path}: documented method '{uml_method}' "
                        f"({'public' if is_public else 'private/internal'}) not found in code. Expected: '{expected_py_name}'"
                    )

    # 2. Verify Flowcharts
    print("-" * 50)
    for uml_path in uml_files:
        print(f"Parsing Flowcharts in {uml_path}...")
        flowchart_mismatches = verify_flowchart_symbols(uml_path)
        if flowchart_mismatches:
            mismatches.extend(flowchart_mismatches)
        else:
            print(f"  - All flowchart references in {uml_path} verified successfully.")

    print("-" * 50)
    if mismatches:
        print("Verification failed! The following mismatches were found:")
        for m in mismatches:
            print(f"  - {m}")
        sys.exit(1)
    else:
        print("Verification succeeded! All documented UML contracts and flowcharts are satisfied in the codebase.")
        sys.exit(0)


if __name__ == "__main__":
    main()
