#!/usr/bin/env python3
"""
Script to handle pip dependency conflicts in Docker builds.
This script will:
1. Run pip freeze to capture current installed packages
2. Identify conflicting dependencies in pyproject.toml or requirements.txt files
3. Update those files to match the frozen versions
"""

import ast
import os
import sys
import subprocess
import re
from pathlib import Path

# Try to import toml, install it if not available
try:
    import toml
except ImportError:
    print("toml package not found, installing...")
    # Try to use the ComfyUI virtual environment pip first
    pip_executable = "/opt/ComfyUI/.venv/bin/pip"
    if os.path.exists(pip_executable):
        subprocess.check_call([pip_executable, "install", "toml"])
    else:
        # Fallback to system pip
        subprocess.check_call([sys.executable, "-m", "pip", "install", "toml"])
    import toml


def run_pip_freeze(output_file="/opt/pip_freeze.txt"):
    """Run pip freeze and save to file"""
    print(f"Running pip freeze and saving to {output_file}")

    # Try to use the ComfyUI virtual environment pip first
    pip_executable = "/opt/ComfyUI/.venv/bin/pip"
    if os.path.exists(pip_executable):
        result = subprocess.run([pip_executable, "freeze"],
                              capture_output=True, text=True)
    else:
        # Fallback to system pip
        result = subprocess.run([sys.executable, "-m", "pip", "freeze"],
                              capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error running pip freeze: {result.stderr}")
        return False

    with open(output_file, 'w') as f:
        f.write(result.stdout)

    print(f"Successfully saved pip freeze output to {output_file}")
    return True


def parse_pip_freeze(freeze_file="/opt/pip_freeze.txt"):
    """Parse pip freeze output into a dictionary of package -> version"""
    packages = {}
    with open(freeze_file, 'r') as f:
        for line in f:
            line = line.strip()
            if '==' in line:
                # Split on == to get package name and version
                parts = line.split('==', 1)
                package_name = parts[0].lower().strip()
                version = parts[1].strip()
                packages[package_name] = version
            elif '@' in line and '==' not in line:
                # Handle packages installed from URLs, e.g., git+https://...
                # Format: package-name @ git+https://...
                if ' @ ' in line:
                    package_name = line.split(' @ ')[0].lower().strip()
                    packages[package_name] = "git+url"  # Mark as git-installed
    return packages


def update_requirements_txt(requirements_path, frozen_packages):
    """Update requirements.txt to match frozen package versions"""
    if not os.path.exists(requirements_path):
        print(f"Requirements file {requirements_path} does not exist, skipping")
        return False
    
    print(f"Updating {requirements_path}")
    
    with open(requirements_path, 'r') as f:
        content = f.read()
    
    original_content = content
    
    # Find all package references in the requirements file
    # This regex matches package names with optional version specifiers
    lines = content.split('\n')
    updated_lines = []
    
    for line in lines:
        stripped_line = line.strip()
        if not stripped_line or stripped_line.startswith('#'):
            updated_lines.append(line)
            continue
        
        # Extract package name from the line (before version specifiers)
        # Handle cases like: package==1.0.0, package>=1.0.0, package~=1.0.0, etc.
        package_match = re.match(r'^([a-zA-Z0-9\-_.]+)', stripped_line)
        if package_match:
            req_package = package_match.group(1).lower()
            if req_package in frozen_packages:
                frozen_version = frozen_packages[req_package]
                if frozen_version == "git+url":
                    # Skip git-installed packages as they can't be easily pinned
                    updated_lines.append(line)
                else:
                    # Replace the entire line with the frozen version
                    new_line = f"{req_package}=={frozen_version}"
                    # Preserve any comments or whitespace from the original line
                    indent = line[:len(line) - len(line.lstrip())]
                    updated_lines.append(indent + new_line)
                    print(f"  Updated {req_package} to {frozen_version}")
            else:
                updated_lines.append(line)
        else:
            updated_lines.append(line)
    
    new_content = '\n'.join(updated_lines)
    
    if new_content != original_content:
        with open(requirements_path, 'w') as f:
            f.write(new_content)
        print(f"Updated {requirements_path} with frozen package versions")
        return True
    else:
        print(f"No changes needed for {requirements_path}")
        return False


def update_pyproject_toml(pyproject_path, frozen_packages):
    """Update pyproject.toml to match frozen package versions"""
    if not os.path.exists(pyproject_path):
        print(f"pyproject.toml file {pyproject_path} does not exist, skipping")
        return False
    
    print(f"Updating {pyproject_path}")
    
    try:
        with open(pyproject_path, 'r') as f:
            data = toml.load(f)
    except Exception as e:
        print(f"Error reading {pyproject_path}: {e}")
        return False
    
    original_data = data.copy()
    
    # Check for dependencies in different sections
    sections_to_check = [
        'project.dependencies',
        'project.optional-dependencies',
        'tool.poetry.dependencies'
    ]
    
    updated = False
    
    # Handle project.dependencies (PEP 621 style)
    if 'project' in data and 'dependencies' in data['project']:
        deps = data['project']['dependencies']
        for i, dep in enumerate(deps):
            # Extract package name from dependency string
            package_match = re.match(r'^([a-zA-Z0-9\-_.]+)', dep)
            if package_match:
                req_package = package_match.group(1).lower()
                if req_package in frozen_packages:
                    frozen_version = frozen_packages[req_package]
                    if frozen_version != "git+url":
                        new_dep = f"{req_package}=={frozen_version}"
                        data['project']['dependencies'][i] = new_dep
                        print(f"  Updated {req_package} to {frozen_version} in project.dependencies")
                        updated = True
    
    # Handle optional dependencies
    if 'project' in data and 'optional-dependencies' in data['project']:
        for extra_name, deps in data['project']['optional-dependencies'].items():
            for i, dep in enumerate(deps):
                package_match = re.match(r'^([a-zA-Z0-9\-_.]+)', dep)
                if package_match:
                    req_package = package_match.group(1).lower()
                    if req_package in frozen_packages:
                        frozen_version = frozen_packages[req_package]
                        if frozen_version != "git+url":
                            new_dep = f"{req_package}=={frozen_version}"
                            data['project']['optional-dependencies'][extra_name][i] = new_dep
                            print(f"  Updated {req_package} to {frozen_version} in project.optional-dependencies.{extra_name}")
                            updated = True
    
    # Handle poetry dependencies
    if 'tool' in data and 'poetry' in data['tool'] and 'dependencies' in data['tool']['poetry']:
        for pkg_name, version in data['tool']['poetry']['dependencies'].items():
            req_package = pkg_name.lower()
            if req_package in frozen_packages:
                frozen_version = frozen_packages[req_package]
                if frozen_version != "git+url":
                    data['tool']['poetry']['dependencies'][pkg_name] = frozen_version
                    print(f"  Updated {req_package} to {frozen_version} in poetry.dependencies")
                    updated = True
    
    if updated:
        with open(pyproject_path, 'w') as f:
            toml.dump(data, f)
        print(f"Updated {pyproject_path} with frozen package versions")
        return True
    else:
        print(f"No changes needed for {pyproject_path}")
        return False


def find_and_update_dependencies(frozen_packages, search_path="."):
    """Find and update all requirements.txt, setup.py, and pyproject.toml files"""
    search_path = Path(search_path)

    # Find all requirements.txt files (including in subdirectories)
    requirements_files = list(search_path.rglob("requirements*.txt"))

    # Find all pyproject.toml files (including in subdirectories)
    pyproject_files = list(search_path.rglob("pyproject.toml"))

    # Find all setup.py files (including in subdirectories)
    setup_files = list(search_path.rglob("setup.py"))

    print(f"Found {len(requirements_files)} requirements files to process")
    print(f"Found {len(pyproject_files)} pyproject.toml files to process")
    print(f"Found {len(setup_files)} setup.py files to process")

    updated_count = 0

    for req_file in requirements_files:
        if update_requirements_txt(str(req_file), frozen_packages):
            updated_count += 1

    for pyproj_file in pyproject_files:
        if update_pyproject_toml(str(pyproj_file), frozen_packages):
            updated_count += 1

    for setup_file in setup_files:
        if update_setup_py(str(setup_file), frozen_packages):
            updated_count += 1

    print(f"Updated {updated_count} files with frozen package versions")
    return updated_count


def update_setup_py(setup_path, frozen_packages):
    """Update setup.py to match frozen package versions"""
    if not os.path.exists(setup_path):
        print(f"Setup file {setup_path} does not exist, skipping")
        return False

    print(f"Updating {setup_path}")

    with open(setup_path, 'r') as f:
        content = f.read()

    original_content = content

    # Look for install_requires and other dependency specifications in setup.py
    # This regex finds patterns like: install_requires=['package>=1.0', ...]
    # First, find the install_requires section
    import ast
    try:
        tree = ast.parse(content)
        visitor = SetupPyVisitor(frozen_packages)
        visitor.visit(tree)

        if visitor.updated:
            # Write the modified AST back to the file
            import ast
            updated_content = ast.unparse(tree)  # This converts AST back to source code
            with open(setup_path, 'w') as f:
                f.write(updated_content)
            print(f"Updated {setup_path} with frozen package versions")
            return True
        else:
            print(f"No changes needed for {setup_path}")
            return False
    except Exception as e:
        print(f"Error parsing {setup_path}: {e}")
        # Fallback to regex-based approach for simple cases
        return update_setup_py_fallback(content, setup_path, frozen_packages)


class SetupPyVisitor(ast.NodeTransformer):
    def __init__(self, frozen_packages):
        self.frozen_packages = frozen_packages
        self.updated = False

    def visit_Call(self, node):
        # Look for setup() calls
        if isinstance(node.func, ast.Name) and node.func.id == 'setup':
            for keyword in node.keywords:
                if keyword.arg in ['install_requires', 'extras_require']:
                    if isinstance(keyword.value, ast.List):
                        keyword.value = self._update_list_node(keyword.value)
                    elif isinstance(keyword.value, ast.Dict):  # extras_require case
                        keyword.value = self._update_extras_require_node(keyword.value)
        return self.generic_visit(node)

    def _update_list_node(self, list_node):
        updated = False
        new_elts = []
        for elt in list_node.elts:
            if isinstance(elt, ast.Str):  # Python < 3.8
                updated_req = self._update_requirement(elt.s)
                if updated_req and updated_req != elt.s:
                    new_elt = ast.Str(s=updated_req)
                    new_elts.append(new_elt)
                    updated = True
                else:
                    new_elts.append(elt)
            elif isinstance(elt, ast.Constant) and isinstance(elt.value, str):  # Python 3.8+
                updated_req = self._update_requirement(elt.value)
                if updated_req and updated_req != elt.value:
                    new_elt = ast.Constant(value=updated_req)
                    new_elts.append(new_elt)
                    updated = True
                else:
                    new_elts.append(elt)
            elif isinstance(elt, ast.JoinedStr):  # f-strings are not handled here
                new_elts.append(elt)  # Skip f-strings for now
            else:
                new_elts.append(elt)

        if updated:
            self.updated = True
            list_node.elts = new_elts

        return list_node

    def _update_extras_require_node(self, dict_node):
        updated = False
        for i, key in enumerate(dict_node.keys):
            if isinstance(key, ast.Str):  # Python < 3.8
                value = dict_node.values[i]
            elif isinstance(key, ast.Constant):  # Python 3.8+
                value = dict_node.values[i]
            else:
                continue

            # The value is the list of requirements
            if isinstance(value, ast.List):
                updated_value = self._update_list_node(value)
                if updated_value is not value:  # If it was modified
                    dict_node.values[i] = updated_value
                    updated = True

        if updated:
            self.updated = True

        return dict_node

    def _update_requirement(self, req):
        # Extract package name from requirement string (e.g., "package>=1.0,<2.0" -> "package")
        package_match = re.match(r'^([a-zA-Z0-9\-_.]+)', req)
        if package_match:
            req_package = package_match.group(1).lower()
            if req_package in self.frozen_packages:
                frozen_version = self.frozen_packages[req_package]
                if frozen_version != "git+url":
                    # Preserve version specifiers but update the version
                    # For example: "package>=1.0,<2.0" -> "package=={frozen_version}"
                    new_req = f"{req_package}=={frozen_version}"
                    return new_req
        return None


def update_setup_py_fallback(content, setup_path, frozen_packages):
    """Fallback method to update setup.py using regex if AST parsing fails"""
    import re

    original_content = content

    # Find install_requires section in setup.py
    # Pattern matches: install_requires=['...', '...', ...]
    pattern = r'(install_requires\s*=\s*\[)([^\]]+)(\])'

    def replace_requirements(match):
        prefix, reqs_content, suffix = match.group(1, 2, 3)

        # Split requirements by comma, being careful with nested quotes
        reqs = []
        current = ""
        quote_char = None
        i = 0
        while i < len(reqs_content):
            char = reqs_content[i]

            if quote_char is None and char in ['"', "'"]:
                quote_char = char
                current += char
            elif char == quote_char:
                current += char
                quote_char = None
            elif char == ',' and quote_char is None:
                reqs.append(current.strip())
                current = ""
            else:
                current += char
            i += 1

        if current.strip():
            reqs.append(current.strip())

        updated_reqs = []
        for req in reqs:
            # Remove leading/trailing whitespace and quotes
            req = req.strip()
            if not req:
                updated_reqs.append(req)
                continue

            # Extract package name from requirement string
            package_match = re.match(r'^[\'"]?([a-zA-Z0-9\-_.]+)', req)
            if package_match:
                req_package = package_match.group(1).lower()
                if req_package in frozen_packages:
                    frozen_version = frozen_packages[req_package]
                    if frozen_version != "git+url":
                        # Create new requirement with exact version
                        quote_start = req[0] if req[0] in ['"', "'"] else "'"
                        quote_end = req[-1] if req[-1] in ['"', "'"] else "'"
                        new_req = f"{quote_start}{req_package}=={frozen_version}{quote_end}"
                        updated_reqs.append(new_req)
                        print(f"  Updated {req_package} to {frozen_version}")
                    else:
                        updated_reqs.append(req)
                else:
                    updated_reqs.append(req)
            else:
                updated_reqs.append(req)

        return prefix + ', '.join(updated_reqs) + suffix

    updated_content = re.sub(pattern, replace_requirements, content)

    if updated_content != original_content:
        with open(setup_path, 'w') as f:
            f.write(updated_content)
        print(f"Updated {setup_path} with frozen package versions using fallback method")
        return True
    else:
        print(f"No changes needed for {setup_path} using fallback method")
        return False


def main():
    print("Starting pip conflict resolution...")

    # Parse command line arguments to get search path
    search_path = sys.argv[1] if len(sys.argv) > 1 else "."

    # Run pip freeze to capture current state
    if not run_pip_freeze():
        print("Failed to run pip freeze, exiting")
        return 1

    # Parse the freeze output
    frozen_packages = parse_pip_freeze()
    print(f"Found {len(frozen_packages)} packages in pip freeze")

    # Find and update all dependency files in the specified search path
    updated_files = find_and_update_dependencies(frozen_packages, search_path)

    print(f"Successfully processed {updated_files} files in {search_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())