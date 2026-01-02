#!/usr/bin/env python3
"""
Test script to verify that CGAL is working properly in the Docker image
"""

def test_cgal():
    try:
        print("Testing CGAL import...")
        from CGAL import CGAL_Polygon_mesh_processing
        print("✓ CGAL Polygon_mesh_processing imported successfully")
        
        from CGAL.CGAL_Polyhedron_3 import Polyhedron_3
        print("✓ CGAL Polyhedron_3 imported successfully")
        
        from CGAL.CGAL_Kernel import Point_3
        print("✓ CGAL Kernel Point_3 imported successfully")
        
        # Test basic functionality
        p1 = Point_3(0, 0, 0)
        p2 = Point_3(1, 0, 0)
        p3 = Point_3(0, 1, 0)
        print(f"✓ Created points: {p1}, {p2}, {p3}")
        
        print("All CGAL tests passed!")
        return True
    except ImportError as e:
        print(f"✗ CGAL import failed: {e}")
        return False
    except Exception as e:
        print(f"✗ CGAL test failed: {e}")
        return False

if __name__ == "__main__":
    success = test_cgal()
    if success:
        print("\n🎉 CGAL is working properly in the Docker image!")
    else:
        print("\n❌ CGAL is not working properly")
        exit(1)