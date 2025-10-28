from dotenv import load_dotenv

load_dotenv()

import argparse
import asyncio

from .gum import gum
from .gum.observers import Screen


def parse_args():
    parser = argparse.ArgumentParser(
        description="GUM - A Python package with command-line interface"
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

    # Inactivity timeout
    parser.add_argument(
        "--inactivity-timeout",
        type=float,
        default=45,
        help="Stop recording after N minutes of inactivity (default: 45)",
    )

    return parser.parse_args()


async def _main():
    args = parse_args()
    print(f"User Name: {args.user_name}")

    # Display warning message before starting recording
    print("\n" + "=" * 70)
    print("⚠️  BEFORE YOU BEGIN RECORDING")
    print("=" * 70)
    print("\nPlease make sure your workspace is clean and contains only")
    print("study-related materials.")
    print("\nClose all personal tabs, folders, and unrelated applications.")
    print("\nOnly the window you select will be recorded — activity outside")
    print("it will be ignored.")
    print("\nYou can pause or stop recording at any time using Ctrl + C")
    print("in the terminal.")
    print(
        f"\nRecording will automatically stop after {args.inactivity_timeout} minutes"
    )
    print("of inactivity in the selected window.")
    print("\nWhen finished, review your recording and delete anything you")
    print("don't want to share.")
    print("\n" + "=" * 70)

    # Wait for user confirmation
    input("\nPress Enter to confirm and start recording...")
    print("\nStarting recording...\n")

    # Create Screen observer with scroll filtering configuration
    screen_observer = Screen(
        screenshots_dir=args.screenshots_dir,
        debug=args.debug,
        scroll_debounce_sec=args.scroll_debounce,
        scroll_min_distance=args.scroll_min_distance,
        scroll_max_frequency=args.scroll_max_frequency,
        scroll_session_timeout=args.scroll_session_timeout,
        upload_to_gdrive=args.upload_to_gdrive,
        inactivity_timeout=args.inactivity_timeout * 60,  # Convert minutes to seconds
    )

    # Use data directory for database, screenshots go in data/screenshots
    data_directory = "data"

    async with gum(args.user_name, screen_observer, data_directory=data_directory):
        await asyncio.Future()  # run forever (Ctrl-C to stop)


def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
