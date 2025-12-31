#!/usr/bin/env python3
"""
Test script for fix_conflicts.py functionality
"""
import os
import tempfile
import subprocess
from pathlib import Path

def test_fix_conflicts():
    # Create a temporary directory structure
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Create test files
        # 1. requirements.txt
        req_file = temp_path / "requirements.txt"
        req_file.write_text("torch>=1.0\nnumpy>=1.18\ntensorflow\n")
        
        # 2. pyproject.toml
        pyproj_file = temp_path / "pyproject.toml"
        pyproj_content = """
[project]
dependencies = [
    "torch>=1.0",
    "requests>=2.0"
]
"""
        pyproj_file.write_text(pyproj_content)
        
        # 3. setup.py
        setup_file = temp_path / "setup.py"
        setup_content = """
from setuptools import setup

setup(
    name="test-package",
    install_requires=[
        "torch>=1.0",
        "numpy>=1.18",
        "pandas"
    ]
)
"""
        setup_file.write_text(setup_content)
        
        # Create a mock pip freeze output file
        freeze_file = temp_path / "pip_freeze.txt"
        freeze_content = """torch==2.5.0
numpy==1.24.3
requests==2.28.2
pandas==1.5.3
"""
        freeze_file.write_text(freeze_content)
        
        # Test the script by modifying the fix_conflicts.py to use our test freeze file
        # First, backup the original function
        script_path = Path("/home/slach/src/github.com/Slach/ai-games/comfyui/fix_conflicts.py")
        
        # Create a modified version of the script for testing
        test_script_content = script_path.read_text()
        
        # Replace the run_pip_freeze function to use our test file
        modified_content = test_script_content.replace(
            "def run_pip_freeze(output_file=\"/opt/pip_freeze.txt\"):",
            "def run_pip_freeze(output_file=None):"
        ).replace(
            "if result.returncode != 0:",
            "if result.returncode != 0:\n        # Use test file instead\n        import shutil\n        shutil.copy(\"" + str(freeze_file) + "\", \"/tmp/test_pip_freeze.txt\")\n        return True"
        ).replace(
            "with open(output_file, 'w') as f:",
            "with open(\"/tmp/test_pip_freeze.txt\", 'w') as f:"
        ).replace(
            "result.stdout",
            "result.stdout if result.returncode == 0 else \"\""
        ).replace(
            "parse_pip_freeze()",
            "parse_pip_freeze(\"/tmp/test_pip_freeze.txt\")"
        ).replace(
            "def parse_pip_freeze(freeze_file=\"/opt/pip_freeze.txt\"):",
            "def parse_pip_freeze(freeze_file=\"/tmp/test_pip_freeze.txt\"):"
        )
        
        # Write the modified script
        test_script_path = temp_path / "test_fix_conflicts.py"
        test_script_path.write_text(modified_content)
        
        # Run the test script on our temp directory
        result = subprocess.run([
            "python3", str(test_script_path), str(temp_path)
        ], capture_output=True, text=True, cwd=temp_path)
        
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        print("Return code:", result.returncode)
        
        # Check the updated files
        print("\nUpdated requirements.txt:")
        print(req_file.read_text())
        
        print("\nUpdated pyproject.toml:")
        print(pyproj_file.read_text())
        
        print("\nUpdated setup.py:")
        print(setup_file.read_text())

if __name__ == "__main__":
    test_fix_conflicts()