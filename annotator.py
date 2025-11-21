#!/usr/bin/env python3 -W ignore::DeprecationWarning

__base_author__ = "Ryan Delaney (Original PGN Logic)"
__contributor__ = "Anton Bil (Tkinter GUI Extension)"
__email__ = "anton.bil.167@gmail.com"
__copyright__ = """© Copyright 2016-2018 Ryan Delaney (Base Code).
© Copyright 2023 Anton Bil (Extension). All rights reserved.
This work is distributed WITHOUT ANY WARRANTY whatsoever; without even the
implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See the README file for additional terms and conditions on your use of this
software.

NOTE: This code is based on the original PGN analysis function by Ryan Delaney and
has been extended by Anton Bil to include a Tkinter GUI with sorting and clipboard
functionality.
"""

import argparse
import core
from annotator_gui import AnnotatorGUI

def parse_args():
    """
    Define an argument parser and return the parsed arguments
    """
    parser = argparse.ArgumentParser(
        prog='annotator',
        description='takes chess games in a PGN file and prints '
        'annotations to standard output')
    parser.add_argument("--gui", "-g",
                        help="Start the Graphical User Interface (GUI)",
                        action='store_true')
    parser.add_argument("--filter", "-i",
                        help="Filter games by metadata (e.g., Event:WC;Player:Carlsen)",
                        default="")
    parser.add_argument("--file", "-f",
                        help="input PGN file",
                        required=True,
                        metavar="FILE.pgn")
    parser.add_argument("--engine", "-e",
                        help="analysis engine (default: %(default)s)",
                        default="")
    parser.add_argument("--gametime", "-a",
                        help="how long to spend on each game \
                             (default: %(default)s)",
                        default="1",
                        type=float,
                        metavar="MINUTES")
    parser.add_argument("--threads", "-t",
                        help="threads for use by the engine \
                             (default: %(default)s)",
                        type=int,
                        default=8)
    parser.add_argument("--verbose", "-v", help="increase verbosity",
                        action="count")
    parser.add_argument("--outputfile", "-o",
                        help="Explicit path for the PGN output file. Overrides the default PGN logging logic.",
                        default="")

    return parser.parse_args()


def main():
    """
    Main function

    - Load games from the PGN file
    - Annotate each game, and print the game with the annotations
    """
    args = parse_args()
    core.setup_logging(args)
    gui_mode = args.gui
    outputfile = args.outputfile
    if args.filter:
        filter_str = args.filter
    else:
        filter_str = "None"
    engine_path = args.engine # New
    gametime = args.gametime # New
    threads = args.threads


    pgnfile = args.file
    if gui_mode: # Start the GUI if the flag is set
        # Pass the CLI arguments to the GUI to populate initial values
        app = AnnotatorGUI(filter_str, engine_path, gametime)
        app.mainloop()
    else:
        core.run_annotate(pgnfile, engine_path, gametime, threads, filter_str, outputfile)


if __name__ == "__main__":
    main()

