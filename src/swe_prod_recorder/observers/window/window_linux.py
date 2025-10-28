# src/swe_prod_recorder/observers/window/window_linux.py

import sys

from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QWidget

from .pyxsys.wmctrl import read_wmctrl_listings
from .pyxsys.xwininfo import read_xwin_tree


class WindowSelectionOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self.selected_windows = []
        self.highlighted_window = None

        # Get window data
        self.x_tree = read_xwin_tree()
        self.wm_territory = read_wmctrl_listings()
        self.wm_territory.xref_x_session(self.x_tree)

        self.windows = self._get_selectable_windows()
        print(f"Found {len(self.windows)} selectable windows:")
        for w in self.windows[:5]:  # Print first 5
            print(
                f"  {w['title']}: x={w['left']}, y={w['top']}, w={w['width']}, h={w['height']}"
            )

        # Setup UI
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowState(Qt.WindowFullScreen)
        self.setMouseTracking(True)

    def _get_selectable_windows(self):
        windows = []

        print(f"WM territory has {len(self.wm_territory.windows)} windows")

        for wm_win in self.wm_territory.windows:
            print(
                f"WM window: {wm_win.title}, x_win_id={getattr(wm_win, 'x_win_id', 'NOT SET')}"
            )

            if not hasattr(wm_win, "x_win_id"):
                continue

            x_win = self.x_tree.select_id(wm_win.x_win_id)
            print(f"  Found x_win: {x_win is not None}")

            if x_win:
                print(f"  Has geom: {x_win.geom is not None}")
                if x_win.geom:
                    windows.append(
                        {
                            "id": wm_win.win_id,
                            "title": wm_win.title,
                            "left": int(x_win.geom.abs_x),
                            "top": int(x_win.geom.abs_y),
                            "width": int(x_win.geom.width),
                            "height": int(x_win.geom.height),
                        }
                    )

        return windows

    def mouseMoveEvent(self, event):
        pos = event.pos()
        self.highlighted_window = None

        for win in self.windows:
            rect = QRect(win["left"], win["top"], win["width"], win["height"])
            if rect.contains(pos):
                print(f"Hovering over: {win['title']}")
                self.highlighted_window = win
                break

        self.update()

    def mousePressEvent(self, event):
        print(f"DEBUG: Click at {event.pos()}")
        print(f"DEBUG: Highlighted: {self.highlighted_window}")
        if event.button() == Qt.LeftButton:
            if self.highlighted_window:
                # Handle window selection
                win_id = self.highlighted_window["id"]

                for i, w in enumerate(self.selected_windows):
                    if w["id"] == win_id:
                        self.selected_windows.pop(i)
                        print(f"✗ Deselected (total: {len(self.selected_windows)})")
                        self.update()
                        return

                self.selected_windows.append(self.highlighted_window.copy())
                print(f"✓ Selected (total: {len(self.selected_windows)})")
                self.update()
            # If no window highlighted, click passes through automatically

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.selected_windows = []
            self.close()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self.selected_windows:
                print(f"✓ Confirmed {len(self.selected_windows)} window(s)")
                self.close()

    def paintEvent(self, event):
        painter = QPainter(self)

        # Selected windows - green
        for idx, win in enumerate(self.selected_windows, 1):
            rect = QRect(win["left"], win["top"], win["width"], win["height"])
            painter.fillRect(rect, QColor(50, 200, 75, 80))
            painter.setPen(QPen(QColor(50, 200, 75, 230), 4))
            painter.drawRect(rect)

            painter.setPen(Qt.white)
            painter.setFont(QFont("Arial", 24, QFont.Bold))
            # Draw number badge
            painter.setPen(Qt.white)
            painter.setFont(QFont("Arial", 24, QFont.Bold))
            painter.drawText(rect.left() + 10, rect.top() + 40, str(idx))

        # Highlighted window - blue
        if (
            self.highlighted_window
            and self.highlighted_window not in self.selected_windows
        ):
            win = self.highlighted_window
            rect = QRect(win["left"], win["top"], win["width"], win["height"])
            painter.fillRect(rect, QColor(75, 150, 255, 60))
            painter.setPen(QPen(QColor(75, 150, 255, 230), 3))
            painter.drawRect(rect)

        # Instructions banner
        painter.fillRect(0, 0, self.width(), 60, QColor(0, 0, 0, 200))
        painter.setPen(Qt.white)
        painter.setFont(QFont("Arial", 14))
        painter.drawText(20, 35, "Click windows to select • ESC=cancel • ENTER=confirm")


def select_region_with_mouse():
    app = QApplication.instance() or QApplication(sys.argv)

    overlay = WindowSelectionOverlay()
    overlay.show()
    app.exec_()

    if not overlay.selected_windows:
        raise RuntimeError("Selection cancelled")

    regions = []
    window_ids = []

    for win in overlay.selected_windows:
        regions.append(
            {
                "left": win["left"],
                "top": win["top"],
                "width": win["width"],
                "height": win["height"],
            }
        )
        window_ids.append(win["id"])

    return regions, window_ids
