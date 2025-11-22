import os
import tkinter as tk
from tkinter import ttk
from typing import List, Dict, Any, Tuple
import threading  # <-- New import for threading

import core
from core import _load_config


class GameListView:
    """
    A separate Toplevel class to display the list of filtered games.
    Adds a context menu to copy full game metadata and a button to start analysis.
    """

    def __init__(self, master: tk.Tk, title: str, games_data: List[Dict[str, Any]], input_filename):
        self.top = tk.Toplevel(master)
        self.top.title(f"Games for: {title}")
        self.top.geometry("600x400")
        self.top.configure(bg='#ECEFF1')

        self.games_data = games_data  # Full data, needed for copying
        self.input_filename = input_filename

        self.title_label = ttk.Label(self.top, text=f"Filtered Games ({len(games_data)}): {title}",
                                     font=('Helvetica', 12, 'bold'), padding=(10, 10))
        self.title_label.pack(fill=tk.X)

        # --- Button Frame (NEW) ---
        self.button_frame = ttk.Frame(self.top, padding=(10, 5))
        self.button_frame.pack(side=tk.BOTTOM, fill=tk.X)

        self.analyze_button = ttk.Button(
            self.button_frame,
            text="Analyze Selected",
            # The button calls the thread-starting function
            command=self._start_analysis_process
        )
        self.analyze_button.pack(side=tk.RIGHT, padx=5, pady=5)
        # --- END Button Frame ---

        # Status bar for feedback (below the buttons)
        self.status_label = ttk.Label(self.top, text="Right-click on a game to copy metadata.",
                                      anchor=tk.W, background='#CFD8DC', padding=(5, 2))
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)

        # Treeview for the games
        self.tree = self._create_treeview(self.top)
        self._load_data_into_tree(self.tree, games_data)

        # Configure Context Menu (NEW)
        self.context_menu = tk.Menu(self.top, tearoff=0)
        self.context_menu.add_command(label="Copy to Clipboard", command=self._copy_selected_game_data)
        # Context menu also calls the thread-starting function
        self.context_menu.add_command(label="Analyze Game", command=self._start_analysis_process)

        # Bind the context menu (right-click) event
        self.tree.bind('<Button-3>', self._show_context_menu)
        self.default_pgn_dir, self.engine_name = self.get_settings()

    # --- HELPER METHODS FOR THREADING AND STATUS UPDATES ---

    def _update_status(self, text: str):
        """Helper to safely configure the status label (called on main thread)."""
        self.status_label.configure(text=text)

    def _update_status_final(self, text: str):
        """Helper to configure final status and re-enable button (called on main thread)."""
        self.status_label.configure(text=text)
        self.analyze_button.config(state=tk.NORMAL)  # Re-enable the button

    def _start_analysis_process(self):
        """
        Wrapper to start the analysis process in a background thread.
        This method is called by the button and the context menu.
        """
        selected_item_ids = self.tree.selection()
        if not selected_item_ids:
            self._update_status("No game selected to analyze.")
            return

        # Disable the button to prevent double-clicking while processing
        self.analyze_button.config(state=tk.DISABLED)

        # Start the worker thread
        threading.Thread(
            target=self._run_analysis_in_thread,
            args=(selected_item_ids,),
            daemon=True
        ).start()

    def _run_analysis_in_thread(self, selected_item_ids: Tuple[str]):
        """
        Runs the long-running core.run_annotate function in a worker thread.
        Updates to the GUI are delegated back to the main thread via self.top.after().
        """

        total_games = len(selected_item_ids)

        for i, game_index_str in enumerate(selected_item_ids):
            try:
                game_index = int(game_index_str)
                game_data = self.games_data[game_index]
            except (ValueError, IndexError):
                # Safely update the status with an error message and continue
                self.top.after(0, self._update_status,
                               f"Error: Could not retrieve data for game index {game_index_str}.")
                continue

                # 1. Prepare analysis strings
            filter_string = f"Event:{game_data['Event']};Site:{game_data['Site']};White:{game_data['White']};Black:{game_data['Black']};Date:{game_data['Date']}"
            new_pgn_path = os.path.join(self.default_pgn_dir, f"{game_data['White']}-{game_data['Black']}.pgn".replace(" ", "_"))
            pgn_output_string = new_pgn_path

            # 2. Update status: "Please wait, analyzing..." (SAFELY via after)
            status_text = f"[{i + 1}/{total_games}] Analyzing: {game_data['White']} vs {game_data['Black']}..."
            self.top.after(0, self._update_status, status_text)

            # 3. Execute the heavy lifting (this only blocks the worker thread)
            try:
                # Original call
                print(filter_string)
                print(pgn_output_string)
                core.run_annotate(self.input_filename, self.engine_name, 1, 8, filter_string, pgn_output_string)
            except Exception as e:
                # Update error status (SAFELY via after)
                self.top.after(0, self._update_status,
                               f"[{i + 1}/{total_games}] Error analyzing {game_data['White']} vs {game_data['Black']}: {e}")
                continue

                # 4. Update success status: "created: ..." (SAFELY via after)
            success_text = f"[{i + 1}/{total_games}] Created: {os.path.basename(pgn_output_string)}"
            self.top.after(0, self._update_status, success_text)

        # 5. Final completion message (SAFELY via after)
        self.top.after(0, self._update_status_final, f"Analysis complete for {total_games} games.")

    # --- END HELPER METHODS FOR THREADING AND STATUS UPDATES ---


    def _create_treeview(self, parent_frame: tk.Toplevel) -> ttk.Treeview:
        """Creates and configures a Treeview widget for the game list."""

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Game.Treeview.Heading", font=('Helvetica', 10, 'bold'),
                        background='#546E7A', foreground='white')

        columns = ("White", "Black", "Result", "Date")
        tree = ttk.Treeview(parent_frame, columns=columns, show='headings', style="Game.Treeview")
        tree.pack(fill="both", expand=True, padx=10, pady=5)

        # Set headers
        tree.heading("White", text="White", anchor=tk.W)
        tree.heading("Black", text="Black", anchor=tk.W)
        tree.heading("Result", text="Result", anchor=tk.CENTER)
        tree.heading("Date", text="Date", anchor=tk.CENTER)

        # Set widths
        tree.column("White", width=150, anchor=tk.W)
        tree.column("Black", width=150, anchor=tk.W)
        tree.column("Result", width=80, anchor=tk.CENTER)
        tree.column("Date", width=100, anchor=tk.CENTER)

        # Add scrollbar
        vsb = ttk.Scrollbar(parent_frame, orient="vertical", command=tree.yview)
        vsb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 10))
        tree.configure(yscrollcommand=vsb.set)

        return tree

    def _load_data_into_tree(self, tree: ttk.Treeview, data: List[Dict[str, Any]]):
        """Inserts game data into the Treeview and uses the index as iid."""
        # Tags for alternating colors
        tree.tag_configure('evenrow', background='#F5F5F5')
        tree.tag_configure('oddrow', background='#FFFFFF')

        for i, item in enumerate(data):
            tag = 'oddrow' if i % 2 != 0 else 'evenrow'
            white = item["White"]
            whiteElo = item["WhiteElo"]
            black = item["Black"]
            blackElo = item["BlackElo"]
            # Use the index 'i' as the item identifier (iid) to retrieve the full data
            tree.insert("", tk.END, iid=str(i), values=(f"{white}({whiteElo})", f"{black}({blackElo})", item["Result"], item["Date"]),
                        tags=(tag,))

    def _show_context_menu(self, event):
        """
        Displays the context menu at the mouse position if a row is selected.
        """
        item_id = self.tree.identify_row(event.y)

        if item_id:
            # Select the item that was clicked
            self.tree.selection_set(item_id)

            try:
                # Show the menu at the mouse position
                self.context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.context_menu.grab_release()

    # The old _analyse_game_data is now _start_analysis_process

    def get_settings(self) -> tuple[str, str]:
        ## 1. Read configuration data
        config_data = _load_config()

        # 2. Assignment of Engine Mappings and Directories

        # The PGN directory is now composed based on the suffix in the JSON
        pgn_suffix = config_data.get("default_pgn_dir_suffix", "Schaken")
        default_pgn_dir = os.path.join(os.path.expanduser("~"), pgn_suffix)
        print(f"Default PGN Directory: {default_pgn_dir}")

        # The engine options are loaded from the JSON
        # The JSON structure (list of dicts) is converted to the Python structure (list of tuples)
        json_engine_options: List[Dict[str, str]] = config_data.get("engine_options", [])
        self.engine_options: List[Tuple[str, str]] = [
            (item.get("display_name", ""), item.get("path", ""))
            for item in json_engine_options
        ]

        # 2. Map for quick lookup
        self.engine_map: Dict[str, str] = {name: path for name, path in self.engine_options}

        # 3. Only the path names for the engine-name
        self.default_engine_display_names: List[str] = [path for name, path in self.engine_options]
        # get the first item (hope it is there) as the engie to be used
        engine_name = self.default_engine_display_names[0]
        return default_pgn_dir, engine_name

    def _copy_selected_game_data(self):
        """
        Copies the full metadata of the selected game to the clipboard.
        """
        selected_item_ids = self.tree.selection()
        if not selected_item_ids:
            self.status_label.configure(text="No game selected to copy.")
            return

        # The iid is the stringified index in self.games_data
        game_index_str = selected_item_ids[0]
        try:
            game_index = int(game_index_str)
            game_data = self.games_data[game_index]
        except (ValueError, IndexError):
            self.status_label.configure(text="Error: Could not retrieve game data.")
            return

        # Format the requested string: Event:...;Site:...;White:...;Black:....;Date:....
        copy_string = (
            f"Event:{game_data['Event']};"
            f"Site:{game_data['Site']};"
            f"White:{game_data['White']};"
            f"Black:{game_data['Black']};"
            f"Date:{game_data['Date']}"
        )

        self.top.clipboard_clear()
        self.top.clipboard_append(copy_string)

        self.status_label.configure(text=f"Game data copied to clipboard: {copy_string[:60]}...")