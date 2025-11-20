# annotator-gui# annotator-gui - Advanced Chess PGN Annotator

A powerful and versatile tool designed for chess players and analysts.
This project leverages the strength of the *python-chess* library and
any UCI-compatible engine (like Stockfish) to perform deep analysis on
Portable Game Notation (PGN) files. It automatically annotates moves
based on objective engine evaluations.

It features both a robust **Command-Line Interface (CLI)** for batch
processing and a user-friendly **Graphical User Interface (GUI)** for
interactive analysis.

## Table of Contents

-   [Features](https://www.google.com/search?q=%23features)

-   [Prerequisites](https://www.google.com/search?q=%23prerequisites)

-   [Installation](https://www.google.com/search?q=%23installation)

-   [Usage](https://www.google.com/search?q=%23usage)

    -   [Command-Line Interface
        (CLI)](https://www.google.com/search?q=%23command-line-interface-cli)
    -   [Graphical User Interface
        (GUI)](https://www.google.com/search?q=%23graphical-user-interface-gui)

-   [Configuration
    Details](https://www.google.com/search?q=%23configuration-details)

-   [License](https://www.google.com/search?q=%23license)

## ✨ Features

-   **Engine-Powered Analysis:** In-depth move evaluation using any
    Universal Chess Interface (UCI) compatible chess engine.
-   **Automatic Annotation:** Automatically classifies moves (e.g., Best
    Move, Inaccuracy, Mistake, Blunder) by comparing the player\'s move
    score against the engine\'s top score.
-   **PGN Support:** Seamless loading and saving of standard PGN files.
-   **Dual Interface:** Run analysis via CLI for fast, non-interactive
    tasks, or use the GUI for visual file selection and result
    inspection.
-   **Configurable Depth/Time:** Adjust the engine\'s search parameters
    (depth or search time) to balance speed and accuracy.

## 🛠 Prerequisites

To run this application, you will need the following:

1.  **Python 3.8+**
2.  A **UCI-compatible chess engine**.
    [Stockfish](https://stockfishchess.org/download/) is highly
    recommended. Ensure the engine executable path is easily accessible.

## 🚀 Installation

### 1. Clone the Repository

*git clone
\[https://github.com/YourUsername/annotator-gui.git\](https://github.com/YourUsername/annotator-gui.git)*

*cd annotator-gui*

### 2. Install Dependencies

The core functionality relies on the *python-chess* library.

*\# It is highly recommended to use a virtual environment*

*python -m venv venv*

*source venv/bin/activate \# On Windows use: venv\\Scripts\\activate*

*pip install python-chess*

*\# Add specific GUI dependencies here if needed*

### 3. Configure the Engine Path

You **must** specify the absolute path to your UCI engine executable
(e.g., *stockfish.exe* or *stockfish*).

## 💻 Usage

### Command-Line Interface (CLI)

Use the CLI for batch processing or integrating analysis into scripts.

*\# Example 1: Analyze a file with a time limit (5 seconds per move)*

*python annotator_cli.py \\*

* \--input input_games.pgn \\*

* \--output annotated_games_cli.pgn \\*

* \--engine-path /path/to/stockfish \\*

* \--time 5.0*

  ------------------ ------------------------------------------------------------ ---------- ---------
  Argument           Description                                                  Required   Default
  *\--input*         Path to the input PGN file.                                  Yes        \-
  *\--output*        Path where the annotated PGN file will be saved.             Yes        \-
  *\--engine-path*   **Mandatory:** Absolute path to the UCI engine executable.   Yes        \-
  *\--time*          Analysis time in seconds per move (e.g., 5.0).               No         2.0
  *\--depth*         Analysis depth (alternative to *\--time*).                   No         \-
  ------------------ ------------------------------------------------------------ ---------- ---------

### Graphical User Interface (GUI)

The GUI provides an interactive way to select files and visualize the
analysis settings.

*python annotator_gui.py*

1.  Specify the path to your UCI engine.
2.  Load the PGN file.
3.  Set your analysis limits (Time or Depth).
4.  Start the annotation process.
5.  View the results and save the annotated PGN file.

## ⚙️ Configuration Details

The annotation logic uses thresholds to determine the quality of a move
based on the loss of centipawns (cp) compared to the engine\'s best
move.

  ---------------- ----------------------------------------------
  Move Quality     Description (Centipawn Loss vs. Engine Best)
  **Best Move**    Negligible difference (e.g., \< 10 cp loss)
  **Inaccuracy**   Minor deviation (e.g., 10 - 50 cp loss)
  **Mistake**      Significant error (e.g., 50 - 100 cp loss)
  **Blunder**      Game-losing error (e.g., \> 100 cp loss)
  ---------------- ----------------------------------------------

## 📄 License

This project is released under the **MIT License**. See the *LICENSE*
file for more details.
