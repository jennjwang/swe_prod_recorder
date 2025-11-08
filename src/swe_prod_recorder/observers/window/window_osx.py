import objc
import AppKit
import Quartz
from Foundation import NSDate, NSRunLoop

_selected_regions = []  # List of selected regions
_selected_window_ids = []  # List of selected window IDs
_all_overlay_windows = []  # List of all overlay windows (for multi-monitor)
_all_overlay_views = []  # List of all overlay views (for refreshing)
_selection_confirmed = False  # Track if user confirmed selection
_selection_cancelled = False  # Track if user cancelled
_shared_selected_windows = []  # Shared selection list across all overlays


def _virtual_screen_frame():
    """Return a rect that covers every connected display."""
    screens = AppKit.NSScreen.screens()

    if not screens:
        # Fallback to the main screen if enumeration fails
        return AppKit.NSScreen.mainScreen().frame()

    min_x = min(scr.frame().origin.x for scr in screens)
    min_y = min(scr.frame().origin.y for scr in screens)
    max_x = max(scr.frame().origin.x + scr.frame().size.width for scr in screens)
    max_y = max(scr.frame().origin.y + scr.frame().size.height for scr in screens)

    virtual_frame = AppKit.NSMakeRect(min_x, min_y, max_x - min_x, max_y - min_y)
    return virtual_frame


def _max_screen_y():
    """Return the upper bound (Quartz Y) across all displays."""
    screens = AppKit.NSScreen.screens()
    if not screens:
        frame = AppKit.NSScreen.mainScreen().frame()
        return frame.origin.y + frame.size.height

    max_y = 0
    for scr in screens:
        f = scr.frame()
        max_y = max(max_y, f.origin.y + f.size.height)
    return max_y


def _check_multi_monitor_spanning(x: int, y: int, w: int, h: int) -> bool:
    """Check if a region spans multiple monitors.

    Returns True if the region is mostly on one monitor, False if it spans multiple.
    """
    import Quartz

    err, ids, cnt = Quartz.CGGetActiveDisplayList(16, None, None)
    if err != Quartz.kCGErrorSuccess or cnt <= 1:
        return True  # Single monitor or error, no spanning possible

    best_overlap = 0
    region_area = w * h

    for did in ids[:cnt]:
        bounds = Quartz.CGDisplayBounds(did)
        mon_x = int(bounds.origin.x)
        mon_y = int(bounds.origin.y)
        mon_w = int(bounds.size.width)
        mon_h = int(bounds.size.height)

        # Calculate overlap
        overlap_x1 = max(x, mon_x)
        overlap_y1 = max(y, mon_y)
        overlap_x2 = min(x + w, mon_x + mon_w)
        overlap_y2 = min(y + h, mon_y + mon_h)

        if overlap_x2 > overlap_x1 and overlap_y2 > overlap_y1:
            overlap_area = (overlap_x2 - overlap_x1) * (overlap_y2 - overlap_y1)
            best_overlap = max(best_overlap, overlap_area)

    # If less than 90% of the region is on the best monitor, it spans multiple monitors
    return best_overlap >= (region_area * 0.9)


class OverlayWindow(AppKit.NSWindow):
    def canBecomeKeyWindow(self):
        return True

    def canBecomeMainWindow(self):
        return True

    def becomeKeyWindow(self):
        result = objc.super(OverlayWindow, self).becomeKeyWindow()
        try:
            content = self.contentView()
            if content is not None:
                self.makeFirstResponder_(content)
        except Exception:
            pass
        return result

    def constrainFrameRect_toScreen_(self, frame_rect, screen):
        # Allow the overlay to span multiple screens without AppKit shrinking it
        return frame_rect


class SelectionView(AppKit.NSView):
    def initWithPrimary_(self, is_primary):
        self = objc.super(SelectionView, self).initWithFrame_(
            AppKit.NSMakeRect(0, 0, 10000, 10000)
        )
        if self is None:
            return None
        self.start = None
        self.end = None
        self.highlighted_window = None
        self.is_primary = is_primary  # Only primary monitor shows banner/button
        # Use shared selection list across all monitors
        # (selected_windows property will reference global _shared_selected_windows)
        return self

    @property
    def selected_windows(self):
        """Access the shared selection list."""
        global _shared_selected_windows
        return _shared_selected_windows

    def isOpaque(self):
        # Ensure the view is transparent
        return False

    def wantsLayer(self):
        # Use layer-backed view for better multi-monitor support
        return True

    def viewDidMoveToWindow(self):
        objc.super(SelectionView, self).viewDidMoveToWindow()
        try:
            win = self.window()
            if win is not None:
                win.makeFirstResponder_(self)
                # Set up tracking area for mouse movement
                self.updateTrackingAreas()
        except Exception:
            pass

    def updateTrackingAreas(self):
        """Set up tracking area to receive mouse moved events."""
        objc.super(SelectionView, self).updateTrackingAreas()

        # Remove any existing tracking areas
        for area in self.trackingAreas():
            self.removeTrackingArea_(area)

        # Add new tracking area covering the entire view
        tracking_area = AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(),
            AppKit.NSTrackingMouseMoved | AppKit.NSTrackingActiveAlways | AppKit.NSTrackingInVisibleRect,
            self,
            None
        )
        self.addTrackingArea_(tracking_area)

    def acceptsFirstResponder(self):
        return True

    def becomeFirstResponder(self):
        result = objc.super(SelectionView, self).becomeFirstResponder()
        return result

    def acceptsFirstMouse_(self, event):
        return True  # first click acts immediately

    def _close_all_overlays(self):
        """Close all overlay windows across all monitors."""
        global _all_overlay_windows
        for win in _all_overlay_windows:
            try:
                win.orderOut_(None)
                win.close()
            except Exception:
                pass

    def _refresh_all_views(self):
        """Refresh all overlay views to show updated selection."""
        global _all_overlay_views
        for view in _all_overlay_views:
            try:
                view.setNeedsDisplay_(True)
            except Exception:
                pass

    def keyDown_(self, event):
        global _selected_regions, _selected_window_ids, _selection_confirmed, _selection_cancelled
        keyCode = event.keyCode()
        print(f"Key pressed: keyCode={keyCode}")

        # ESC = cancel
        if keyCode == 53:  # kVK_Escape
            print("ESC pressed - cancelling")
            _selected_regions = []
            _selected_window_ids = []
            _selection_cancelled = True
            self._close_all_overlays()
            return

        # Enter/Return = confirm selection
        elif keyCode == 36 or keyCode == 76:  # kVK_Return or kVK_KeypadEnter
            print(
                f"Enter pressed - selected_windows count: {len(self.selected_windows)}"
            )
            if self.selected_windows:
                # Use the selected windows
                _selected_regions = [w.copy() for w in self.selected_windows]
                _selected_window_ids = [
                    w.get("window_id") for w in self.selected_windows
                ]
                # Remove window_id from regions as it's stored separately
                for region in _selected_regions:
                    region.pop("window_id", None)
                    region.pop("_owner", None)  # Remove debug field
                    region.pop("_name", None)  # Remove debug field
                print(f"Confirming selection of {len(self.selected_windows)} window(s)")
                print(
                    f"Setting global _selected_regions to {len(_selected_regions)} items"
                )
                _selection_confirmed = True
                self._close_all_overlays()
            else:
                print("No windows selected. Please click windows to select them first.")
            return

        objc.super(SelectionView, self).keyDown_(event)

    def mouseMoved_(self, event):
        location = event.locationInWindow()
        window_info = self._get_window_at_location(location)
        if window_info != self.highlighted_window:
            self.highlighted_window = window_info
            self.setNeedsDisplay_(True)

    def mouseDown_(self, event):
        """Click adds/removes windows or confirms selection via button"""
        print(f">>> mouseDown_ called! clickCount={event.clickCount()}")
        global _selected_regions, _selected_window_ids, _selection_confirmed, _shared_selected_windows

        location = event.locationInWindow()

        # Only primary monitor shows DONE button
        if self.is_primary:
            # Check if clicking the DONE button (bottom-right area of banner)
            banner_height = 80
            button_width = 160
            button_height = 50
            button_x = self.bounds().size.width - button_width - 30
            button_y = (banner_height - button_height) / 2

            if (
                button_x <= location.x <= button_x + button_width
                and button_y <= location.y <= button_y + button_height
            ):
                # Clicked DONE button
                if self.selected_windows:
                    print(
                        f"✓ DONE button clicked - confirming {len(self.selected_windows)} window(s)"
                    )
                    _selected_regions = [w.copy() for w in self.selected_windows]
                    _selected_window_ids = [
                        w.get("window_id") for w in self.selected_windows
                    ]
                    for region in _selected_regions:
                        region.pop("window_id", None)
                        region.pop("_owner", None)  # Remove debug field
                        region.pop("_name", None)  # Remove debug field
                    _selection_confirmed = True
                    self._close_all_overlays()
                    return

        window_info = self._get_window_at_location(location)
        print(f">>> window_info: {window_info is not None}")

        # Double-click on empty area to confirm selection (backup method)
        if event.clickCount() == 2 and window_info is None and not self.start:
            if self.selected_windows:
                print(
                    f"Double-click detected - confirming selection of {len(self.selected_windows)} window(s)"
                )
                _selected_regions = [w.copy() for w in self.selected_windows]
                _selected_window_ids = [
                    w.get("window_id") for w in self.selected_windows
                ]
                for region in _selected_regions:
                    region.pop("window_id", None)
                    region.pop("_owner", None)  # Remove debug field
                    region.pop("_name", None)  # Remove debug field
                _selection_confirmed = True
                self._close_all_overlays()
                return

        if window_info:
            window_id = window_info.get("window_id")
            # Check if already selected - if so, DESELECT it
            already_selected = False
            for i, w in enumerate(_shared_selected_windows):
                if w.get("window_id") == window_id:
                    _shared_selected_windows.pop(i)
                    print(
                        f"✗ Removed window from selection (total: {len(_shared_selected_windows)})"
                    )
                    already_selected = True
                    break

            if not already_selected and window_id:
                # Validate window size before adding
                win_w = window_info.get("width", 0)
                win_h = window_info.get("height", 0)
                win_x = window_info.get("left", 0)
                win_y = window_info.get("top", 0)
                MIN_WIDTH = 200
                MIN_HEIGHT = 150

                # Get screen dimensions for maximum size validation
                screens = AppKit.NSScreen.screens()
                max_screen_width = max(scr.frame().size.width for scr in screens) if screens else 3840
                max_screen_height = max(scr.frame().size.height for scr in screens) if screens else 2160
                MAX_WIDTH = max_screen_width
                MAX_HEIGHT = max_screen_height

                if win_w < MIN_WIDTH or win_h < MIN_HEIGHT:
                    print(f"⚠️  Window too small ({win_w}x{win_h}). Minimum: {MIN_WIDTH}x{MIN_HEIGHT} pixels")
                elif win_w > MAX_WIDTH or win_h > MAX_HEIGHT:
                    print(f"⚠️  Window too large ({win_w}x{win_h}). Maximum: {int(MAX_WIDTH)}x{int(MAX_HEIGHT)} pixels")
                    print(f"    Hint: This might be a desktop/parent window. Try clicking on specific application windows.")
                elif not _check_multi_monitor_spanning(win_x, win_y, win_w, win_h):
                    print(f"⚠️  Window spans multiple monitors ({win_w}x{win_h})")
                    print(f"    Hint: Select windows that fit within a single monitor to avoid merged screenshots")
                else:
                    # Add window to selection
                    owner = window_info.get("_owner", "")
                    name = window_info.get("_name", "")
                    _shared_selected_windows.append(window_info.copy())
                    print(
                        f"✓ Added window to selection: {win_w}x{win_h} (total: {len(_shared_selected_windows)})"
                    )
                    print(f"  Owner: {owner!r}, Name: {name!r}")
                    print("  → Click DONE button or double-click empty area")

            self.highlighted_window = None
            # Refresh all overlay views to show updated selection
            self._refresh_all_views()
        else:
            # Start manual region drawing
            self.start = location
            self.end = self.start
            self.setNeedsDisplay_(True)

    def mouseDragged_(self, event):
        if self.start:
            self.end = event.locationInWindow()
            self.highlighted_window = None
            self.setNeedsDisplay_(True)

    def mouseUp_(self, event):
        global _shared_selected_windows
        if not self.start:
            return
        self.end = event.locationInWindow()
        self.setNeedsDisplay_(True)

        x0, y0 = self.start.x, self.start.y
        x1, y1 = self.end.x, self.end.y
        left, top = min(x0, x1), min(y0, y1)
        width, height = abs(x1 - x0), abs(y1 - y0)

        window_frame = self.window().frame()
        global_left = window_frame.origin.x + left
        global_bottom = window_frame.origin.y + top
        max_y = getattr(self, "max_y", None)
        if max_y is None:
            max_y = _max_screen_y()
            self.max_y = max_y
        quartz_top = max_y - (global_bottom + height)

        # Add manual region to selection (no window_id for manual regions)
        # Validate minimum and maximum size to avoid capturing tiny or excessively large regions
        MIN_WIDTH = 200
        MIN_HEIGHT = 150

        # Get screen dimensions for maximum size validation
        screens = AppKit.NSScreen.screens()
        max_screen_width = max(scr.frame().size.width for scr in screens) if screens else 3840
        max_screen_height = max(scr.frame().size.height for scr in screens) if screens else 2160
        MAX_WIDTH = max_screen_width
        MAX_HEIGHT = max_screen_height

        if width < MIN_WIDTH or height < MIN_HEIGHT:
            print(f"⚠️  Region too small ({int(width)}x{int(height)}). Minimum: {MIN_WIDTH}x{MIN_HEIGHT} pixels")
            self.start = None
            self.end = None
            self.setNeedsDisplay_(True)
            return

        if width > MAX_WIDTH or height > MAX_HEIGHT:
            print(f"⚠️  Region too large ({int(width)}x{int(height)}). Maximum: {int(MAX_WIDTH)}x{int(MAX_HEIGHT)} pixels")
            print(f"    Hint: Select individual application windows, not the entire desktop")
            self.start = None
            self.end = None
            self.setNeedsDisplay_(True)
            return

        # Check if region spans multiple monitors
        if not _check_multi_monitor_spanning(int(global_left), int(quartz_top), int(width), int(height)):
            print(f"⚠️  Region spans multiple monitors ({int(width)}x{int(height)})")
            print(f"    Hint: Draw regions within a single monitor to avoid merged screenshots")
            self.start = None
            self.end = None
            self.setNeedsDisplay_(True)
            return

        manual_region = {
            "left": int(global_left),
            "top": int(quartz_top),
            "width": int(width),
            "height": int(height),
        }
        _shared_selected_windows.append(manual_region)
        print(f"✓ Added manual region to selection: {int(width)}x{int(height)} (total: {len(_shared_selected_windows)})")

        # Reset drag state
        self.start = None
        self.end = None
        # Refresh all overlay views
        self._refresh_all_views()

    def _get_window_at_location(self, location):
        window_frame = self.window().frame()
        screen_point = AppKit.NSMakePoint(
            window_frame.origin.x + location.x, window_frame.origin.y + location.y
        )

        window_list = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        )
        if not window_list:
            return None

        # Compute Quartz Y-flip
        max_y = 0
        for scr in AppKit.NSScreen.screens():
            f = scr.frame()
            max_y = max(max_y, f.origin.y + f.size.height)
        quartz_y = max_y - screen_point.y

        # Get screen dimensions for size validation
        screens = AppKit.NSScreen.screens()
        max_screen_width = max(scr.frame().size.width for scr in screens) if screens else 3840
        max_screen_height = max(scr.frame().size.height for scr in screens) if screens else 2160

        # Find all windows that contain the click point
        # Prefer the largest window to avoid selecting small sub-components
        matching_windows = []
        for win in window_list:
            bounds = win.get("kCGWindowBounds", {})
            if not bounds:
                continue
            x, y, w, h = (
                bounds.get("X", 0),
                bounds.get("Y", 0),
                bounds.get("Width", 0),
                bounds.get("Height", 0),
            )
            layer = win.get("kCGWindowLayer", 0)
            owner_name = win.get("kCGWindowOwnerName", "")
            window_name = win.get("kCGWindowName", "")

            # Filter out system/desktop windows by owner
            excluded_owners = {"Dock", "WindowServer", "Window Manager", "Desktop", "Wallpaper"}
            if owner_name in excluded_owners:
                continue

            # Filter out windows with names suggesting desktop/workspace
            if window_name and any(word in window_name.lower() for word in ["desktop", "wallpaper", "workspace"]):
                continue

            # Filter out floating windows, very small windows (min 200x150), and excessively large windows
            if layer >= AppKit.NSFloatingWindowLevel or w < 200 or h < 150:
                continue

            # Filter out screen-sized windows ONLY if they appear to be desktop/parent containers
            # Allow legitimate full-screen app windows (Chrome, VS Code, etc.)
            if w >= max_screen_width or h >= max_screen_height:
                # Allow if it has a legitimate app owner (not empty or generic)
                if not owner_name or owner_name in {"Window Server", "Window Manager", "Finder"}:
                    # Likely a desktop container - reject it
                    continue
                # Otherwise allow (it's probably a real app in full-screen mode)
            # Filter out windows that span multiple monitors
            if not _check_multi_monitor_spanning(x, y, w, h):
                continue
            if x <= screen_point.x <= x + w and y <= quartz_y <= y + h:
                window_id = win.get("kCGWindowNumber")
                matching_windows.append({
                    "left": int(x),
                    "top": int(y),
                    "width": int(w),
                    "height": int(h),
                    "window_id": window_id,
                    "area": w * h,
                    "_owner": owner_name,  # For debugging
                    "_name": window_name,  # For debugging
                })

        # Return the largest matching window
        if matching_windows:
            largest = max(matching_windows, key=lambda w: w["area"])
            largest.pop("area")  # Remove helper field
            # Keep _owner and _name for debugging (will be removed before capture)
            return largest
        return None

    def drawRect_(self, _):
        # Only draw banner and button on primary monitor
        if self.is_primary:
            # Draw instruction banner at BOTTOM (more visible than top)
            banner_height = 80
            banner_rect = AppKit.NSMakeRect(
                0,
                0,  # Bottom of screen
                self.bounds().size.width,
                banner_height,
            )
            # More opaque background for better visibility
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0, 0.95).set()
            AppKit.NSBezierPath.fillRect_(banner_rect)

            # Add a bright border at top of banner for extra visibility
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.3).set()
            top_border = AppKit.NSMakeRect(0, banner_height - 2, self.bounds().size.width, 2)
            AppKit.NSBezierPath.fillRect_(top_border)

            # Draw DONE button (center-right of banner for visibility)
            button_width = 160
            button_height = 50
            button_x = self.bounds().size.width - button_width - 30
            button_y = (banner_height - button_height) / 2

            button_rect = AppKit.NSMakeRect(button_x, button_y, button_width, button_height)
            button_path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                button_rect, 8, 8
            )

            if self.selected_windows:
                # Enabled - bright green
                AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    0.0, 0.9, 0.2, 1.0
                ).setFill()
            else:
                # Disabled - more visible gray
                AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.3, 0.8).setFill()

            button_path.fill()

            # Bright white outline for visibility
            AppKit.NSColor.whiteColor().setStroke()
            button_path.setLineWidth_(3)
            button_path.stroke()

            # Draw DONE text - larger and clearer
            if self.selected_windows:
                done_text_str = "✓ DONE"
            else:
                done_text_str = "DONE"
            done_text = AppKit.NSString.stringWithString_(done_text_str)
            done_attrs = {
                AppKit.NSFontAttributeName: AppKit.NSFont.boldSystemFontOfSize_(22),
                AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
            }
            done_size = done_text.sizeWithAttributes_(done_attrs)
            done_x = button_x + (button_width - done_size.width) / 2
            done_y = button_y + (button_height - done_size.height) / 2
            done_text.drawAtPoint_withAttributes_(
                AppKit.NSMakePoint(done_x, done_y), done_attrs
            )

            # Draw instruction text - larger and clearer
            if self.selected_windows:
                text_str = f"✓ {len(self.selected_windows)} window(s) selected  •  Click to deselect  •  Press ENTER or click DONE"
            else:
                text_str = "Click windows to select  •  Double-click empty area to confirm  •  ESC to cancel"
            text = AppKit.NSString.stringWithString_(text_str)
            attrs = {
                AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(16),
                AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
            }
            text_size = text.sizeWithAttributes_(attrs)
            text_x = 30  # Left-aligned with padding
            text_y = (banner_height - text_size.height) / 2
            text.drawAtPoint_withAttributes_(AppKit.NSMakePoint(text_x, text_y), attrs)

        # Calculate max_y once for coordinate conversions
        max_y = 0
        for scr in AppKit.NSScreen.screens():
            f = scr.frame()
            max_y = max(max_y, f.origin.y + f.size.height)
        window_frame = self.window().frame()

        # Draw selected windows in green - only if they're currently visible on this Space
        for idx, win in enumerate(self.selected_windows, 1):
            # Check if this window is currently on-screen (on the active Space)
            win_id = win.get("window_id")
            if win_id is not None:
                # Query if the window is currently visible on this Space
                window_list = Quartz.CGWindowListCopyWindowInfo(
                    Quartz.kCGWindowListOptionOnScreenOnly,
                    Quartz.kCGNullWindowID,
                )
                window_visible = False
                if window_list:
                    for w in window_list:
                        if w.get("kCGWindowNumber") == win_id:
                            # Window is on-screen, check if it's on the active Space
                            is_onscreen = w.get("kCGWindowIsOnscreen", False)
                            if is_onscreen:
                                window_visible = True
                            break

                # Skip drawing if window is not visible on current Space
                if not window_visible:
                    continue

            view_x = win["left"] - window_frame.origin.x
            view_y = (max_y - win["top"] - win["height"]) - window_frame.origin.y
            rect = AppKit.NSMakeRect(view_x, view_y, win["width"], win["height"])

            path = AppKit.NSBezierPath.bezierPathWithRect_(rect)
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.2, 0.8, 0.3, 0.3
            ).setFill()
            path.fill()
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.2, 0.8, 0.3, 0.9
            ).setStroke()
            path.setLineWidth_(4.0)
            path.stroke()

            # Draw number badge
            badge_text_str = str(idx)
            badge_text = AppKit.NSString.stringWithString_(badge_text_str)
            badge_attrs = {
                AppKit.NSFontAttributeName: AppKit.NSFont.boldSystemFontOfSize_(24),
                AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
            }
            badge_size = badge_text.sizeWithAttributes_(badge_attrs)
            badge_x = view_x + 10
            badge_y = view_y + win["height"] - badge_size.height - 10

            # Draw badge background circle
            badge_radius = 20
            badge_circle = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                AppKit.NSMakeRect(
                    badge_x - 5, badge_y - 5, badge_radius * 2, badge_radius * 2
                )
            )
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.2, 0.8, 0.3, 0.9
            ).setFill()
            badge_circle.fill()

            badge_text.drawAtPoint_withAttributes_(
                AppKit.NSMakePoint(badge_x + 5, badge_y), badge_attrs
            )

        # Draw highlighted window in blue (only if not dragging)
        if self.highlighted_window and not self.start:
            win = self.highlighted_window
            view_x = win["left"] - window_frame.origin.x
            view_y = (max_y - win["top"] - win["height"]) - window_frame.origin.y
            rect = AppKit.NSMakeRect(view_x, view_y, win["width"], win["height"])

            path = AppKit.NSBezierPath.bezierPathWithRect_(rect)
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.3, 0.6, 1.0, 0.25
            ).setFill()
            path.fill()
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                0.3, 0.6, 1.0, 0.9
            ).setStroke()
            path.setLineWidth_(3.0)
            path.stroke()

        # Draw manual region being dragged in red
        elif self.start and self.end:
            rect = AppKit.NSMakeRect(
                min(self.start.x, self.end.x),
                min(self.start.y, self.end.y),
                abs(self.end.x - self.start.x),
                abs(self.end.y - self.start.y),
            )
            path = AppKit.NSBezierPath.bezierPathWithRect_(rect)
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                1, 0, 0, 0.3
            ).setFill()
            path.fill()
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                1, 0, 0, 0.9
            ).setStroke()
            path.setLineWidth_(2.0)
            path.stroke()


def select_region_with_mouse() -> tuple[list[dict], list[int | None]]:
    """Modal overlay for selecting multiple windows or dragging rectangles.

    Returns
    -------
    tuple[list[dict], list[int | None]]
        A tuple of (list of region_dicts, list of window_ids). For windows that were selected,
        window_id will be the CGWindowNumber for tracking. For manual rectangles, window_id will be None.
    """
    global _selected_regions, _selected_window_ids, _all_overlay_windows, _all_overlay_views
    global _selection_confirmed, _selection_cancelled, _shared_selected_windows

    # Reset all global state
    _selected_regions = []
    _selected_window_ids = []
    _all_overlay_windows = []
    _all_overlay_views = []
    _selection_confirmed = False
    _selection_cancelled = False
    _shared_selected_windows = []

    app = AppKit.NSApplication.sharedApplication()
    max_y = _max_screen_y()

    # Check if displays have separate spaces (Mission Control setting)
    separate_spaces = AppKit.NSScreen.screensHaveSeparateSpaces()
    screens = AppKit.NSScreen.screens()

    print(f"\n📐 OVERLAY WINDOW CREATION:")
    print(f"   Displays have separate Spaces: {separate_spaces}")
    if separate_spaces:
        print(f"   ✓ Creating separate overlay for each of {len(screens)} monitor(s)")
    else:
        print(f"   ℹ️  Creating separate overlays even though Spaces aren't separate")
        print(f"   ℹ️  This approach works with both settings!")

    # Create one overlay window per monitor
    for i, screen in enumerate(screens):
        screen_frame = screen.frame()
        is_primary = (i == 0)  # First screen is primary (shows banner/button)

        print(f"   Creating overlay for Monitor {i+1}: "
              f"x={screen_frame.origin.x}, y={screen_frame.origin.y}, "
              f"width={screen_frame.size.width}, height={screen_frame.size.height}")

        # Create overlay window for this screen
        window = OverlayWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            screen_frame,
            AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        window.setOpaque_(False)
        window.setHasShadow_(False)
        window.setReleasedWhenClosed_(True)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        window.setLevel_(AppKit.NSFloatingWindowLevel)
        window.setIgnoresMouseEvents_(False)
        window.setAcceptsMouseMovedEvents_(True)
        window.setHidesOnDeactivate_(False)

        # Set collection behavior - appear on all monitors, but filter drawing by visibility
        try:
            behavior = (AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
                       AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary |
                       AppKit.NSWindowCollectionBehaviorStationary)
            window.setCollectionBehavior_(behavior)
        except Exception:
            pass

        # Create view for this screen
        view = SelectionView.alloc().initWithPrimary_(is_primary)
        view.setFrame_(window.contentView().bounds())
        view.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        view.max_y = max_y
        window.setContentView_(view)
        window.setFrame_display_(screen_frame, False)

        # Store window and view
        _all_overlay_windows.append(window)
        _all_overlay_views.append(view)

    # Show all windows
    for window in _all_overlay_windows:
        window.makeKeyAndOrderFront_(None)
        window.orderFrontRegardless()

    app.activateIgnoringOtherApps_(True)

    # Make the first window key
    if _all_overlay_windows:
        _all_overlay_windows[0].makeKeyWindow()
        _all_overlay_windows[0].makeFirstResponder_(_all_overlay_views[0])
        _all_overlay_windows[0].setInitialFirstResponder_(_all_overlay_views[0])

    # Give the window system time to process
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))

    print("\n" + "=" * 70)
    print("MULTI-MONITOR WINDOW SELECTION")
    print("=" * 70)
    print("1. Click on windows to SELECT them (they turn GREEN)")
    print("2. Click selected windows again to DESELECT them")
    print("3. Click the green DONE button (on primary monitor) to confirm")
    print("4. Press ENTER to confirm or ESC to cancel")
    print("=" * 70 + "\n")

    AppKit.NSCursor.crosshairCursor().push()
    try:
        # Run custom event loop until user confirms or cancels
        while not _selection_confirmed and not _selection_cancelled:
            # Process events for a short time
            date = NSDate.dateWithTimeIntervalSinceNow_(0.1)
            event = app.nextEventMatchingMask_untilDate_inMode_dequeue_(
                AppKit.NSEventMaskAny,
                date,
                AppKit.NSDefaultRunLoopMode,
                True
            )
            if event:
                app.sendEvent_(event)
                app.updateWindows()

        print(f"Selection completed: confirmed={_selection_confirmed}, cancelled={_selection_cancelled}")

    finally:
        # Always restore cursor
        try:
            AppKit.NSCursor.pop()
        except Exception:
            AppKit.NSCursor.arrowCursor().set()

        # Close all overlay windows
        for window in _all_overlay_windows:
            try:
                window.orderOut_(None)
                window.close()
            except Exception:
                pass

        app.updateWindows()

        # Give AppKit a tick to process the close
        NSRunLoop.currentRunLoop().runUntilDate_(
            NSDate.dateWithTimeIntervalSinceNow_(0.05)
        )

    # Debug: print selected regions
    print(f"Selected regions count: {len(_selected_regions)}")

    # Check if user cancelled
    if _selection_cancelled or not _selection_confirmed:
        raise RuntimeError("Selection cancelled")

    print(f"Returning {len(_selected_regions)} selected region(s)")
    return _selected_regions, _selected_window_ids
