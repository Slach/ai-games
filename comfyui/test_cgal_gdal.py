#!/usr/bin/env python3
"""
Test script to verify CGAL and GDAL installations
"""
import subprocess
import sys

def test_cgal():
    """Test CGAL Python package"""
    try:
        from CGAL import CGAL_Polygon_mesh_processing
        from CGAL.CGAL_Polyhedron_3 import Polyhedron_3
        from CGAL.CGAL_Kernel import Point_3
        print("✓ CGAL Python package found and working")
        return True
    except ImportError as e:
        print(f"✗ CGAL Python package missing: {e}")
        return False

def test_gdal():
    """Test GDAL Python package"""
    try:
        from osgeo import gdal
        print("✓ GDAL (osgeo) package found and working")
        return True
    except ImportError as e:
        print(f"✗ GDAL package missing: {e}")

    # Try alternative import
    try:
        import gdal
        print("✓ GDAL package found and working")
        return True
    except ImportError as e:
        print(f"✗ Alternative GDAL import also failed: {e}")
        return False

def test_system_gdal():
    """Test if system GDAL library is available"""
    try:
        result = subprocess.run(['gdal-config', '--version'],
                              capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✓ System GDAL library found: {result.stdout.strip()}")
            return True
        else:
            print("✗ System GDAL library not found")
            return False
    except FileNotFoundError:
        print("✗ gdal-config command not found")
        return False

if __name__ == "__main__":
    print("Testing CGAL and GDAL installations...\n")

    print("1. Testing CGAL Python package:")
    cgal_ok = test_cgal()
    print()

    print("2. Testing GDAL Python package:")
    gdal_ok = test_gdal()
    print()

    print("3. Testing system GDAL:")
    sys_gdal_ok = test_system_gdal()
    print()

    print("Summary:")
    print(f"CGAL Python: {'✓' if cgal_ok else '✗'}")
    print(f"GDAL Python: {'✓' if gdal_ok else '✗'}")
    print(f"System GDAL: {'✓' if sys_gdal_ok else '✗'}")

    if not cgal_ok:
        print("\nTo fix CGAL: pip install cgal>=0.9.0")
    if not gdal_ok:
        print("\nTo fix GDAL: pip install gdal or pip install osgeo-gdal")

    return not (cgal_ok and (gdal_ok or sys_gdal_ok))