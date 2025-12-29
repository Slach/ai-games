# Copyright (c) 2020-2022, NVIDIA CORPORATION. All rights reserved.
# Patched to use pre-built extension instead of JIT compilation

import torch
from . import _C

# Re-export functions from the pre-built extension
xfm_points = _C.xfm_points
xfm_vectors = _C.xfm_vectors
image_loss = _C.image_loss
diffuse_cubemap = _C.diffuse_cubemap
specular_cubemap = _C.specular_cubemap
prepare_shading_normal = _C.prepare_shading_normal
lambert = _C.lambert
frostbite_diffuse = _C.frostbite_diffuse
pbr_specular = _C.pbr_specular
pbr_bsdf = _C.pbr_bsdf
_fresnel_shlick = _C._fresnel_shlick
_ndf_ggx = _C._ndf_ggx
_lambda_ggx = _C._lambda_ggx
_masking_smith = _C._masking_smith
