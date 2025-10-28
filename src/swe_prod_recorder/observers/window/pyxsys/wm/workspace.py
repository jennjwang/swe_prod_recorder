class Workspace(object):
    def __init__(self, ws_str):
        d_num, current, geom, vp_pos, working_geom, name = self.parse_ws_line(ws_str)
        self.number = d_num
        self.name = name
        self.is_current = current
        self.geometry = DesktopGeom(*geom)
        self.working_geometry = WorkareaGeom(*working_geom)
        if vp_pos is None:
            self.viewport_position = None
        else:
            self.viewport_position = ViewportGeom(*vp_pos)
        self.windows = list()
        return

    def __repr__(self):
        current_str = ""
        if self.is_current:
            current_str = " [Active]"
        n_win = len(self.windows)
        if n_win > 0:
            repr_str = f"Workspace of {n_win} windows "
        else:
            repr_str = f"Empty workspace" + (f" " * 8)
        repr_str += f"on desktop {self.number}: '{self.name}' "
        repr_str += f"({self.geometry}){current_str}"
        return repr_str

    @property
    def win_id(self):
        return self._win_id

    @win_id.setter
    def win_id(self, win_id):
        self.check_id(win_id)
        self._win_id = win_id
        return

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, name):
        self._name = name
        return

    @property
    def windows(self):
        return self._windows

    @windows.setter
    def windows(self, window_list):
        self._windows = window_list
        return

    def add_windows(self, window_list):
        assert type(window_list) is list, TypeError("Must supply windows in a list")
        self.windows.extend(window_list)
        return

    @property
    def geometry(self):
        return self._geometry

    @geometry.setter
    def geometry(self, geom):
        self._geometry = geom
        return

    @property
    def working_geometry(self):
        return self._working_geometry

    @working_geometry.setter
    def working_geometry(self, geom):
        self._working_geometry = geom
        return

    @property
    def viewport_position(self):
        return self._viewport_position

    @viewport_position.setter
    def viewport_position(self, pos):
        self._viewport_position = pos
        return

    @staticmethod
    def parse_ws_line(ws_str):
        number = int(ws_str.split()[0])
        assert number > -1, "Desktop number not a positive int (not valid workspace)"
        is_current = ws_str.split()[1] == "*"
        if not is_current:
            assert ws_str.split()[1] == "-", "Unsure of line's current desktop status"
        dg_str = ws_str.split()[3]
        desktop_geom = tuple([int(x) for x in dg_str.split("x")])
        vp_str = ws_str.split()[5]
        if vp_str == "N/A":
            viewport_pos = None
        else:
            viewport_pos = tuple([int(x) for x in vp_str.split(",")])
        wa_xy_str = ws_str.split()[7]
        wa_wh_str = ws_str.split()[8]
        wa_xy = tuple([int(x) for x in wa_xy_str.split(",")])
        wa_wh = tuple([int(x) for x in wa_wh_str.split("x")])
        workarea_geom = wa_xy + wa_wh
        end_str = ws_str[ws_str.find(" WA: ") + 5 :]
        name = end_str[len(" ".join([wa_xy_str, wa_wh_str])) :].lstrip()
        return number, is_current, desktop_geom, viewport_pos, workarea_geom, name


class DesktopGeom(object):
    def __init__(self, width, height):
        self.width = width
        self.height = height
        return

    def __repr__(self):
        return f"{self.width}x{self.height}"

    @property
    def width(self):
        return self._width

    @width.setter
    def width(self, w):
        self._width = w
        return

    @property
    def height(self):
        return self._height

    @height.setter
    def height(self, h):
        self._height = h
        return


class WorkareaGeom(object):
    def __init__(self, width, height, x, y):
        self.width = width
        self.height = height
        self.x = x
        self.y = y
        return

    @property
    def width(self):
        return self._width

    @width.setter
    def width(self, w):
        self._width = w
        return

    @property
    def height(self):
        return self._height

    @height.setter
    def height(self, h):
        self._height = h
        return

    @property
    def x(self):
        return self._x

    @x.setter
    def x(self, x):
        self._x = x
        return

    @property
    def y(self):
        return self._y

    @y.setter
    def y(self, y):
        self._y = y
        return


class ViewportGeom(object):
    def __init__(self, x, y):
        self.x = x
        self.y = y
        return

    @property
    def x(self):
        return self._x

    @x.setter
    def x(self, x):
        self._x = x
        return

    @property
    def y(self):
        return self._y

    @y.setter
    def y(self, y):
        self._y = y
        return
