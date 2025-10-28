from .window import ChildWindow, RootWindow, SourceWindow
from ..colours import colour_str


class WindowTree(object):
    def __init__(self):
        """
        Each call to `xwininfo -tree` will return:

        - a 'source' window (first line of output), which is the node that was queried,
          e.g. when called with the `-root` flag, the source will be the root and
          subsequently make the WindowTree a "root tree" (i.e. the source window ID
          matches the root window ID).
        - a 'root' window (first line of indented output, indented by 2 spaces)
        - a 'parent' window (specifically it is the parent of the source window

        This class is to be initialised 'empty' and then completed by adding these
        attributes upon reading the lines of the output. It is possible to check if
        the class has been 'completed' by checking if the value of
        """
        self._source = None
        self._root = None
        self._source_initialised = False
        self._root_initialised = False
        self._root_indent_offset = None
        self._open_path = None
        self._deepest_open_level = None
        self._indent_step_size = 3
        return

    def __repr__(self):
        return f"WindowTree (rooted at {self.root})"

    @property
    def root(self):
        return self._root

    @root.setter
    def root(self, root_node):
        self._root = root_node
        return

    @property
    def source(self):
        return self._source

    @source.setter
    def source(self, source_node):
        self._source = source_node
        return

    @property
    def source_initialised(self):
        return self._source_initialised

    def initialise_source(self, source_line):
        self.source = SourceWindow(source_line)
        assert self.open_path is None, "Expected no open path on uninitialised tree"
        self.open_path = TreePath([self.source])
        self._source_initialised = True
        return

    @property
    def root_initialised(self):
        return self._root_initialised

    def initialise_root(self, root_line, root_line_indent):
        self.root = RootWindow(root_line)
        assert self.root.win_id != "0x0", ValueError("Cannot root a tree at NULL")
        self.root_indent_offset = root_line_indent  # it's 2, but do not hard code this
        self._root_initialised = True
        return

    @property
    def root_indent_offset(self):
        return self._root_indent_offset

    @root_indent_offset.setter
    def root_indent_offset(self, offset):
        self._root_indent_offset = offset
        return

    @property
    def open_path(self):
        return self._open_path

    @open_path.setter
    def open_path(self, path):
        self._open_path = path
        return

    @property
    def deepest_open_level(self):
        return self.open_path.deepest_level

    def retract_to_level(self, target_level):
        level_step = self.deepest_open_level - target_level
        self.open_path.retract_levels(level_step)
        return

    @property
    def indent_step_size(self):
        return self._indent_step_size

    def show_outline(self):
        """
        Print the outline view.
        """
        print(self.outline)
        return

    @property
    def outline(self):
        """
        Return a simple outline view of the tree from its source.
        Unlike the __repr__, just show the hierarchy of windows and IDs,
        as in a file viewer. Printable using the function show_outline.
        """
        dot_dash_outline = self.dot_dash_outline(self.source)
        box_outline = self.parse_dot_dash_outline_to_box_outline(dot_dash_outline)
        return box_outline

    @staticmethod
    def show_numbered_hierarchy(root, indent="0"):
        root_name = root.name
        if root_name is None:
            root_name = "(unnamed)"
        print(" ".join([indent, root_name]))
        for i, c in enumerate(root.children, 0):
            WindowTree.show_numbered_hierarchy(c, "".join((" ", indent, ".", str(i))))
        return

    @staticmethod
    def dot_dash_outline(root, indent=""):
        """
        Print a simple representation of the hierarchy path below a root node, by
        representing the level with dots and the open/closed status of the level
        by an underscore or a dash (respectively).
        """
        outline = []
        root_name = root.name
        if root_name is None:
            root_name = "(unnamed)"
        if root.desktop_number is not None:
            d_num_coloured = colour_str("green", f" ⠶ workspace {root.desktop_number}")
            root_name += d_num_coloured
        outline.append(" ".join([indent, root_name]))
        for i, child in enumerate(root.children, 0):
            if i == len(root.children) - 1:
                joiner = "-"
            else:
                joiner = "_"
            sub_indent = "".join((joiner, indent, ".", str("")))
            outline.append(WindowTree.dot_dash_outline(child, sub_indent))
        return "\n".join(outline)

    @staticmethod
    def parse_dot_dash_outline_to_box_outline(outline_str):
        """
        Parse the dot/dash/underscore notation from print_dot_dash_outline.
        The dash/underscore representation is of the same length as the
        dot representation (i.e. one dot per level), and the series of
        dashes and/or underscores indicates the closed/open status from the
        deepest level up to the uppermost/source level.

        E.g. "-_.." indicates a level 2 node which is closing at level 1 (i.e.
        it is the last child node of the deepest node) and remaining open at
        level 0 (i.e. it is not the last child node of the uppermost/source node).
        """
        outline = []
        outline_dict = {
            "last_child": "┗",
            "mid_child": "┣",
            "h_connect": "━",
            "v_connect": "┃",
            "empty": " ",
        }
        for line in outline_str.split("\n"):
            parsed_line = ""
            dot_dash_str = line.split(" ")[0]
            if dot_dash_str == "":
                node_level = 0
                path_status = []
            else:
                assert "." in dot_dash_str, ValueError("Input is not valid")
                node_level = dot_dash_str.count(".")
                # Extract the dash/underscore path representation, and reverse
                # it so that each position's index is the level it describes
                # e.g. a dot_dash_str of "--_..." becomes a path_str of "_--"
                path_str = dot_dash_str[:node_level][::-1]
                # path_closed_status[level_index] is a bool representing if
                # that level is closed (True means closed, False means open)
                path_closed_status = [p == "-" for p in path_str]
            if node_level > 0:
                connectors = []
                for level_index, is_closed in enumerate(path_closed_status):
                    assert level_index < node_level, "Should never be node's level"
                    if level_index == (node_level - 1) and is_closed:
                        # Closed at leaf level (i.e. "closing")
                        connector = outline_dict["last_child"]
                        connector += outline_dict["h_connect"]
                    elif is_closed:
                        # Closed "before" leaf level (i.e. "already closed")
                        connector = outline_dict["empty"] * 2
                    elif level_index < (node_level - 1) and not is_closed:
                        # Unclosed at intermediate level (i.e. "continuing")
                        connector = outline_dict["v_connect"]
                        connector += outline_dict["empty"]
                    else:
                        # Unclosed at node level (i.e. "remaining open")
                        connector = outline_dict["mid_child"]
                        connector += outline_dict["h_connect"]
                    connectors.append(connector)
                connector_str = "".join(connectors)
                parsed_line += connector_str
            parsed_line += line[len(dot_dash_str) + 1 :]
            outline.append(parsed_line)
        return "\n".join(outline)

    def walk(self, start_node=None):
        """
        Recursive walk from the start_node (by default, the source node) by iterating
        through the 'children' attribute at each step [depth-first not breadth-first].
        """
        if start_node is None:
            start_node = self.source
        yield start_node.children
        for c in start_node.children:
            yield from self.walk(start_node=c)

    def select_id(self, win_id):
        """
        Retrieve the first window with matching `win_id` (obviously should be unique).
        """
        try:
            next(p[0] for p in self.walk() if p if p[0].win_id == win_id)
        except StopIteration:
            return None


class TreePath(list):
    """
    Class listing the levels down a particular branch of a tree, to be used to keep
    track of which 'path' has been 'opened' when adding descendants from a source node.
    For conciseness, store only the ID of each window on the branch, given as a list.
    """

    def __init__(self, window_list):
        assert len(window_list) > 0, "Cannot create TreePath from empty window list"
        self.extend(window_list)
        self._deepest_level = len(self) - 1
        self._deepest_node = self[-1]
        return

    def __repr__(self):
        n_id = len(self)
        tp_r = [f"TreePath of {n_id} windows:"]
        level_repr_indent_size = 2
        indent = " " * level_repr_indent_size
        for win in self:
            if "level" in win.__dict__:
                level_indent = indent * win.level
            else:
                level_indent = ""
            tp_r.append(f"{level_indent}{win}".split("\n")[0])
        return "\n".join(tp_r)

    def outline(self):
        """
        TODO: Print a simple outline view of the path from its source.
        Unlike the __repr__, just show the hierarchy of windows and IDs,
        as in a file viewer.
        """
        outline_view = []
        outline_dict = {
            "last_child": "┗",
            "h_connect": "━",
        }
        level_repr_indent_size = 2
        indent = " " * level_repr_indent_size
        for i, win in enumerate(self):
            if "level" in win.__dict__:
                level_indent = (indent * win.level)[2:]
                level_indent += outline_dict["last_child"] + outline_dict["h_connect"]
            else:
                level_indent = ""
            if win.name is None:
                name_repr = "(unnamed)"
            else:
                name_repr = win.name
            outline_view.append(f"{level_indent}{win.win_id}: {name_repr}")
        return "\n".join(outline_view)

    @property
    def deepest_level(self):
        self._deepest_level = len(self) - 1
        return self._deepest_level

    @property
    def deepest_node(self):
        self._deepest_node = self[-1]
        return self._deepest_node

    @property
    def deepest_parent(self):
        if self.deepest_level < 1:
            return None
        else:
            self._deepest_parent = self[-2]
        return self._deepest_parent

    def deepen(self, child_window_line):
        """
        Extend the open path on the tree by providing a Window object (TODO: store on
        the path by its ID only), while its parent (i.e. prior node on the TreePath)
        window will have the extension window added as a child node.
        """
        extension_window = ChildWindow(child_window_line, self.deepest_level + 1)
        # assert Window in type(extension_window).mro(), "TreePath.deepen takes a Window"
        self.deepest_node.add_children([extension_window])
        self.extend([extension_window])
        return

    def continue_level(self, sibling_window_line):
        """
        Extend the open path on the tree by providing a Window object (TODO: store on
        the path by its ID only), while its parent (i.e. prior node on the TreePath)
        window will have the extension window added as a child node. Unlike deepen,
        this method is used when the level has already been begun (i.e. adding a sibling
        at the level of a previous child rather than a new level of children).
        """
        extension_window = ChildWindow(sibling_window_line, self.deepest_level)
        # assert Window in type(extension_window).mro(), "TreePath.deepen takes a Window"
        assert self.deepest_level > 0, "No parent windows: path has only one window"
        self.deepest_parent.add_children([extension_window])
        self.pop()
        self.extend([extension_window])
        return

    def retract_levels(self, n_levels):
        assert type(n_levels) is int, TypeError(f"{n_levels} not an integer of levels")
        initial_d = self.deepest_level
        assert n_levels < initial_d, f"Cannot retract {n_levels}, max. is {initial_d}"
        for n in range(n_levels):
            self.pop()
        assert initial_d - self.deepest_level == n_levels, f"Did not retract {n_levels}"
        return
