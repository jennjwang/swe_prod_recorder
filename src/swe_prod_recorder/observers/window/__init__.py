import sys

if sys.platform == "darwin":
    from .window_osx import select_region_with_mouse
elif sys.platform == "linux":
    from .window_linux import select_region_with_mouse
else:
    raise NotImplementedError(f"Platform {sys.platform} not supported")
