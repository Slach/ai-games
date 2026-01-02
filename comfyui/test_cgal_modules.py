#!/usr/bin/env python3
try:
    from CGAL import CGAL_Polygon_mesh_processing
    print("CGAL OK")
except ImportError as e:
    print("CGAL MISSING:", e)

try:
    from osgeo import gdal
    print("GDAL OK")
except ImportError as e:
    print("GDAL MISSING:", e)

import subprocess
result = subprocess.run(['/opt/ComfyUI/.venv/bin/pip', 'list'], capture_output=True, text=True)
print("\nInstalled packages:")
for line in result.stdout.split('\n'):
    if 'cgal' in line.lower() or 'gdal' in line.lower():
        print(line)