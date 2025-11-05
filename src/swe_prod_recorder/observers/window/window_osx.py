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
        except Exception:
            pass

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
            # Check if clicking the DONE button (top-right area of banner)
            banner_height = 60
            screen_height = self.bounds().size.height
            button_width = 120
            button_height = 40
            button_x = self.bounds().size.width - button_width - 20
            button_y = screen_height - banner_height + 10

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
                # Add window to selection
                _shared_selected_windows.append(window_info.copy())
                print(
                    f"✓ Added window to selection (total: {len(_shared_selected_windows)})"
                )
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
        manual_region = {
            "left": int(global_left),
            "top": int(quartz_top),
            "width": int(width),
            "height": int(height),
        }
        _shared_selected_windows.append(manual_region)
        print(f"Added manual region to selection (total: {len(_shared_selected_windows)})")

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
            if layer >= AppKit.NSFloatingWindowLevel or w < 50 or h < 50:
                continue
            if x <= screen_point.x <= x + w and y <= quartz_y <= y + h:
                window_id = win.get("kCGWindowNumber")
                return {
                    "left": int(x),
                    "top": int(y),
                    "width": int(w),
                    "height": int(h),
                    "window_id": window_id,
                }
        return None

    def drawRect_(self, _):
        # Only draw banner and button on primary monitor
        if self.is_primary:
            # Draw instruction banner at top
            banner_height = 60
            banner_rect = AppKit.NSMakeRect(
                0,
                self.bounds().size.height - banner_height,
                self.bounds().size.width,
                banner_height,
            )
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0, 0.8).set()
            AppKit.NSBezierPath.fillRect_(banner_rect)

            # Draw DONE button (top-right)
            button_width = 120
            button_height = 40
            button_x = self.bounds().size.width - button_width - 20
            button_y = self.bounds().size.height - banner_height + 10

            button_rect = AppKit.NSMakeRect(button_x, button_y, button_width, button_height)
            button_path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                button_rect, 8, 8
            )

            if self.selected_windows:
                # Enabled - green
                AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    0.2, 0.8, 0.3, 0.9
                ).setFill()
            else:
                # Disabled - gray
                AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.5, 0.5).setFill()

            button_path.fill()
            AppKit.NSColor.whiteColor().setStroke()
            button_path.setLineWidth_(2)
            button_path.stroke()

            # Draw DONE text
            done_text = AppKit.NSString.stringWithString_("DONE")
            done_attrs = {
                AppKit.NSFontAttributeName: AppKit.NSFont.boldSystemFontOfSize_(18),
                AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
            }
            done_size = done_text.sizeWithAttributes_(done_attrs)
            done_x = button_x + (button_width - done_size.width) / 2
            done_y = button_y + (button_height - done_size.height) / 2
            done_text.drawAtPoint_withAttributes_(
                AppKit.NSMakePoint(done_x, done_y), done_attrs
            )

            # Draw instruction text
            text_str = "Click windows to toggle selection  •  Click again to deselect"
            text = AppKit.NSString.stringWithString_(text_str)
            attrs = {
                AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(14),
                AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
            }
            text_size = text.sizeWithAttributes_(attrs)
            text_x = 20  # Left-aligned
            text_y = (
                self.bounds().size.height
                - banner_height
                + (banner_height - text_size.height) / 2
            )
            text.drawAtPoint_withAttributes_(AppKit.NSMakePoint(text_x, text_y), attrs)

        # Calculate max_y once for coordinate conversions
        max_y = 0
        for scr in AppKit.NSScreen.screens():
            f = scr.frame()
            max_y = max(max_y, f.origin.y + f.size.height)
        window_frame = self.window().frame()

        # Draw selected windows in green
        for idx, win in enumerate(self.selected_windows, 1):
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

        # Set collection behavior
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
