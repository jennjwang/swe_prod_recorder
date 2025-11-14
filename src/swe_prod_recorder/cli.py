import argparse
import asyncio
import signal
import sys
import threading

# Fix pynput's AXIsProcessTrusted threading issue on macOS
# pynput's MouseListener tries to lazily import AXIsProcessTrusted in a background thread,
# which fails due to pyobjc's lazy import not being thread-safe. We pre-load it here on the
# main thread before any listeners start.
try:
    from ApplicationServices import AXIsProcessTrusted as _AXIsProcessTrusted
    from pynput._util import darwin

    # Force the lazy load now on main thread
    if hasattr(darwin, 'HIServices') and hasattr(darwin.HIServices, 'AXIsProcessTrusted'):
        _ = darwin.HIServices.AXIsProcessTrusted()
    elif hasattr(darwin, 'HIServices'):
        darwin.HIServices.AXIsProcessTrusted = _AXIsProcessTrusted

except Exception as e:
    print(f"Warning: Could not pre-load AXIsProcessTrusted: {e}")

from .gum import gum
from .observers import Screen


def parse_args():
    parser = argparse.ArgumentParser(
        description="SWE Productivity Recorder - Screen activity recorder for software engineer productivity research"
    )
    parser.add_argument(
        "--user-name", "-u", type=str, default="anonymous", help="The user name to use"
    )
    parser.add_argument("--debug", "-d", action="store_true", help="Enable debug mode")

    # Scroll filtering options
    parser.add_argument(
        "--scroll-debounce",
        type=float,
        default=0.5,
        help="Minimum time between scroll events (seconds, default: 0.5)",
    )
    parser.add_argument(
        "--scroll-min-distance",
        type=float,
        default=5.0,
        help="Minimum scroll distance to log (pixels, default: 5.0)",
    )
    parser.add_argument(
        "--scroll-max-frequency",
        type=int,
        default=10,
        help="Maximum scroll events per second (default: 10)",
    )
    parser.add_argument(
        "--scroll-session-timeout",
        type=float,
        default=2.0,
        help="Scroll session timeout (seconds, default: 2.0)",
    )

    # Screenshot storage options
    parser.add_argument(
        "--upload-to-gdrive",
        action="store_true",
        help="Upload screenshots to Google Drive (default: keep local)",
    )
    parser.add_argument(
        "--screenshots-dir",
        type=str,
        default="data/screenshots",
        help="Directory to save screenshots (default: data/screenshots)",
    )

    # Recording mode
    parser.add_argument(
        "--record-all-screens",
        action="store_true",
        help="Record all monitors/screens (no window selection needed)",
    )

    # Inactivity timeout
    parser.add_argument(
        "--inactivity-timeout",
        type=float,
        default=45,
        help="Stop recording after N minutes of inactivity (default: 45)",
    )

    return parser.parse_args()


async def _async_main(args, screen_observer, stop_event):
    """Run async event loop in background thread.

    This manages the database, observer workers, and update processing.
    """
    data_directory = "data"

    try:
        async with gum(args.user_name, screen_observer, data_directory=data_directory):
            # Wait for stop signal
            while not stop_event.is_set():
                await asyncio.sleep(0.1)
    except Exception as e:
        print(f"Error in async loop: {e}")
        import traceback
        traceback.print_exc()


def main():
    """Main entry point.

    Architecture:
    - Main thread: Runs pynput listeners (macOS-safe for TIS APIs)
    - Background thread: Runs asyncio event loop (database, observers, updates)

    This architecture ensures pynput's keyboard listener (which calls macOS TIS
    APIs) runs on the main thread, avoiding dispatch queue assertion failures.
    """
    args = parse_args()
    print(f"User Name: {args.user_name}")

    # Display user warning and instructions
    print("\n" + "=" * 70)
    print("⚠️  BEFORE YOU BEGIN RECORDING")
    print("=" * 70)
    print("\nPlease make sure your workspace is clean and contains only")
    print("study-related materials.")
    print("\nClose all personal tabs, folders, and unrelated applications.")

    if args.record_all_screens:
        print("\nALL monitors/screens will be recorded — everything on screen")
        print("will be captured.")
    else:
        print("\nOnly the window you select will be recorded — activity outside")
        print("it will be ignored.")

    print("\nYou can pause or stop recording at any time using Ctrl + C")
    print("in the terminal.")
    print(
        f"\nRecording will automatically stop after {args.inactivity_timeout} minutes"
    )
    print("of inactivity.")
    print("\nWhen finished, review your recording and delete anything you")
    print("don't want to share.")
    print("\n" + "=" * 70)

    input("\nPress Enter to confirm and start recording...")
    print("\nStarting recording...\n")

    # Create screen observer (window selection happens on main thread)
    screen_observer = Screen(
        screenshots_dir=args.screenshots_dir,
        debug=args.debug,
        scroll_debounce_sec=args.scroll_debounce,
        scroll_min_distance=args.scroll_min_distance,
        scroll_max_frequency=args.scroll_max_frequency,
        scroll_session_timeout=args.scroll_session_timeout,
        upload_to_gdrive=args.upload_to_gdrive,
        record_all_screens=args.record_all_screens,
        inactivity_timeout=args.inactivity_timeout * 60,
        start_listeners_on_main_thread=True,  # macOS-safe mode
    )

    # Coordination between main and background threads
    stop_event = threading.Event()

    # Launch asyncio event loop in background thread
    async_thread = threading.Thread(
        target=lambda: asyncio.run(_async_main(args, screen_observer, stop_event)),
        daemon=True,
        name="AsyncIOThread"
    )
    async_thread.start()

    # Give async loop time to initialize
    import time
    time.sleep(0.5)

    # Set up Ctrl+C handler
    def signal_handler(sig, frame):
        print("\n\nShutting down...")
        stop_event.set()
        screen_observer.stop_listeners_sync()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    try:
        # Run pynput listeners on main thread (blocks until stopped)
        screen_observer.run_listeners_on_main_thread()

        # Clean shutdown
        stop_event.set()
        async_thread.join(timeout=5)

    except KeyboardInterrupt:
        print("\n\nShutting down...")
        stop_event.set()
        screen_observer.stop_listeners_sync()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        stop_event.set()
        screen_observer.stop_listeners_sync()
