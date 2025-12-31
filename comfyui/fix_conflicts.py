#!/usr/bin/env python3
"""
Script to handle pip dependency conflicts in Docker builds.
This script will:
1. Run pip freeze to capture current installed packages
2. Identify conflicting dependencies in pyproject.toml or requirements.txt files
3. Update those files to match the frozen versions
"""

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
    subprocess.check_call([sys.executable, "-m", "pip", "install", "toml"])
    import toml


def run_pip_freeze(output_file="/opt/pip_freeze.txt"):
    """Run pip freeze and save to file"""
    print(f"Running pip freeze and saving to {output_file}")
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


def find_and_update_dependencies(frozen_packages, search_path="/opt"):
    """Find and update all requirements.txt and pyproject.toml files"""
    search_path = Path(search_path)
    
    # Find all requirements.txt files
    requirements_files = list(search_path.rglob("requirements*.txt"))
    requirements_files.extend(list(search_path.glob("requirements*.txt")))
    
    # Find all pyproject.toml files
    pyproject_files = list(search_path.rglob("pyproject.toml"))
    pyproject_files.extend(list(search_path.glob("pyproject.toml")))
    
    print(f"Found {len(requirements_files)} requirements files to process")
    print(f"Found {len(pyproject_files)} pyproject.toml files to process")
    
    updated_count = 0
    
    for req_file in requirements_files:
        if update_requirements_txt(str(req_file), frozen_packages):
            updated_count += 1
    
    for pyproj_file in pyproject_files:
        if update_pyproject_toml(str(pyproj_file), frozen_packages):
            updated_count += 1
    
    print(f"Updated {updated_count} files with frozen package versions")
    return updated_count


def main():
    print("Starting pip conflict resolution...")
    
    # Run pip freeze to capture current state
    if not run_pip_freeze():
        print("Failed to run pip freeze, exiting")
        return 1
    
    # Parse the freeze output
    frozen_packages = parse_pip_freeze()
    print(f"Found {len(frozen_packages)} packages in pip freeze")
    
    # Find and update all dependency files
    updated_files = find_and_update_dependencies(frozen_packages)
    
    print(f"Successfully processed {updated_files} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())