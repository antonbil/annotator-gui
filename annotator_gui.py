import sys
import os
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
from concurrent.futures import ThreadPoolExecutor
from typing import Iterator, List, Tuple, Dict, Any
import io
from collections import defaultdict
import argparse
import json
import logging
import chess
import chess.pgn
import chess.engine
import chess.variant
import json
from pathlib import Path
from core import logger, run_annotate, extract_filename_from_inputfile, _load_config, pgn_text_iterator
from statsview import PGNStatsView
# --- LOGGING AND STDOUT REDIRECTION CLASS ---

class ConsoleRedirect(logging.Handler):
    """
    A custom logging handler and stdout 'file-like' object that redirects all
    output to a Tkinter Text widget in a thread-safe manner.
    """
    def __init__(self, text_widget: tk.Text):
        super().__init__()
        self.text_widget = text_widget
        self.queue = []
        self.running = True
        # Formatter for log messages
        self.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))

    def emit(self, record):
        """Called by the logging module."""
        # Format the message using the logging formatter
        msg = self.format(record)
        self.write(msg + '\n')

    def write(self, s):
        """Called by print() (via sys.stdout)."""
        # Add message to the queue. We do NOT update the widget directly.
        self.queue.append(s)

        # Schedule the GUI update (thread-safe)
        self.text_widget.after(0, self.process_queue)

    def flush(self):
        """Required for file-like objects, but does nothing here."""
        pass

    def process_queue(self):
        """Processes the message queue in the main Tkinter thread."""
        while self.queue:
            message = self.queue.pop(0)
            self.text_widget.insert(tk.END, message)
            self.text_widget.see(tk.END) # Auto-scrolls to the bottom

def analyze_pgn_stats(input_file_path: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]] | None:
    """
    Reads the PGN file and calculates statistics per Site and Event.

    Returns the processed statistics as two dictionaries:
    (stats_site, stats_event).
    """
    stats_site = defaultdict(lambda: {'count': 0, 'total_elo': 0, 'player_count': 0})
    stats_event = defaultdict(lambda: {'count': 0, 'total_elo': 0, 'player_count': 0})

    # validate a file path
    if not input_file_path or not os.path.exists(input_file_path):
        logger.error(f"Error: Input file not found or not specified: {input_file_path}")
        return None

    try:
        # File handling: Using a context manager for proper closing is best practice
        logger.info(f"Starting PGN analysis of: {os.path.basename(input_file_path)}")

        all_games = []


        game_counter = 0
        for item in pgn_text_iterator(input_file_path):
            pgn_io = io.StringIO(item.strip())
            try:
                game = chess.pgn.read_game(pgn_io)
            except Exception as e:
                print(e)
                print(item)
                continue

            game_data = {}

            game_data["White"]= game.headers.get("White","")
            game_data["WhiteElo"] = game.headers.get("WhiteElo", "")
            game_data["BlackElo"] = game.headers.get("BlackElo", "")
            game_data["Black"]= game.headers.get("Black","")
            game_data["Result"]= game.headers.get("Result","")
            game_data["Date"]= game.headers.get("Date","")
            game_data["Site"] = game.headers.get("Site", "")
            game_data["Event"] = game.headers.get("Event", "")
            all_games.append(game_data)

            game_counter += 1

            # 1. Retrieve Headers
            site = game.headers.get("Site", "Onbekende Site")
            event = game.headers.get("Event", "Onbekend Event")

            white_elo_str = game.headers.get("WhiteElo", "0")
            black_elo_str = game.headers.get("BlackElo", "0")

            # 2. Calculate Elo and Player Count
            current_game_total_elo = 0
            current_game_player_count = 0

            try:
                white_elo = int(white_elo_str)
                current_game_total_elo += white_elo
                current_game_player_count += 1
            except ValueError:
                pass # Ignore if Elo is invalid

            try:
                black_elo = int(black_elo_str)
                current_game_total_elo += black_elo
                current_game_player_count += 1
            except ValueError:
                pass

            # If there is at least one valid rating, update the statistics
            if current_game_player_count > 0:
                stats_site[site]['count'] += 1
                stats_site[site]['total_elo'] += current_game_total_elo
                stats_site[site]['player_count'] += current_game_player_count

                stats_event[event]['count'] += 1
                stats_event[event]['total_elo'] += current_game_total_elo
                stats_event[event]['player_count'] += current_game_player_count

        logger.info(f"Total {game_counter} games read.")

        # 3. Format the results and return them

        # Function to convert the raw data to the Treeview structure
        def format_stats(raw_stats: Dict[str, Any]) -> List[Dict[str, Any]]:
            formatted = []
            for name, data in raw_stats.items():
                # Calculate Average Elo
                avg_elo = data['total_elo'] / data['player_count'] if data['player_count'] > 0 else 0
                formatted.append({
                    "Naam": name,
                    "Count": data['count'],
                    "AvgElo": round(avg_elo)
                })
            return formatted

        return format_stats(stats_site), format_stats(stats_event), all_games

    except Exception as e:
        logger.error(f"Unexpected error during PGN analysis: {e}")
        # In case of errors, return an empty set to prevent the GUI from breaking
        return None

# ----------------------------------------------------------------------
# TKINTER GUI CLASS
# ----------------------------------------------------------------------

class AnnotatorGUI(tk.Tk):
    def __init__(self, initial_filter, initial_engine_path, initial_gametime):
        super().__init__()
        self.title("Annotator Configuration")
        self.geometry("800x800")

        # Ensure a single-threaded executor to prevent two analyses from running simultaneously
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.analysis_future = None
        self.original_stdout = sys.stdout
        self.console_handler = None

        ## 1. Read configuration data
        config_data = _load_config()

        # 2. Assignment of Engine Mappings and Directories

        # The PGN directory is now composed based on the suffix in the JSON
        pgn_suffix = config_data.get("default_pgn_dir_suffix", "Schaken")
        self.default_pgn_dir = os.path.join(os.path.expanduser("~"), pgn_suffix)
        print(f"Default PGN Directory: {self.default_pgn_dir}")

        # The engine options are loaded from the JSON
        # The JSON structure (list of dicts) is converted to the Python structure (list of tuples)
        json_engine_options: List[Dict[str, str]] = config_data.get("engine_options", [])
        self.engine_options: List[Tuple[str, str]] = [
            (item.get("display_name", ""), item.get("path", ""))
            for item in json_engine_options
        ]

        print("\nLoaded Engine Options:")
        for name, path in self.engine_options:
            print(f"- {name}: {path}")

        # --- The rest of the GUI initialization would go here ---

        # For demonstration, a simple label
        #tk.Label(self, text="Configuratie succesvol geladen!", font=("Arial", 16)).pack(pady=50)

        # 2. Map for quick lookup
        self.engine_map: Dict[str, str] = {name: path for name, path in self.engine_options}

        # 3. Only the display names for the Combobox
        self.default_engine_display_names: List[str] = [name for name, path in self.engine_options]

        # Initialize variables
        initial_inputfile = ""
        self.default_filters = ["None", "Interesting", "Result:1-0", "Site:Wijk;Result:1-0,0-1;Title:GM", "Player:Carlsen;TimeControl:600+5"]

        self.inputfile_var = tk.StringVar(value=initial_inputfile)
        self.pgn_var = tk.StringVar()
        self.filter_var = tk.StringVar(value=initial_filter)
        self.gametime_var = tk.StringVar(value=str(initial_gametime))
        self._pgn_manually_set = False
        self.update_pgn_path(initial_setup=True)

        # 4. Engine State Variables
        # a) engine_var tracks the DISPLAY NAME of the Combobox
        initial_display_name = self.default_engine_display_names[0] if self.default_engine_display_names else ""
        self.engine_var = tk.StringVar(value=initial_display_name)

        # b) _engine_path_var tracks the TECHNICAL PATH (the real value for analysis)
        initial_path = self.engine_map.get(initial_display_name, initial_engine_path)
        self._engine_path_var = tk.StringVar(value=initial_path)

        self.create_widgets()
        self.inputfile_var.trace_add("write", self.update_pgn_path)
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    # --- Engine Selector Logic ---

    def on_engine_selected(self, event):
        """Triggered when an option in the combobox is selected."""
        selected_display_name = self.engine_var.get()

        # Look up the technical path based on the display name
        selected_path = self.engine_map.get(selected_display_name)

        if selected_path is not None:
            # Update the internal variable with the technical path
            self._engine_path_var.set(selected_path)
        else:
            # This happens if the user types manually or if there is an unexpected value.
            # We assume the typed value ITSELF is the technical path
            # and update the internal variable as well.
            self._engine_path_var.set(selected_display_name)


    def browse_engine_file(self):
        """Prompts for an engine path and updates both the display and the path."""
        engine_path = filedialog.askopenfilename(
            title="Select the chess engine (e.g., stockfish)",
            filetypes=[("Executable files", ("*")), ("All files", "*.*")]
        )
        if engine_path:
            # 1. Update the internal variable (the real value)
            self._engine_path_var.set(engine_path)

            # 2. Create a new display name for the Combobox
            display_name = f"Custom Path: {os.path.basename(engine_path)}"

            # 3. Update the Combobox with the new display name
            self.engine_var.set(display_name)

            # Optional: add the new option to the map and values
            if display_name not in self.engine_map:
                self.engine_map[display_name] = engine_path
                current_values = list(self.engine_combobox['values'])
                if display_name not in current_values:
                    current_values.append(display_name)
                    self.engine_combobox['values'] = current_values

    # --- Application Logic ---

    def on_closing(self):
        """Stops the executor and closes the application."""
        if self.analysis_future and self.analysis_future.running():
            logger.warning("Task is being cancelled.")
            self.analysis_future.cancel()
        self.executor.shutdown(wait=False)
        self.destroy()

    def update_pgn_path(self, *args, initial_setup=False):
        if initial_setup or not self._pgn_manually_set:
            current_inputfile = self.inputfile_var.get()
            filename_base = extract_filename_from_inputfile(current_inputfile)
            new_pgn_path = os.path.join(self.default_pgn_dir, f"{filename_base}-annotated.pgn")
            self.pgn_var.set(new_pgn_path)

    def set_pgn_manually_set(self, event):
        self._pgn_manually_set = True

    def browse_pgn_file(self):
        self._pgn_manually_set = True
        filename = filedialog.asksaveasfilename(
            defaultextension=".pgn",
            initialdir=os.path.dirname(self.pgn_var.get()) if self.pgn_var.get() else self.default_pgn_dir,
            initialfile=os.path.basename(self.pgn_var.get()) if self.pgn_var.get() else "",
            filetypes=[("PGN files", "*.pgn"), ("All files", "*.*")],
            title="Save the annotated PGN file as..."
        )
        if filename:
            self.pgn_var.set(filename)

    def run_annotate_start(self):
        """Starts the engine analysis in a separate thread and redirects output."""

        if self.analysis_future and self.analysis_future.running():
            self.status_var.set("Error: Analysis is already running.")
            return

        inputfile_arg = self.inputfile_var.get()
        outputfile_arg = self.pgn_var.get()
        filter_arg = self.filter_var.get()

        # USE THE INTERNAL PATH VARIABLE HERE (the technical value)
        engine_arg = self._engine_path_var.get()

        gametime_str = self.gametime_var.get()

        if not inputfile_arg or not engine_arg or not gametime_str:
            # We check the technical value!
            self.status_var.set("Error: Fill in all required fields (Input, Engine Path, Time).")
            return

        try:
            gametime_arg = float(gametime_str)
        except ValueError:
            self.status_var.set("Error: Analysis Time must be a valid number.")
            return

        self.console_text.delete(1.0, tk.END)
        self.redirect_output_start()

        self.status_var.set(f"Engine analysis started for {extract_filename_from_inputfile(inputfile_arg)}... (Running)")
        self.start_button.config(state=tk.DISABLED)
        self.analyze_button.config(state=tk.DISABLED) # Disable Analysis button

        self.analysis_future = self.executor.submit(
            run_annotate, inputfile_arg, engine_arg, gametime_arg, 8, filter_arg, outputfile_arg
        )

        self.after(100, lambda: self.check_analysis_status(outputfile_arg))

    # --- FUNCTIONS FOR PGN ANALYSIS ---

    def run_pgn_analysis(self):
        """Starts the PGN analysis (statistics) in a separate thread."""

        if self.analysis_future and self.analysis_future.running():
            self.status_var.set("Error: Analysis is already running.")
            return

        inputfile_arg = self.inputfile_var.get()
        if not inputfile_arg:
            self.status_var.set("Error: Select an Input File/URL first.")
            return

        self.console_text.delete(1.0, tk.END)
        self.redirect_output_start() # Ensures output goes to the console

        self.status_var.set(f"PGN analysis started for {extract_filename_from_inputfile(inputfile_arg)}... (Running)")

        self.start_button.config(state=tk.DISABLED)
        self.analyze_button.config(state=tk.DISABLED)

        # The analysis is submitted as a future task
        # self.analysis_future = self.executor.submit(
        #     analyze_pgn_stats, inputfile_arg
        # )

        # For synchronous execution in this example, call directly:
        results = analyze_pgn_stats(inputfile_arg)

        if results:
            self.check_analysis_status_pgn(True)
            site_stats, event_stats, all_games = results

            root = tk.Tk()
            # Initialize the GUI with the returned data
            PGNStatsView(root, site_stats, event_stats, inputfile_arg, all_games )
            root.mainloop()
        else:
            self.check_analysis_status_pgn(False)
            # Display an error message if the analysis fails (e.g., no data or incorrect reading)
            messagebox.showerror("Error", "PGN analysis failed. Check the log for the reason.")


    def check_analysis_status_pgn(self, success):
            self.start_button.config(state=tk.NORMAL)
            self.analyze_button.config(state=tk.NORMAL)

            try:
                # success = self.analysis_future.result() # If future were used
                if success:
                    self.status_var.set("✅ PGN Analysis completed. See Log for statistics.")
                else:
                    self.status_var.set("❌ PGN Analysis failed. See log for details.")
            except Exception as e:
                self.status_var.set(f"❌ An unexpected error occurred: {e}")
            finally:
                # self.analysis_future = None # If future were used
                pass # Do nothing since we called directly
    # --- END OF NEW FUNCTIONS FOR PGN ANALYSIS ---


    def redirect_output_start(self):
        """Redirects sys.stdout and the logging handlers to the Text widget."""
        # First remove any existing handlers to prevent duplication
        if self.console_handler:
            logging.getLogger().removeHandler(self.console_handler)

        self.console_handler = ConsoleRedirect(self.console_text)
        sys.stdout = self.console_handler # Redirects print() calls
        logging.getLogger().addHandler(self.console_handler) # Redirects logger calls


    def redirect_output_stop(self):
        """Restores sys.stdout and removes the custom logging handler."""
        sys.stdout = self.original_stdout
        if self.console_handler:
            # We must remove the handler from the root logger
            logging.getLogger().removeHandler(self.console_handler)
            self.console_handler = None


    def check_analysis_status(self, outputfile_arg):
        """Checks if the annotation task in the separate thread is complete."""
        if not self.analysis_future:
            return

        if self.analysis_future.running():
            self.after(100, lambda: self.check_analysis_status(outputfile_arg))
        else:
            self.redirect_output_stop()
            self.start_button.config(state=tk.NORMAL)
            self.analyze_button.config(state=tk.NORMAL) # Re-enable Analysis button

            try:
                success = self.analysis_future.result()
                if success:
                    self.status_var.set(f"✅ Engine analysis complete. Games saved to {os.path.basename(outputfile_arg)}.")
                else:
                    self.status_var.set("❌ Engine analysis failed. See log for details.")
            except Exception as e:
                self.status_var.set(f"❌ An unexpected error occurred: {e}")
            finally:
                self.analysis_future = None


    def create_widgets(self):
        # Configure the grid layout
        self.columnconfigure(1, weight=1)
        self.rowconfigure(8, weight=1) # Now row 8 because of the extra button row

        style = ttk.Style()
        style.configure("TLabel", padding=5, font=('Arial', 10))
        style.configure("TButton", padding=5, font=('Arial', 10, 'bold'))
        style.configure("TCombobox", padding=5, font=('Arial', 10))

        row_index = 0

        # --- Configuration Fields ---

        # 1. INPUT FILE/URL Entry
        ttk.Label(self, text="Input File/URL (-i):").grid(row=row_index, column=0, sticky="w", padx=10, pady=5)
        inputfile_entry = ttk.Entry(self, textvariable=self.inputfile_var, width=80)
        inputfile_entry.grid(row=row_index, column=1, sticky="ew", padx=10, pady=5)
        inputfile_entry.focus_set()
        row_index += 1

        # 2. PGN Entry (Output File)
        ttk.Label(self, text="PGN Output File (-o):").grid(row=row_index, column=0, sticky="w", padx=10, pady=5)
        pgn_entry = ttk.Entry(self, textvariable=self.pgn_var)
        pgn_entry.grid(row=row_index, column=1, sticky="ew", padx=10, pady=5)
        pgn_entry.bind('<Key>', self.set_pgn_manually_set)
        browse_button = ttk.Button(self, text="Browse Output...", command=self.browse_pgn_file)
        browse_button.grid(row=row_index, column=2, sticky="e", padx=(0, 10), pady=5)
        row_index += 1

        # 3. FILTER Combobox
        ttk.Label(self, text="Game Filter (-f):").grid(row=row_index, column=0, sticky="w", padx=10, pady=5)
        filter_combobox = ttk.Combobox(self, textvariable=self.filter_var, values=self.default_filters, width=80)
        filter_combobox.grid(row=row_index, column=1, sticky="ew", padx=10, pady=5)
        ttk.Label(self, text="E.g: Player:Carlsen;Result:1-0").grid(row=row_index, column=2, sticky="w", padx=(0, 10), pady=5)
        row_index += 1

        # 4. ENGINE Combobox
        ttk.Label(self, text="Engine Path (-e):").grid(row=row_index, column=0, sticky="w", padx=10, pady=5)

        self.engine_combobox = ttk.Combobox(self,
                                             textvariable=self.engine_var,
                                             values=self.default_engine_display_names, # Use only the display names
                                             width=80)
        self.engine_combobox.grid(row=row_index, column=1, sticky="ew", padx=10, pady=5)

        self.engine_combobox.bind("<<ComboboxSelected>>", self.on_engine_selected)

        browse_engine_button = ttk.Button(self, text="Browse Engine...", command=self.browse_engine_file)
        browse_engine_button.grid(row=row_index, column=2, sticky="e", padx=(0, 10), pady=5)
        row_index += 1

        # 5. GAMETIME Entry
        ttk.Label(self, text="Analysis Time (-t) [sec]:").grid(row=row_index, column=0, sticky="w", padx=10, pady=5)
        gametime_entry = ttk.Entry(self, textvariable=self.gametime_var, width=10)
        gametime_entry.grid(row=row_index, column=1, sticky="w", padx=10, pady=5)
        row_index += 1

        # 6. Start and Analysis Buttons (NEW ROW WITH TWO BUTTONS)
        button_frame = ttk.Frame(self)
        button_frame.grid(row=row_index, column=0, columnspan=3, sticky="ew", padx=10, pady=15)
        button_frame.columnconfigure(0, weight=1) # For Start Button
        button_frame.columnconfigure(1, weight=1) # For Analysis Button

        # Start Button (Engine Analysis)
        self.start_button = ttk.Button(button_frame, text="Start Analysis (Engine)", command=self.run_annotate_start)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))

        # NEW BUTTON (Statistics Analysis)
        self.analyze_button = ttk.Button(button_frame, text="Analyze PGN (Statistics)", command=self.run_pgn_analysis)
        self.analyze_button.grid(row=0, column=1, sticky="ew", padx=(5, 0))

        row_index += 1

        # 7. Status Label
        self.status_var = tk.StringVar(value="Waiting for configuration or press Start.")
        status_label = ttk.Label(self, textvariable=self.status_var, relief=tk.SUNKEN, anchor="w")
        status_label.grid(row=row_index, column=0, columnspan=3, sticky="ew", padx=10, pady=(5, 10), ipady=5)
        row_index += 1

        # --- Console Output ---

        # 8. Console Label
        ttk.Label(self, text="Analysis Log:").grid(row=row_index, column=0, sticky="w", padx=10, pady=5)
        row_index += 1

        # 9. Console Text Widget with Scrollbar (Frame for layout)
        console_frame = ttk.Frame(self)
        console_frame.grid(row=row_index, column=0, columnspan=3, sticky="nsew", padx=10, pady=(0, 10))
        console_frame.columnconfigure(0, weight=1)
        console_frame.rowconfigure(0, weight=1)

        self.console_text = tk.Text(console_frame, wrap=tk.WORD, state=tk.NORMAL, height=15, bg="#202020", fg="#f0f0f0", font=('Consolas', 9))
        self.console_text.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(console_frame, command=self.console_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.console_text.config(yscrollcommand=scrollbar.set)

        self.grid_rowconfigure(row_index, weight=1)

