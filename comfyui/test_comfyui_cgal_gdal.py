#!/usr/bin/env python3
"""
Comprehensive test script to verify CGAL and GDAL installations for ComfyUI-GeometryPack and TRELLIS2
"""
import subprocess
import sys
import os

def test_cgal_imports():
    """Test various CGAL imports used by ComfyUI-GeometryPack"""
    tests = [
        ("CGAL_Polygon_mesh_processing", "from CGAL import CGAL_Polygon_mesh_processing"),
        ("CGAL_Polyhedron_3", "from CGAL.CGAL_Polyhedron_3 import Polyhedron_3"),
        ("CGAL_Kernel_Point_3", "from CGAL.CGAL_Kernel import Point_3"),
        ("CGAL_Surface_mesh", "from CGAL.CGAL_Surface_mesh import Surface_mesh")
    ]

    print("Testing CGAL Python imports:")
    all_ok = True
    for name, import_cmd in tests:
        try:
            exec(import_cmd)
            print(f"  ✓ {name}")
        except ImportError as e:
            print(f"  ✗ {name}: {e}")
            all_ok = False

    return all_ok

def test_gdal_imports():
    """Test GDAL imports"""
    tests = [
        ("osgeo.gdal", "from osgeo import gdal"),
        ("osgeo.osr", "from osgeo import osr"),
        ("osgeo.ogr", "from osgeo import ogr")
    ]

    print("\nTesting GDAL Python imports:")
    all_ok = True
    for name, import_cmd in tests:
        try:
            exec(import_cmd)
            print(f"  ✓ {name}")
        except ImportError as e:
            print(f"  ✗ {name}: {e}")
            all_ok = False

    return all_ok

def test_comfyui_geometrypack_imports():
    """Test if ComfyUI-GeometryPack can be imported without CGAL warnings"""
    print("\nTesting ComfyUI-GeometryPack imports:")

    geometry_pack_path = "/opt/ComfyUI/custom_nodes/ComfyUI-GeometryPack"
    if not os.path.exists(geometry_pack_path):
        print("  ⚠ ComfyUI-GeometryPack not found (expected in Docker build)")
        return None

    # Add the path to sys.path temporarily
    sys.path.insert(0, geometry_pack_path)

    try:
        # This is what GeometryPack does internally
        from CGAL import CGAL_Polygon_mesh_processing
        print("  ✓ CGAL import for GeometryPack works")

        # Test if we can import any GeometryPack modules
        try:
            import geometry_pack_nodes  # This might not exist, but let's try
            print("  ✓ GeometryPack nodes import")
        except ImportError:
            print("  ℹ GeometryPack nodes not directly importable (normal)")

        return True
    except ImportError as e:
        print(f"  ✗ GeometryPack CGAL dependency failed: {e}")
        return False
    finally:
        if geometry_pack_path in sys.path:
            sys.path.remove(geometry_pack_path)

def test_pip_packages():
    """Check if the correct packages are installed"""
    print("\nChecking installed pip packages:")

    try:
        result = subprocess.run([sys.executable, "-m", "pip", "list", "--format=freeze"],
                              capture_output=True, text=True)

        packages = result.stdout.lower()
        cgal_found = any("cgal" in line and line.startswith("cgal==") for line in packages.split('\n'))
        gdal_found = any("gdal" in line and ("osgeo-gdal" in line or line.startswith("gdal==")) for line in packages.split('\n'))

        print(f"  {'✓' if cgal_found else '✗'} cgal package")
        print(f"  {'✓' if gdal_found else '✗'} gdal package")

        return cgal_found or gdal_found

    except Exception as e:
        print(f"  ✗ Could not check pip packages: {e}")
        return False

def test_comfyui_node_files():
    """Check if ComfyUI nodes that depend on CGAL are present"""
    print("\nChecking ComfyUI node files:")

    geometry_pack_path = "/opt/ComfyUI/custom_nodes/ComfyUI-GeometryPack"
    trellis2_path = "/opt/ComfyUI/custom_nodes/ComfyUI-TRELLIS2"

    checks = [
        (geometry_pack_path, "ComfyUI-GeometryPack"),
        (trellis2_path, "ComfyUI-TRELLIS2")
    ]

    for path, name in checks:
        if os.path.exists(path):
            print(f"  ✓ {name} found at {path}")
            # Check for CGAL usage in the node files
            try:
                result = subprocess.run(["grep", "-r", "CGAL", path, "--include=*.py"],
                                      capture_output=True, text=True)
                if result.returncode == 0:
                    cgal_lines = len(result.stdout.strip().split('\n'))
                    print(f"    - Contains {cgal_lines} CGAL references")
            except:
                pass
        else:
            print(f"  ✗ {name} not found")

if __name__ == "__main__":
    print("=== ComfyUI CGAL/GDAL Installation Test ===\n")

    # Test 1: CGAL imports
    cgal_ok = test_cgal_imports()

    # Test 2: GDAL imports
    gdal_ok = test_gdal_imports()

    # Test 3: ComfyUI-GeometryPack compatibility
    geom_pack_ok = test_comfyui_geometrypack_imports()

    # Test 4: Pip packages
    pip_ok = test_pip_packages()

    # Test 5: Node files
    test_comfyui_node_files()

    # Summary
    print("\n" + "="*50)
    print("SUMMARY:")
    print(f"CGAL Python: {'✓ PASS' if cgal_ok else '✗ FAIL'}")
    print(f"GDAL Python: {'✓ PASS' if gdal_ok else '✗ FAIL'}")
    print(f"GeometryPack: {'✓ PASS' if geom_pack_ok else '⚠ SKIP' if geom_pack_ok is None else '✗ FAIL'}")
    print(f"Pip Packages: {'✓ PASS' if pip_ok else '✗ FAIL'}")

    all_ok = cgal_ok and (gdal_ok or pip_ok)

    if all_ok:
        print("\n🎉 All tests passed! ComfyUI-GeometryPack should work correctly.")
        print("TRELLIS2 should also work since CGAL dependencies are satisfied.")
    else:
        print("\n❌ Some tests failed. Check the issues above.")
        if not cgal_ok:
            print("  - Fix CGAL: pip install cgal>=0.9.0")
        if not gdal_ok and not pip_ok:
            print("  - Fix GDAL: pip install osgeo-gdal")

    sys.exit(0 if all_ok else 1)