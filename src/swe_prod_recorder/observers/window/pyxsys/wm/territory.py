from .window import StickyWindow, WorkspaceWindow
from .workspace import Workspace


class WorkspaceTerritory(object):
    """
    Each call to `wmctrl -d` will return one liner per workspace managed by the
    window manager, each of which contains the following (space-separated values):

    - an integer desktop number,
    - a '*' character for the current desktop, otherwise a '-' character,
    - the desktop geometry as '<width>x<height>' (e.g. '1280x1024'),
    - the viewport position  in the format '<y>,<y>' (e.g. '0,0'),
    - the workarea geometry as 'X,Y and WxH' (e.g. '0,0 1280x998'),
    - the name of the desktop (possibly containing multiple spaces).

    Each call to `wmctrl -l` will return:

    - the window identity as a  hexadecimal  integer,
    - the desktop number (a -1 is used to identify a "sticky" window, i.e.
      shown across all workspaces, which I interpret as 'belonging to the
      workspace territory' i.e. attached to this class).
    - the client machine name.
    - the window title (possibly with multiple spaces in the title).

    This class combines both of these listings as a 'territory' of workspaces,
    and populates the workspaces with windows (stored in them as attributes).

    TODO: add a lookup function to return the workspace with a given desktop number,
          for use in pyxsys.recover.remap⠶WorkspaceTerritoryRemap.transform_to_remap
    """

    def __init__(self, workspaces_str, windows_str):
        self.sticky_windows = []
        self.workspaces = [Workspace(w) for w in workspaces_str.split("\n") if w != ""]
        self.populate_workspaces(windows_str)
        return

    def __repr__(self):
        n_ws = len(self.workspaces)
        n_w = sum([len(ws.windows) for ws in self.workspaces])
        return f"WorkspaceTerritory of {n_ws} workspaces ({n_w} windows)"

    @property
    def workspaces(self):
        return self._workspaces

    @workspaces.setter
    def workspaces(self, workspace_list):
        self._workspaces = workspace_list
        return

    @property
    def windows(self):
        """
        Return a list of sticky windows, followed by workspaces' windows.
        """
        ws_windows = [x for xs in [w.windows for w in self.workspaces] for x in xs]
        return self.sticky_windows + ws_windows

    @property
    def sticky_windows(self):
        return self._sticky_windows

    @sticky_windows.setter
    def sticky_windows(self, val):
        assert type(val) is list, TypeError("Must give a list of windows")
        self._sticky_windows = val
        return

    def get_workspace(self, desktop_number):
        if desktop_number < 0:
            return self.sticky_windows
        for w in self.workspaces:
            if w.number == desktop_number:
                return w
        # This should only be reached in case the above fails to return. Raise error:
        known_ws_nums = [w.number for w in self.workspaces]
        raise ValueError(f"{desktop_number} not a workspace number ({known_ws_nums})")

    def populate_workspaces(self, windows_str):
        sticky_windows = []
        workspace_windows = {}
        for w in windows_str.split("\n"):
            if w == "":
                continue
            if int(w.split()[1]) < 0:
                sticky_windows.append(StickyWindow(w))
            else:
                win = WorkspaceWindow(w)
                if win.desktop_number not in workspace_windows:
                    workspace_windows[win.desktop_number] = []
                workspace_windows[win.desktop_number].append(win)
        for ws_n in workspace_windows:
            window_list = workspace_windows[ws_n]
            self.get_workspace(ws_n).add_windows(window_list)
        self.sticky_windows.extend(sticky_windows)
        for ws_n in workspace_windows:
            target_ws = self.get_workspace(ws_n)
        return

    def xref_x_session(self, x_session):
        """
        Mark all windows with their workspace
        """
        for tw in self.windows:
            tw_id = int(tw.win_id, 16)
            xw_list = [x for xs in x_session.walk() for x in xs]
            for xw in xw_list:
                xw_id = int(xw.win_id, 16)
                if tw_id == xw_id:
                    xw.desktop_number = tw.desktop_number
                    tw.x_win_id = xw.win_id
                    break
        return
