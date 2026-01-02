#!/usr/bin/env python3
try:
    from CGAL import CGAL_Polygon_mesh_processing
    print("CGAL OK")
except ImportError as e:
    print("CGAL MISSING:", e)

try:
    import gdal
    print("GDAL OK")
except ImportError as e:
    print("GDAL MISSING:", e)

try:
    from osgeo import gdal as osgeo_gdal
    print("OSGEO GDAL OK")
except ImportError as e:
    print("OSGEO GDAL MISSING:", e)