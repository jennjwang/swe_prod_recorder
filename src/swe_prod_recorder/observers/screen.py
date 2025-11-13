"""Screen observer for capturing screenshots around user interactions.

This module handles cross-platform screen capture with special attention to macOS
coordinate system complexities:

Coordinate Systems (macOS):
- Cocoa/pynput: Y=0 at bottom-left (native mouse events)
- Screen: Y=0 at top-left (internal storage)
- Quartz: Y=0 at bottom-left (CGWindowListCopyWindowInfo)
- mss: Y=0 at bottom-left (screen capture library)

Key Conversions:
- Cocoa → Screen: screen_y = gmax_y - cocoa_y
- Screen → Quartz: quartz_y = gmax_y - screen_y - height
- Quartz → Screen: screen_y = gmax_y - quartz_y - height

Window Tracking:
- Tracks window position dynamically as it moves
- Preserves original window dimensions from selection (handles Electron apps)
- Verifies tracked window is topmost before capturing interactions
"""

from __future__ import annotations

import asyncio
import base64
import gc
import logging
import os
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from importlib.resources import files as get_package_file
from typing import Any, Dict, Iterable, List, Optional

import mss
from PIL import Image, ImageDraw

try:
    # Optional dependencies
    from pydrive.auth import GoogleAuth
    from pydrive.drive import GoogleDrive
except ImportError:
    USE_GDRIVE = False
else:
    USE_GDRIVE = True
from pynput import keyboard, mouse  # still synchronous
from shapely.geometry import box
from shapely.ops import unary_union

from ..schemas import Update
from .observer import Observer
from .window import select_region_with_mouse


def initialize_google_drive(client_secrets_path: str = None) -> GoogleDrive:
    """
    Initialize Google Drive authentication with optional custom client_secrets.json path.

    Parameters
    ----------
    client_secrets_path : str, optional
        Path to the client_secrets.json file. If None, uses default location.

    Returns
    -------
    GoogleDrive
        Authenticated Google Drive client
    """
    gauth = GoogleAuth()

    if client_secrets_path:
        # Expand user path and get absolute path
        client_secrets_path = os.path.abspath(os.path.expanduser(client_secrets_path))

        # Verify the file exists
        if not os.path.exists(client_secrets_path):
            raise FileNotFoundError(
                f"Client secrets file not found: {client_secrets_path}"
            )

        # Copy the client_secrets.json to current directory temporarily
        import shutil

        temp_client_secrets = "client_secrets.json"

        try:
            shutil.copy2(client_secrets_path, temp_client_secrets)
            print(f"✅ Copied client_secrets.json to current directory")

            # Use default behavior (PyDrive will find client_secrets.json in current directory)
            gauth.LocalWebserverAuth()  # Opens browser for first-time authentication

        finally:
            # Clean up temporary file
            try:
                os.remove(temp_client_secrets)
                print(f"✅ Cleaned up temporary client_secrets.json")
            except OSError:
                pass  # File might already be deleted
    else:
        # Use default behavior (looks for client_secrets.json in current directory)
        gauth.LocalWebserverAuth()  # Opens browser for first-time authentication

    return GoogleDrive(gauth)


# Initialize with default behavior (looks for client_secrets.json in current directory)
# drive = initialize_google_drive()


def list_folders(drive: GoogleDrive):
    """List all folders in Google Drive to help find folder IDs"""
    folders = drive.ListFile(
        {"q": "mimeType='application/vnd.google-apps.folder' and trashed=false"}
    ).GetList()
    print("Available folders:")
    for folder in folders:
        print(f"Name: {folder['title']}, ID: {folder['id']}")
    return folders


def find_folder_by_name(folder_name: str, drive: GoogleDrive):
    """Find a folder by name and return its ID"""
    folders = drive.ListFile(
        {
            "q": f"mimeType='application/vnd.google-apps.folder' and title='{folder_name}' and trashed=false"
        }
    ).GetList()
    if folders:
        return folders[0]["id"]
    return None


def upload_file(path: str, drive_dir: str, drive_instance: GoogleDrive):
    """Upload a file to Google Drive and delete the local file.

    Parameters
    ----------
    path : str
        Path to the file to upload
    drive_dir : str
        Google Drive folder ID to upload to
    drive_instance : GoogleDrive
        Google Drive client instance.
    """
    upload_file = drive_instance.CreateFile(
        {"title": path.split("/")[-1], "parents": [{"id": drive_dir}]}
    )
    upload_file.SetContentFile(path)
    upload_file.Upload()
    os.remove(path)


###############################################################################
# Window‑geometry helpers                                                     #
###############################################################################


def _get_global_bounds() -> tuple[float, float, float, float]:
    """Return a bounding box enclosing **all** physical displays.

    Returns
    -------
    (min_x, min_y, max_x, max_y) tuple in Quartz global coordinates.
    """
    import Quartz

    err, ids, cnt = Quartz.CGGetActiveDisplayList(16, None, None)
    if err != Quartz.kCGErrorSuccess:  # pragma: no cover (defensive)
        raise OSError(f"CGGetActiveDisplayList failed: {err}")

    min_x = min_y = float("inf")
    max_x = max_y = -float("inf")
    for did in ids[:cnt]:
        r = Quartz.CGDisplayBounds(did)
        x0, y0 = r.origin.x, r.origin.y
        x1, y1 = x0 + r.size.width, y0 + r.size.height
        min_x, min_y = min(min_x, x0), min(min_y, y0)
        max_x, max_y = max(max_x, x1), max(max_y, y1)
    return min_x, min_y, max_x, max_y


def _get_visible_windows() -> List[tuple[dict, float]]:
    """List *onscreen* windows with their visible‑area ratio.

    Each tuple is ``(window_info_dict, visible_ratio)`` where *visible_ratio*
    is in ``[0.0, 1.0]``.  Internal system windows (Dock, WindowServer, …) are
    ignored.
    """
    import Quartz

    _, _, _, gmax_y = _get_global_bounds()

    opts = (
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListOptionIncludingWindow
    )
    wins = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID)

    occupied = None  # running union of opaque regions above the current window
    result: list[tuple[dict, float]] = []

    for info in wins:
        owner = info.get("kCGWindowOwnerName", "")
        if owner in ("Dock", "WindowServer", "Window Server"):
            continue

        bounds = info.get("kCGWindowBounds", {})
        x, y, w, h = (
            bounds.get("X", 0),
            bounds.get("Y", 0),
            bounds.get("Width", 0),
            bounds.get("Height", 0),
        )
        if w <= 0 or h <= 0:
            continue  # hidden or minimised

        inv_y = gmax_y - y - h  # Quartz→Shapely Y‑flip
        poly = box(x, inv_y, x + w, inv_y + h)
        if poly.is_empty:
            continue

        visible = poly if occupied is None else poly.difference(occupied)
        if not visible.is_empty:
            ratio = visible.area / poly.area
            result.append((info, ratio))
            occupied = poly if occupied is None else unary_union([occupied, poly])

    return result


def _get_window_by_name(window_name: str) -> Optional[tuple[int, dict]]:
    """Get window ID and bounds by owner name.

    Returns
    -------
    tuple[int, dict] or None
        (window_id, bounds_dict) where bounds are in screen coordinates (Y=0 at top)
        {'left': x, 'top': y, 'width': w, 'height': h}
    """
    import Quartz

    _, _, _, gmax_y = _get_global_bounds()

    opts = (
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListOptionIncludingWindow
    )
    wins = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID)

    for info in wins:
        owner = info.get("kCGWindowOwnerName", "")
        if owner == window_name:
            window_id = info.get("kCGWindowNumber")
            if window_id is None:
                continue

            bounds = info.get("kCGWindowBounds", {})
            x = int(bounds.get("X", 0))
            y = int(bounds.get("Y", 0))
            w = int(bounds.get("Width", 0))
            h = int(bounds.get("Height", 0))
            if w > 0 and h > 0:
                # Flip Y coordinate from Quartz (bottom-left origin) to screen (top-left origin)
                top = int(gmax_y - y - h)
                bounds_dict = {"left": x, "top": top, "width": w, "height": h}
                return (window_id, bounds_dict)
    return None


def _get_window_bounds_by_id(window_id: int) -> Optional[dict]:
    """Get window bounds by window ID.

    Returns
    -------
    dict or None
        Bounds in screen coordinates (Y=0 at top)
        {'left': x, 'top': y, 'width': w, 'height': h}
    """
    import Quartz

    _, _, _, gmax_y = _get_global_bounds()

    opts = (
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListOptionIncludingWindow
    )
    wins = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID)

    for info in wins:
        wid = info.get("kCGWindowNumber")
        if wid == window_id:
            bounds = info.get("kCGWindowBounds", {})
            x = int(bounds.get("X", 0))
            y = int(bounds.get("Y", 0))
            w = int(bounds.get("Width", 0))
            h = int(bounds.get("Height", 0))
            if w > 0 and h > 0:
                # Flip Y coordinate from Quartz (bottom-left origin) to screen (top-left origin)
                top = int(gmax_y - y - h)
                return {"left": x, "top": top, "width": w, "height": h}
    return None


def list_available_windows() -> List[str]:
    """List all available window names that can be tracked.

    Returns a list of window owner names that are currently visible.
    Excludes system windows like Dock and WindowServer.
    """
    import Quartz

    opts = (
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListOptionIncludingWindow
    )
    wins = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID)

    window_names = set()
    for info in wins:
        owner = info.get("kCGWindowOwnerName", "")
        if owner and owner not in ("Dock", "WindowServer", "Window Server"):
            bounds = info.get("kCGWindowBounds", {})
            w = bounds.get("Width", 0)
            h = bounds.get("Height", 0)
            if w > 0 and h > 0:
                window_names.add(owner)

    return sorted(window_names)


def _is_app_visible(names: Iterable[str]) -> bool:
    """Return *True* if **any** window from *names* is at least partially visible."""
    targets = set(names)
    return any(
        info.get("kCGWindowOwnerName", "") in targets and ratio > 0
        for info, ratio in _get_visible_windows()
    )


###############################################################################
# Screen observer                                                             #
###############################################################################


class Screen(Observer):
    """
    Capture before/after screenshots around user interactions.

    Coordinate System Handling (macOS):
    - pynput mouse events: Cocoa coordinates (Y=0 at bottom)
    - Internal storage: Screen coordinates (Y=0 at top)
    - Quartz window queries: Return Quartz coordinates (Y=0 at bottom)
    - mss.grab(): Expects Quartz coordinates (Y=0 at bottom)

    Conversions:
    - pynput → screen: screen_y = gmax_y - pynput_y
    - screen → mss: mss_top = gmax_y - screen_top - height
    - Quartz → screen: screen_top = gmax_y - quartz_y - height

    Window Tracking:
    - Use `track_window` parameter to dynamically follow a specific window
    - The capture region automatically updates when the window moves
    - Window dimensions from selection are preserved (handles Electron apps)
    - Use `list_available_windows()` to see available window names
    - Example: Screen(track_window="Google Chrome")

    Keyboard Events:
    - Only the first and last screenshots are kept for consecutive key presses
    - Intermediate screenshots are automatically deleted
    - A keyboard session ends after `keyboard_timeout` seconds of inactivity
    """

    _CAPTURE_FPS: int = 5  # Lower FPS to reduce CPU/memory usage
    _PERIODIC_SEC: int = 30
    _DEBOUNCE_SEC: int = 1
    _MON_START: int = 1  # first real display in mss
    _MEMORY_CLEANUP_INTERVAL: int = 10  # More frequent GC to prevent memory buildup
    _MAX_WORKERS: int = 4  # Limit thread pool size to prevent exhaustion
    _MAX_SCREENSHOT_AGE: int = 3600  # Delete screenshots older than 1 hour (in seconds)

    # Scroll filtering constants
    _SCROLL_DEBOUNCE_SEC: float = 0.8  # Minimum time between scroll events
    _SCROLL_MIN_DISTANCE: float = 8.0  # Minimum scroll distance to log
    _SCROLL_MAX_FREQUENCY: int = 8  # Max scroll events per second
    _SCROLL_SESSION_TIMEOUT: float = 3.0  # Timeout for scroll sessions

    # ─────────────────────────────── construction
    def __init__(
        self,
        screenshots_dir: str = "~/Downloads/records/screenshots",
        skip_when_visible: Optional[str | list[str]] = None,
        history_k: int = 10,
        debug: bool = False,
        keyboard_timeout: float = 2.0,
        gdrive_dir: str = "screenshots",
        client_secrets_path: str = "~/Desktop/client_secrets.json",
        scroll_debounce_sec: float = 0.5,
        scroll_min_distance: float = 5.0,
        scroll_max_frequency: int = 10,
        scroll_session_timeout: float = 2.0,
        upload_to_gdrive: bool = False,
        target_coordinates: Optional[tuple[int, int, int, int]] = None,
        track_window: Optional[str] = None,
        inactivity_timeout: float = 45 * 60,  # 45 minutes in seconds
        start_listeners_on_main_thread: bool = False,  # macOS: run listeners on main thread
    ) -> None:
        self.screens_dir = os.path.abspath(os.path.expanduser(screenshots_dir))
        os.makedirs(self.screens_dir, exist_ok=True)

        self._guard = (
            {skip_when_visible}
            if isinstance(skip_when_visible, str)
            else set(skip_when_visible or [])
        )
        self.upload_to_gdrive = upload_to_gdrive

        self.debug = debug

        # Custom thread pool to prevent exhaustion
        self._thread_pool = ThreadPoolExecutor(max_workers=self._MAX_WORKERS)

        # Scroll filtering configuration
        self._scroll_debounce_sec = scroll_debounce_sec
        self._scroll_min_distance = scroll_min_distance
        self._scroll_max_frequency = scroll_max_frequency
        self._scroll_session_timeout = scroll_session_timeout

        # state shared with worker
        self._frames: Dict[int, Any] = {}
        self._frame_lock = asyncio.Lock()

        self._history: deque[str] = deque(maxlen=max(0, history_k))
        self._pending_event: Optional[dict] = None
        self._debounce_handle: Optional[asyncio.TimerHandle] = None

        # keyboard activity tracking
        self._key_activity_start: Optional[float] = None
        self._key_activity_timeout: float = (
            keyboard_timeout  # seconds of inactivity to consider session ended
        )
        self._key_screenshots: List[
            str
        ] = []  # track intermediate screenshots for cleanup
        self._key_activity_lock = asyncio.Lock()

        # scroll activity tracking
        self._scroll_last_time: Optional[float] = None
        self._scroll_last_position: Optional[tuple[float, float]] = None
        self._scroll_session_start: Optional[float] = None
        self._scroll_event_count: int = 0
        self._scroll_lock = asyncio.Lock()

        # Inactivity timeout tracking
        self._inactivity_timeout = inactivity_timeout
        self._last_activity_time: Optional[float] = None
        self._inactivity_lock = asyncio.Lock()

        # Window tracking configuration (support for multiple windows)
        self._track_window = track_window  # Keep for backward compatibility
        self._tracked_windows: List[
            dict
        ] = []  # List of {"id": window_id, "region": {...}, "fixed": bool}
        self._current_region_lock = asyncio.Lock()


        # Set target region from coordinates, window tracking, or mouse selection
        if track_window:
            # Will track window dynamically - get initial bounds and window ID
            result = _get_window_by_name(track_window)
            if result is None:
                raise ValueError(f"Window '{track_window}' not found")
            window_id, region = result
            self._tracked_windows.append({
                "id": window_id,
                "region": region,
                "original_size": (region["width"], region["height"])  # Preserve selection size
            })
            if self.debug:
                print(f"Tracking window '{track_window}' (ID: {window_id}): {region}")
        elif target_coordinates:
            # target_coordinates should be (left, top, width, height)
            left, top, width, height = target_coordinates
            region = {"left": left, "top": top, "width": width, "height": height}
            self._tracked_windows.append({
                "id": None,
                "region": region,
                "original_size": None  # Fixed region, never update
            })
            if self.debug:
                print(f"Using target coordinates: {region}")
        else:
            # User selects region(s)/window(s) with mouse
            regions, window_ids = select_region_with_mouse()

            # Convert regions from Quartz coordinates to screen coordinates
            _, _, _, gmax_y = _get_global_bounds()

            # Get screen dimensions to detect fullscreen selections
            import mss
            with mss.mss() as sct:
                screen_bounds = sct.monitors[1]  # Primary monitor
                screen_width = screen_bounds["width"]
                screen_height = screen_bounds["height"]

            for region, window_id in zip(regions, window_ids):
                # Skip zero-sized regions (created by clicks without drag)
                if region["width"] == 0 or region["height"] == 0:
                    if self.debug:
                        print(f"Skipping zero-sized region: {region}")
                    continue

                # Regions from select_region_with_mouse are in Quartz coords
                # Convert to screen coords for consistent comparison
                quartz_top = region["top"]
                height = region["height"]
                screen_top = gmax_y - quartz_top - height

                screen_region = {
                    "left": region["left"],
                    "top": int(screen_top),
                    "width": region["width"],
                    "height": height
                }

                # If this is a fullscreen selection, treat it as a fixed region (no window tracking)
                # This prevents issues with Desktop/Wallpaper windows not being topmost
                is_fullscreen = (
                    region["width"] >= screen_width * 0.95 and
                    region["height"] >= screen_height * 0.95
                )

                effective_window_id = None if is_fullscreen else window_id

                self._tracked_windows.append({
                    "id": effective_window_id,
                    "region": screen_region,
                    "original_size": (region["width"], region["height"]) if effective_window_id else None
                })

                if is_fullscreen:
                    print(f"Using fullscreen region (no window tracking): {screen_region}")
                elif effective_window_id is not None:
                    print(f"Tracking selected window (ID: {effective_window_id}): {screen_region}")
                else:
                    print(f"Using fixed region: {screen_region}")

            print(f"\nTotal windows/regions selected: {len(self._tracked_windows)}")

        # Detect and store high-DPI status
        self._is_high_dpi = self._detect_high_dpi()

        # call parent
        super().__init__()

        # Event loop and handler references (set when worker starts)
        self._loop = None
        self._mouse_handler = None
        self._scroll_handler = None
        self._key_handler = None
        self._mouse_listener = None
        self._key_listener = None
        self._start_listeners_on_main_thread = start_listeners_on_main_thread
        self._listeners_started = False

        # Define listener callbacks that safely schedule events to async loop
        def safe_schedule_event(x: float, y: float, typ: str):
            if self._loop and self._mouse_handler:
                asyncio.run_coroutine_threadsafe(self._mouse_handler(x, y, typ), self._loop)

        def safe_schedule_scroll(x: float, y: float, dx: float, dy: float):
            if self._loop and self._scroll_handler:
                asyncio.run_coroutine_threadsafe(self._scroll_handler(x, y, dx, dy), self._loop)

        def safe_schedule_key(key, typ: str):
            if self._loop and self._key_handler:
                asyncio.run_coroutine_threadsafe(self._key_handler(key, typ), self._loop)

        # Store listener factory functions for deferred initialization
        self._mouse_listener_factory = lambda: mouse.Listener(
            on_click=lambda x, y, btn, prs: safe_schedule_event(x, y, f"click_{btn.name}") if prs else None,
            on_scroll=lambda x, y, dx, dy: safe_schedule_scroll(x, y, dx, dy),
        )
        self._key_listener_factory = lambda: keyboard.Listener(
            on_press=lambda key: safe_schedule_key(key, "press"),
        )

        # Adjust settings for high-DPI displays
        if self._is_high_dpi:
            self._CAPTURE_FPS = 3  # Even lower FPS for high-DPI displays
            self._MEMORY_CLEANUP_INTERVAL = 20  # More frequent cleanup
            if self.debug:
                logging.getLogger("Screen").info(
                    "High-DPI display detected, using conservative settings"
                )

    @staticmethod
    def _mon_for(x: float, y: float, mons: list[dict]) -> Optional[int]:
        for idx, m in enumerate(mons, 1):
            if (
                m["left"] <= x < m["left"] + m["width"]
                and m["top"] <= y < m["top"] + m["height"]
            ):
                return idx
        return None

    @staticmethod
    def _screen_to_mss_coords(screen_region: dict) -> dict:
        """Convert screen coordinates to mss/Quartz coordinates.

        Parameters
        ----------
        screen_region : dict
            {'left': x, 'top': y, 'width': w, 'height': h} with Y=0 at top

        Returns
        -------
        dict
            {'left': x, 'top': y, 'width': w, 'height': h} with Y=0 at bottom (Quartz)

        Note
        ----
        On macOS, mss.grab() expects Quartz coordinates (Y=0 at bottom).
        """
        _, _, _, gmax_y = _get_global_bounds()
        return {
            "left": screen_region["left"],
            "top": int(gmax_y - screen_region["top"] - screen_region["height"]),
            "width": screen_region["width"],
            "height": screen_region["height"]
        }

    async def _update_tracked_regions(self) -> None:
        """
        Update the capture regions for all tracked windows.

        For windows with original_size, we preserve the selection dimensions
        but update the position to follow window movement.
        """
        if self.debug:
            print("Checking window bounds update…")

        async with self._current_region_lock:
            for tracked in self._tracked_windows:
                # Skip manually drawn regions (no window ID)
                if tracked["id"] is None:
                    continue

                # For tracked windows, update position but preserve original dimensions
                new_region = await self._run_in_thread(
                    _get_window_bounds_by_id, tracked["id"]
                )
                if new_region:
                    old_region = tracked["region"]
                    original_width, original_height = tracked.get("original_size", (new_region["width"], new_region["height"]))

                    # Update position but keep original dimensions from selection
                    updated_region = {
                        "left": new_region["left"],
                        "top": new_region["top"],
                        "width": original_width,
                        "height": original_height
                    }
                    tracked["region"] = updated_region

                    # Log if position changed significantly
                    if self.debug and old_region:
                        if (
                            abs(old_region["left"] - updated_region["left"]) > 10
                            or abs(old_region["top"] - updated_region["top"]) > 10
                        ):
                            logging.getLogger("Screen").info(
                                f"Window (ID: {tracked['id']}) moved: {updated_region}"
                            )
                else:
                    if self.debug:
                        logging.getLogger("Screen").warning(
                            f"Tracked window (ID: {tracked['id']}) not found"
                        )

    def _is_point_in_region(self, x: float, y: float, region: dict) -> bool:
        """Check if a point (in global coordinates) is inside a region."""
        return (
            region["left"] <= x < region["left"] + region["width"]
            and region["top"] <= y < region["top"] + region["height"]
        )

    def _get_topmost_window_at_point(self, x: float, y: float) -> Optional[int]:
        """Get the window ID of the topmost window at the given point.

        Parameters:
        - x, y: Screen coordinates (Y=0 at top)

        Returns the CGWindowNumber of the topmost window at (x, y), or None if none found.
        """
        import Quartz

        # Get ALL on-screen windows in front-to-back Z-order
        opts = Quartz.kCGWindowListOptionOnScreenOnly
        wins = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID)

        if not wins:
            return None

        # Convert screen Y to Quartz Y for comparison with window bounds
        _, _, _, gmax_y = _get_global_bounds()
        quartz_y = gmax_y - y

        # Find topmost non-system window at this point
        for win in wins:
            bounds = win.get("kCGWindowBounds", {})
            if not bounds:
                continue

            wx, wy, ww, wh = (
                bounds.get("X", 0),
                bounds.get("Y", 0),
                bounds.get("Width", 0),
                bounds.get("Height", 0),
            )

            # Check if point is within this window
            if wx <= x <= wx + ww and wy <= quartz_y <= wy + wh:
                window_id = win.get("kCGWindowNumber")
                owner = win.get("kCGWindowOwnerName", "Unknown")
                layer = win.get("kCGWindowLayer", 0)

                # Skip system UI elements
                is_menubar = layer == Quartz.CGWindowLevelForKey(Quartz.kCGMainMenuWindowLevelKey)
                is_system = owner in ("Dock", "WindowServer", "Window Server")

                if not is_system and not is_menubar:
                    return window_id

        return None

    def _find_region_for_point(self, x: float, y: float) -> Optional[dict]:
        """Find which tracked window/region contains this point.

        Returns the tracked window dict {"id": ..., "region": ...} or None if not found.

        For tracked windows (not manual regions), this also verifies that the tracked
        window is actually the topmost window at this point.
        """
        for tracked in self._tracked_windows:
            if self._is_point_in_region(x, y, tracked["region"]):
                # If this is a tracked window (has window_id), verify it's topmost
                if tracked["id"] is not None:
                    topmost_id = self._get_topmost_window_at_point(x, y)

                    if topmost_id != tracked["id"]:
                        # Tracked window is not topmost - ignore this interaction
                        if self.debug:
                            logging.getLogger("Screen").info(
                                f"Skipping interaction at ({x:.1f}, {y:.1f}) - tracked window not on top"
                            )
                        continue

                # Point is in region and (if tracked) window is topmost
                return tracked
        return None

    async def _update_activity_time(self) -> None:
        """Update the last activity timestamp."""
        async with self._inactivity_lock:
            self._last_activity_time = time.time()

    async def _run_in_thread(self, func, *args, **kwargs):
        """Run a function in the custom thread pool."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._thread_pool, lambda: func(*args, **kwargs)
        )

    def _detect_high_dpi(self) -> bool:
        """Detect if running on a high-DPI display and adjust settings."""
        try:
            # Check if any monitor has high resolution (likely Retina)
            with mss.mss() as sct:
                for monitor in sct.monitors[1:]:  # Skip monitor 0 (all monitors)
                    if monitor["width"] > 2560 or monitor["height"] > 1600:
                        return True
        except Exception:
            pass
        return False

    def _should_log_scroll(self, x: float, y: float, dx: float, dy: float) -> bool:
        """
        Determine if a scroll event should be logged based on filtering criteria.

        Returns True if the scroll event should be logged, False otherwise.
        """
        current_time = time.time()

        # Check if this is a new scroll session
        if (
            self._scroll_session_start is None
            or current_time - self._scroll_session_start > self._scroll_session_timeout
        ):
            # Start new session
            self._scroll_session_start = current_time
            self._scroll_event_count = 0
            self._scroll_last_position = (x, y)
            self._scroll_last_time = current_time
            return True

        # Check debounce time
        if (
            self._scroll_last_time is not None
            and current_time - self._scroll_last_time < self._scroll_debounce_sec
        ):
            return False

        # Check minimum distance
        if self._scroll_last_position is not None:
            distance = (
                (x - self._scroll_last_position[0]) ** 2
                + (y - self._scroll_last_position[1]) ** 2
            ) ** 0.5
            if distance < self._scroll_min_distance:
                return False

        # Check frequency limit
        self._scroll_event_count += 1
        session_duration = current_time - self._scroll_session_start
        if session_duration > 0:
            frequency = self._scroll_event_count / session_duration
            if frequency > self._scroll_max_frequency:
                return False

        # Update tracking state
        self._scroll_last_position = (x, y)
        self._scroll_last_time = current_time

        return True

    async def _cleanup_key_screenshots(self) -> None:
        """Clean up intermediate keyboard screenshots, keeping only first and last."""
        if len(self._key_screenshots) <= 2:
            return

        # Keep first and last, delete the rest
        to_delete = self._key_screenshots[1:-1]
        self._key_screenshots = [self._key_screenshots[0], self._key_screenshots[-1]]

        for path in to_delete:
            try:
                await self._run_in_thread(os.remove, path)
                if self.debug:
                    logging.getLogger("Screen").info(
                        f"Deleted intermediate screenshot: {path}"
                    )
            except OSError:
                pass  # File might already be deleted

    async def _cleanup_old_screenshots(self) -> None:
        """Delete screenshots older than _MAX_SCREENSHOT_AGE to prevent disk space issues."""
        try:
            current_time = time.time()
            deleted_count = 0

            for filename in await self._run_in_thread(os.listdir, self.screens_dir):
                filepath = os.path.join(self.screens_dir, filename)
                if not filename.endswith(".jpg"):
                    continue

                try:
                    file_age = current_time - await self._run_in_thread(
                        os.path.getmtime, filepath
                    )
                    if file_age > self._MAX_SCREENSHOT_AGE:
                        await self._run_in_thread(os.remove, filepath)
                        deleted_count += 1
                except OSError:
                    pass  # File might have been deleted already

            if deleted_count > 0 and self.debug:
                logging.getLogger("Screen").info(
                    f"Cleaned up {deleted_count} old screenshots"
                )
        except Exception as e:
            if self.debug:
                logging.getLogger("Screen").error(f"Error cleaning up screenshots: {e}")

    # ─────────────────────────────── I/O helpers
    async def _save_frame(
        self,
        frame,
        monitor_rect: dict,
        x,
        y,
        tag: str,
        box_color: str = "red",
        box_width: int = 10,
    ) -> str:
        """
        Save a frame with bounding box and crosshair at the given position.

        Parameters
        ----------
        frame : mss frame object
            The captured frame (physical pixels)
        monitor_rect : dict
            The monitor/region dict with 'width' and 'height' in logical points
        x, y : float
            Mouse coordinates in logical points (relative to monitor)
        tag : str
            Filename tag
        box_color : str
            Color for bounding box and crosshair
        box_width : int
            Width of bounding box outline
        """
        ts = f"{time.time():.5f}"
        path = os.path.join(self.screens_dir, f"{ts}_{tag}.jpg")
        image = Image.frombytes("RGB", (frame.width, frame.height), frame.rgb)
        draw = ImageDraw.Draw(image)

        # Compute actual scale factor from frame vs monitor dimensions
        # This handles any DPI (1.0x, 1.5x, 2.0x, 2.5x, etc.) correctly
        scale_x = frame.width / monitor_rect["width"]
        scale_y = frame.height / monitor_rect["height"]

        # Convert logical point coordinates to physical pixel coordinates
        x_pixel = int(x * scale_x)
        y_pixel = int(y * scale_y)

        # Ensure coordinates are within bounds
        x_pixel = max(0, min(frame.width - 1, x_pixel))
        y_pixel = max(0, min(frame.height - 1, y_pixel))

        # Calculate bounding box with smaller, more precise padding
        # Use average scale for box size to handle non-uniform scaling
        avg_scale = (scale_x + scale_y) / 2.0
        box_size = int(30 * avg_scale)  # 30 logical points
        x1 = max(0, x_pixel - box_size)
        x2 = min(frame.width, x_pixel + box_size)
        y1 = max(0, y_pixel - box_size)
        y2 = min(frame.height, y_pixel + box_size)

        # Draw the bounding box if coordinates are valid
        if x1 < x2 and y1 < y2:
            draw.rectangle([x1, y1, x2, y2], outline=box_color, width=box_width)

        # Draw a crosshair at the exact mouse position
        crosshair_size = int(15 * avg_scale)  # 15 logical points
        crosshair_width = max(2, int(3 * avg_scale))

        # Horizontal line
        h_x1 = max(0, x_pixel - crosshair_size)
        h_x2 = min(frame.width, x_pixel + crosshair_size)
        draw.line(
            [(h_x1, y_pixel), (h_x2, y_pixel)], fill=box_color, width=crosshair_width
        )

        # Vertical line
        v_y1 = max(0, y_pixel - crosshair_size)
        v_y2 = min(frame.height, y_pixel + crosshair_size)
        draw.line(
            [(x_pixel, v_y1), (x_pixel, v_y2)], fill=box_color, width=crosshair_width
        )

        # Save with lower quality to reduce memory usage and disk I/O
        await self._run_in_thread(
            image.save,
            path,
            "JPEG",
            quality=50,  # Reduced to 50 for better performance
            optimize=True,  # Enable optimization
        )

        # Explicitly delete image objects to free memory
        del draw
        del image

        return path

    async def _process_and_emit(
        self,
        before_path: str,
        after_path: str | None,
        action: str | None,
        ev: dict | None,
    ) -> None:
        if "scroll" in action:
            # Include scroll delta information
            scroll_info = ev.get("scroll", (0, 0))
            step = f"scroll({ev['position'][0]:.1f}, {ev['position'][1]:.1f}, dx={scroll_info[0]:.2f}, dy={scroll_info[1]:.2f})"
            await self.update_queue.put(Update(content=step, content_type="input_text"))
        elif "click" in action:
            step = f"{action}({ev['position'][0]:.1f}, {ev['position'][1]:.1f})"
            await self.update_queue.put(Update(content=step, content_type="input_text"))
        else:
            step = f"{action}({ev['text']})"
            await self.update_queue.put(Update(content=step, content_type="input_text"))

    async def stop(self) -> None:
        """Stop the observer and clean up resources."""
        await super().stop()

        # Clean up frame objects
        async with self._frame_lock:
            for frame in self._frames.values():
                if frame is not None:
                    del frame
            self._frames.clear()

        # Force garbage collection
        await self._run_in_thread(gc.collect)

        # Shutdown thread pool
        if hasattr(self, "_thread_pool"):
            self._thread_pool.shutdown(wait=True)

    # ─────────────────────────────── main thread listener methods (macOS-safe)
    def run_listeners_on_main_thread(self):
        """Run pynput listeners on main thread (blocks until stopped).

        On macOS, the keyboard listener calls TIS (Text Input Source) APIs which
        must run on the main dispatch queue. We call run() directly instead of
        start() to avoid creating background threads.

        Mouse listener runs in background thread (doesn't need TIS APIs).
        Keyboard listener runs on main thread (blocks, but macOS-safe).
        """
        if not self._start_listeners_on_main_thread:
            raise RuntimeError("Screen observer not configured for main thread listeners")

        # Create listeners
        self._mouse_listener = self._mouse_listener_factory()
        self._key_listener = self._key_listener_factory()
        self._listeners_started = True

        # Start mouse listener in background thread
        import threading
        mouse_thread = threading.Thread(
            target=self._mouse_listener.run,
            daemon=True,
            name="MouseListener"
        )
        mouse_thread.start()

        # Run keyboard listener on main thread (blocks until stopped)
        try:
            self._key_listener.run()
        except KeyboardInterrupt:
            pass
        finally:
            self._mouse_listener.stop()
            mouse_thread.join(timeout=1)

    def stop_listeners_sync(self):
        """Stop pynput listeners synchronously (safe to call from signal handler)"""
        if self._mouse_listener:
            try:
                self._mouse_listener.stop()
            except:
                pass
        if self._key_listener:
            try:
                self._key_listener.stop()
            except:
                pass

    # ─────────────────────────────── skip guard
    def _skip(self) -> bool:
        return _is_app_visible(self._guard) if self._guard else False

    # ─────────────────────────────── main async worker
    async def _worker(self) -> None:  # overrides base class
        log = logging.getLogger("Screen")
        if self.debug:
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s [Screen] %(message)s",
                datefmt="%H:%M:%S",
            )
        else:
            log.addHandler(logging.NullHandler())
            log.propagate = False

        CAP_FPS = self._CAPTURE_FPS
        PERIOD = self._PERIODIC_SEC
        DEBOUNCE = self._DEBOUNCE_SEC

        loop = asyncio.get_running_loop()
        self._loop = loop  # Set loop reference for listener callbacks

        key_event_count = 0

        # ------------------------------------------------------------------
        # All calls to mss / Quartz are wrapped in `to_thread`
        # ------------------------------------------------------------------
        with mss.mss() as sct:
            # Initialize mons list - will be updated dynamically for tracked windows
            if self._tracked_windows:
                # Use the tracked windows/regions
                if self.debug:
                    log.info(
                        f"Recording {len(self._tracked_windows)} window(s)/region(s)"
                    )
            else:
                # Use all monitors (backward compatibility)
                if self.debug:
                    log.info(f"Recording all monitors")

            # Create and start listeners if not using main thread mode
            if not self._start_listeners_on_main_thread:
                if not self._listeners_started:
                    self._mouse_listener = self._mouse_listener_factory()
                    self._key_listener = self._key_listener_factory()

                    # Brief delay to let AppKit modal state settle after window selection
                    await asyncio.sleep(0.1)

                    self._mouse_listener.start()
                    self._key_listener.start()
                    self._listeners_started = True

            # Wait for listeners to be started (might be on main thread)
            wait_time = 0
            while not self._listeners_started and wait_time < 10:
                await asyncio.sleep(0.1)
                wait_time += 0.1

            if not self._listeners_started:
                log.error("Listeners not started after 10 seconds")
                return

            mouse_listener = self._mouse_listener
            key_listener = self._key_listener

            # ---- nested helper inside the async context ----
            async def flush():
                if self._pending_event is None:
                    return
                if self._skip():
                    self._pending_event = None
                    return

                ev = self._pending_event
                # Clear pending event immediately to avoid blocking next event
                self._pending_event = None

                # Update tracked regions before capturing "after" frame
                await self._update_tracked_regions()

                # Use the region from the event for capturing the "after" frame
                mon_rect = ev["monitor_rect"]
                if mon_rect is None:
                    if self.debug:
                        logging.getLogger("Screen").warning(
                            "Monitor region not available"
                        )
                    return

                # Convert screen coordinates to mss coordinates
                mss_rect = self._screen_to_mss_coords(mon_rect)
                try:
                    aft = await self._run_in_thread(sct.grab, mss_rect)
                except Exception as e:
                    if self.debug:
                        logging.getLogger("Screen").error(
                            f"Failed to capture after frame: {e}"
                        )
                    return

                if "scroll" in ev["type"]:
                    scroll_info = ev.get("scroll", (0, 0))
                    step = f"scroll({ev['position'][0]:.1f}, {ev['position'][1]:.1f}, dx={scroll_info[0]:.2f}, dy={scroll_info[1]:.2f})"
                else:
                    step = f"{ev['type']}({ev['position'][0]:.1f}, {ev['position'][1]:.1f})"

                bef_path = await self._save_frame(
                    ev["before"],
                    ev["monitor_rect"],
                    ev["position"][0],
                    ev["position"][1],
                    f"{step}_before",
                )
                aft_path = await self._save_frame(
                    aft, mon_rect, ev["position"][0], ev["position"][1], f"{step}_after"
                )
                await self._process_and_emit(bef_path, aft_path, ev["type"], ev)

                log.info(f"{ev['type']} captured on window {ev['mon']}")

            # ---- mouse event reception ----
            async def _handle_mouse_event(x: float, y: float, typ: str):
                # Convert pynput coordinates (Cocoa, Y from bottom) to screen coordinates (Y from top)
                _, _, _, gmax_y = await self._run_in_thread(_get_global_bounds)
                screen_y = gmax_y - y

                # Check if point is in any of our tracked windows/regions
                tracked = self._find_region_for_point(x, screen_y)
                if tracked is None:
                    if self.debug:
                        log.info(
                            f"{typ:<6} @({x:7.1f},{screen_y:7.1f}) outside tracked window(s), skipping"
                        )
                    return

                # Update regions for tracked windows
                if tracked["id"] is not None:
                    await self._update_tracked_regions()

                mon = tracked["region"]
                rel_x = x - mon["left"]
                rel_y = screen_y - mon["top"]
                idx = self._tracked_windows.index(tracked) + 1  # 1-indexed for display

                # Grab FRESH "before" frame using current window rect
                # Convert screen coordinates to mss coordinates
                mss_mon = self._screen_to_mss_coords(mon)
                try:
                    bf = await self._run_in_thread(sct.grab, mss_mon)
                except Exception as e:
                    if self.debug:
                        log.error(f"Failed to capture before frame: {e}")
                    return

                log.info(
                    f"{typ:<6} @({rel_x:7.1f},{rel_y:7.1f}) → win={idx}   {'(guarded)' if self._skip() else ''}"
                )
                if self._skip():
                    return

                # Update activity timestamp
                await self._update_activity_time()

                self._pending_event = {
                    "type": typ,
                    "position": (rel_x, rel_y),
                    "mon": idx,
                    "before": bf,
                    "monitor_rect": mon,
                }

                # Process asynchronously - don't wait for completion
                asyncio.create_task(flush())

            # ---- keyboard event reception ----
            async def _handle_key_event(key, typ: str):
                # Get current mouse position to determine active window
                x, y = mouse.Controller().position

                # Convert pynput coordinates (Cocoa, Y from bottom) to screen coordinates (Y from top)
                _, _, _, gmax_y = await self._run_in_thread(_get_global_bounds)
                screen_y = gmax_y - y

                # Check if point is in any of our tracked windows/regions
                tracked = self._find_region_for_point(x, screen_y)
                if tracked is None:
                    if self.debug:
                        log.info(
                            f"Key {typ}: {str(key)} outside tracked window(s), skipping"
                        )
                    return

                # Update regions for tracked windows
                if tracked["id"] is not None:
                    await self._update_tracked_regions()

                mon = tracked["region"]
                rel_x = x - mon["left"]
                rel_y = screen_y - mon["top"]
                idx = self._tracked_windows.index(tracked) + 1  # 1-indexed for display

                # Grab FRESH frame using current window rect
                # Convert screen coordinates to mss coordinates
                mss_mon = self._screen_to_mss_coords(mon)
                try:
                    frame = await self._run_in_thread(sct.grab, mss_mon)
                except Exception as e:
                    if self.debug:
                        log.error(f"Failed to capture keyboard frame: {e}")
                    return

                log.info(f"Key {typ}: {str(key)} on window {idx}")

                # Update activity timestamp
                await self._update_activity_time()

                step = f"key_{typ}({str(key)})"
                await self.update_queue.put(
                    Update(content=step, content_type="input_text")
                )

                async with self._key_activity_lock:
                    current_time = time.time()

                    # Check if this is the start of a new keyboard session
                    if (
                        self._key_activity_start is None
                        or current_time - self._key_activity_start
                        > self._key_activity_timeout
                    ):
                        # Start new session - save first screenshot
                        self._key_activity_start = current_time
                        self._key_screenshots = []

                        # Save frame
                        screenshot_path = await self._save_frame(
                            frame, mon, rel_x, rel_y, f"{step}_first"
                        )
                        self._key_screenshots.append(screenshot_path)
                        log.info(
                            f"Started new keyboard session, saved first screenshot: {screenshot_path}"
                        )
                    else:
                        # Continue existing session - save intermediate screenshot
                        screenshot_path = await self._save_frame(
                            frame, mon, rel_x, rel_y, f"{step}_intermediate"
                        )
                        self._key_screenshots.append(screenshot_path)
                        log.info(
                            f"Continued keyboard session, saved intermediate screenshot: {screenshot_path}"
                        )

                    # Schedule cleanup of previous intermediate screenshots
                    if len(self._key_screenshots) > 2:
                        asyncio.create_task(self._cleanup_key_screenshots())

            # ---- scroll event reception ----
            async def _handle_scroll_event(x: float, y: float, dx: float, dy: float):
                # Convert pynput coordinates (Cocoa, Y from bottom) to screen coordinates (Y from top)
                _, _, _, gmax_y = await self._run_in_thread(_get_global_bounds)
                screen_y = gmax_y - y

                # Apply scroll filtering
                async with self._scroll_lock:
                    if not self._should_log_scroll(x, screen_y, dx, dy):
                        if self.debug:
                            log.info(f"Scroll filtered out: dx={dx:.2f}, dy={dy:.2f}")
                        return

                # Check if point is in any of our tracked windows/regions
                tracked = self._find_region_for_point(x, screen_y)
                if tracked is None:
                    if self.debug:
                        log.info(
                            f"Scroll @({x:7.1f},{screen_y:7.1f}) outside tracked window(s), skipping"
                        )
                    return

                # Update regions for tracked windows
                if tracked["id"] is not None:
                    await self._update_tracked_regions()

                mon = tracked["region"]
                rel_x = x - mon["left"]
                rel_y = screen_y - mon["top"]
                idx = self._tracked_windows.index(tracked) + 1  # 1-indexed for display

                # Grab FRESH "before" frame using current window rect
                # Convert screen coordinates to mss coordinates
                mss_mon = self._screen_to_mss_coords(mon)
                try:
                    bf = await self._run_in_thread(sct.grab, mss_mon)
                except Exception as e:
                    if self.debug:
                        log.error(f"Failed to capture before frame: {e}")
                    return

                # Only log significant scroll movements
                scroll_magnitude = (dx**2 + dy**2) ** 0.5
                if scroll_magnitude < 1.0:  # Very small scrolls
                    if self.debug:
                        log.info(f"Scroll too small: magnitude={scroll_magnitude:.2f}")
                    return

                log.info(
                    f"Scroll @({rel_x:7.1f},{rel_y:7.1f}) dx={dx:.2f} dy={dy:.2f} → win={idx}"
                )

                if self._skip():
                    return

                # Update activity timestamp
                await self._update_activity_time()

                self._pending_event = {
                    "type": "scroll",
                    "position": (rel_x, rel_y),
                    "mon": idx,
                    "before": bf,
                    "scroll": (dx, dy),
                    "monitor_rect": mon,
                }

                # Process event immediately
                await flush()

            # Connect the handler functions to the instance variables
            # so the pynput callbacks can invoke them
            self._mouse_handler = _handle_mouse_event
            self._scroll_handler = _handle_scroll_event
            self._key_handler = _handle_key_event

            # ---- main capture loop ----
            log.info(f"Screen observer started — guarding {self._guard or '∅'}")
            last_periodic = time.time()
            last_screenshot_cleanup = time.time()
            frame_count = 0

            # Initialize last activity time
            async with self._inactivity_lock:
                self._last_activity_time = time.time()

            while self._running:  # flag from base class
                t0 = time.time()

                # Check for inactivity timeout
                async with self._inactivity_lock:
                    if self._last_activity_time is not None:
                        inactive_duration = t0 - self._last_activity_time
                        if inactive_duration >= self._inactivity_timeout:
                            log.info(
                                f"Stopping recording due to {inactive_duration / 60:.1f} minutes of inactivity"
                            )
                            print(f"\n{'=' * 70}")
                            print(
                                f"Recording automatically stopped after {inactive_duration / 60:.1f} minutes of inactivity"
                            )
                            print(f"{'=' * 70}\n")
                            self._running = False
                            break

                # For tracked windows, update regions periodically
                # We capture frames at event time (not periodic)
                if self._tracked_windows:
                    await self._update_tracked_regions()
                    if (
                        self.debug and frame_count % 30 == 0
                    ):  # Log every 30 frames to avoid spam
                        log.info(f"Updated tracked window regions")
                    frame_count += 1

                    # Force garbage collection periodically to prevent memory buildup
                    if frame_count % self._MEMORY_CLEANUP_INTERVAL == 0:
                        await self._run_in_thread(gc.collect)

                # Clean up old screenshots every 5 minutes
                if t0 - last_screenshot_cleanup > 300:  # 300 seconds = 5 minutes
                    await self._cleanup_old_screenshots()
                    last_screenshot_cleanup = t0

                # Check for keyboard session timeout
                current_time = time.time()
                if (
                    self._key_activity_start is not None
                    and current_time - self._key_activity_start
                    > self._key_activity_timeout
                    and len(self._key_screenshots) > 1
                ):
                    # Session ended - rename last screenshot to indicate it's the final one
                    async with self._key_activity_lock:
                        if len(self._key_screenshots) > 1:
                            last_path = self._key_screenshots[-1]
                            final_path = last_path.replace("_intermediate", "_final")
                            try:
                                await self._run_in_thread(
                                    os.rename, last_path, final_path
                                )
                                self._key_screenshots[-1] = final_path
                                log.info(
                                    f"Keyboard session ended, renamed final screenshot: {final_path}"
                                )
                            except OSError:
                                pass
                        self._key_activity_start = None
                        self._key_screenshots = []

                # fps throttle
                dt = time.time() - t0
                await asyncio.sleep(max(0, (1 / CAP_FPS) - dt))

            # Shutdown listeners if started in async worker
            # (main thread listeners are stopped via stop_listeners_sync)
            if not self._start_listeners_on_main_thread:
                mouse_listener.stop()
                key_listener.stop()

            # Final cleanup of any remaining keyboard session
            if self._key_activity_start is not None and len(self._key_screenshots) > 1:
                async with self._key_activity_lock:
                    last_path = self._key_screenshots[-1]
                    final_path = last_path.replace("_intermediate", "_final")
                    try:
                        await self._run_in_thread(os.rename, last_path, final_path)
                        log.info(
                            f"Final keyboard session cleanup, renamed: {final_path}"
                        )
                    except OSError:
                        pass
                    await self._cleanup_key_screenshots()
