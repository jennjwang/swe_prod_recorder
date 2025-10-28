from subprocess import run
from shutil import which
from .wm.territory import WorkspaceTerritory


def read_wmctrl_listings():
    """
    Read the workspaces and mapped windows from wmctrl into a single representation.
    """
    assert which("wmctrl") is not None, "wmctrl not found, please install it"
    result_d = run(["wmctrl", "-d"], capture_output=True)
    assert result_d.returncode == 0, f"'wmctrl -d' call failed.\n{result_d.stderr}"
    result_l = run(["wmctrl", "-l"], capture_output=True)
    assert result_l.returncode == 0, f"'wmctrl -l' failed.\n{result_l.stderr}"
    workspaces_str = result_d.stdout.decode()
    windows_str = result_l.stdout.decode()
    territory = WorkspaceTerritory(workspaces_str, windows_str)
    return territory
