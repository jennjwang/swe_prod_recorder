class Window(object):
    def __init__(self, win_id, d_num, client_hostname, win_title):
        self.desktop_number = d_num
        self.win_id = win_id
        self.title = win_title
        self.client_hostname = client_hostname
        return

    def __repr__(self):
        """
        Prints the window desktop number, ID, and title (omits the hostname as this
        is the same for all windows on a system, so not worth viewing repeatedly).
        """
        repr_str = f"Window {self.win_id} on workspace {self.desktop_number}"
        repr_str += f" — ‘{self.title}’"
        return repr_str

    @property
    def desktop_number(self):
        return self._desktop_number

    @desktop_number.setter
    def desktop_number(self, d_num):
        self._desktop_number = d_num
        return

    @property
    def win_id(self):
        return self._win_id

    @win_id.setter
    def win_id(self, win_id):
        self._win_id = win_id
        return

    @property
    def title(self):
        return self._title

    @title.setter
    def title(self, title):
        self._title = title
        return

    @property
    def client_hostname(self):
        return self._geom

    @client_hostname.setter
    def client_hostname(self, hostname):
        self._client_hostname = hostname
        return

    @property
    def simple_repr(self):
        # TODO
        if self.title == "Terminal":
            r = f"@"
        r = f"{self.title}"
        return r

    @property
    def is_terminal(self):
        if self.title.startswith("Terminal @"):
            return True
        else:
            return False

    @property
    def term_win_id(self):
        if self.is_terminal:
            return self.title.split()[1]
        else:
            return None


class WindowDesc(Window):
    def __init__(self, line):
        super(WindowDesc, self).__init__(*self.parse_descline(line))
        return

    @staticmethod
    def parse_descline(line):
        """
        Window ID is given in the middle of a line, after a colon (window type format),
        followed by the window's name (if any).
        """
        win_id = line.split()[0]
        d_num = int(line.split()[1])
        client_hostname = line.split()[2]
        win_title = line[line.find(client_hostname) + len(client_hostname) + 1 :]
        return win_id, d_num, client_hostname, win_title


class WorkspaceWindow(WindowDesc):
    def __init__(self, line):
        super(WorkspaceWindow, self).__init__(line)
        return


class StickyWindow(WindowDesc):
    def __init__(self, line):
        super(StickyWindow, self).__init__(line)
        return
