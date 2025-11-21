import tkinter as tk
import core
from typing import Tuple
from core import logger
import chess
import chess.pgn
import chess.engine
import chess.variant
import os

# run_annotate(pgnfile: str, enginepath: str, gametime: int, threads: int, filter_str: str, outputfile: str)
#
from tkinter import ttk
from typing import List, Dict, Any, Optional
from core import _load_config


# --- HELPER FUNCTION FOR SIMULATING PGN READING ---
def _pgn_reader(input_file_path: str, tag_name: str, tag_value: str, all_games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Reads a PGN file and filters by a Site or Event.

    Args:
        input_filename: The name of the PGN file
        tag_name: "Site" or "Event".
        tag_value: The name of the Site or Event to filter on.

    Returns:
        A list of game data (White, Black, Result, Date, Site, Event).
    """

    # Filter the data
    filtered_games = [game for game in all_games if game.get(tag_name) == tag_value]

    # Format the output to return only the necessary meta-information
    output = []
    for game in filtered_games:
        # Ensure all necessary tags are present for the GameListView
        output.append({
            "White": game["White"],
            "Black": game["Black"],
            "Result": game["Result"],
            "Site": game["Site"],
            "Event": game["Event"],
            "Date": game["Date"]
        })

    return output


class GameListView:
    """
    A separate Toplevel class to display the list of filtered games.
    Adds a context menu to copy full game metadata.
    """

    def __init__(self, master: tk.Tk, title: str, games_data: List[Dict[str, Any]], input_filename):
        self.top = tk.Toplevel(master)
        self.top.title(f"Games for: {title}")
        self.top.geometry("600x400")
        self.top.configure(bg='#ECEFF1')

        self.games_data = games_data # Full data, needed for copying
        self.input_filename = input_filename

        self.title_label = ttk.Label(self.top, text=f"Filtered Games ({len(games_data)}): {title}",
                                     font=('Helvetica', 12, 'bold'), padding=(10, 10))
        self.title_label.pack(fill=tk.X)

        # Status bar for feedback (NEW)
        self.status_label = ttk.Label(self.top, text="Right-click on a game to copy metadata.",
                                      anchor=tk.W, background='#CFD8DC', padding=(5, 2))
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)

        # Treeview for the games
        self.tree = self._create_treeview(self.top)
        self._load_data_into_tree(self.tree, games_data)

        # Configure Context Menu (NEW)
        self.context_menu = tk.Menu(self.top, tearoff=0)
        self.context_menu.add_command(label="Copy to Clipboard", command=self._copy_selected_game_data)
        self.context_menu.add_command(label="Analyze Game", command=self._analyse_game_data)

        # Bind the context menu (right-click) event
        self.tree.bind('<Button-3>', self._show_context_menu)


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
            # Use the index 'i' as the item identifier (iid) to retrieve the full data
            tree.insert("", tk.END, iid=str(i), values=(item["White"], item["Black"], item["Result"], item["Date"]), tags=(tag,))

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

    def _analyse_game_data(self):
        """
        Copies the full metadata of the selected game to the clipboard.
        """
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

        # get the current item in the list displayed on screen
        selected_item_ids = self.tree.selection()
        if not selected_item_ids:
            self.status_label.configure(text="No game selected to copy.")
            return

        # The id is the stringified index in self.games_data
        game_index_str = selected_item_ids[0]
        try:
            game_index = int(game_index_str)
            game_data = self.games_data[game_index]
        except (ValueError, IndexError):
            self.status_label.configure(text="Error: Could not retrieve game data.")
            return

        # Format the requested string: Event:...;Site:...;White:...;Black:....;Date:....
        filter_string = f"Event:{game_data['Event']};Site:{game_data['Site']};White:{game_data['White']};Black:{game_data['Black']};Date:{game_data['Date']}"
        new_pgn_path = os.path.join(default_pgn_dir, f"{game_data['White']}-{game_data['Black']}.pgn")
        pgn_output_string = new_pgn_path
        #update status in console
        print(f"start analysis, create {new_pgn_path}")
        self.status_label.configure(text=f"Please wait, analyzing: {pgn_output_string[:60]}...")
        self.status_label.update()
        core.run_annotate(self.input_filename, engine_name, 1.0, 8, filter_string, pgn_output_string)

        self.status_label.configure(text=f"created: {pgn_output_string[:60]}...")

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


class PGNStatsView:
    def __init__(self, master, site_data: List[Dict[str, Any]], event_data: List[Dict[str, Any]], input_filename: str, all_games: List[Dict[str, Any]]):
        self.master = master
        master.title("PGN Analysis Results")
        master.geometry("700x500")
        master.configure(bg='#ECEFF1')

        self.site_data = site_data
        self.event_data = event_data
        self.input_filename = input_filename  # Storage of the PGN filename
        self.all_games = all_games

        # Current Treeview where the context menu is activated
        self.current_tree: Optional[ttk.Treeview] = None

        # 0. Status bar for feedback
        self.status_label = ttk.Label(master, text="Click on a row to copy the name. Right-click for menu.",
                                      anchor=tk.W, background='#CFD8DC', padding=(5, 2))
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)

        # 1. Notebook for tabs (Site / Event)
        self.notebook = ttk.Notebook(master)
        self.notebook.pack(pady=10, padx=10, fill="both", expand=True)

        # 2. Create the two frames for the tabs
        self.site_frame = ttk.Frame(self.notebook, padding="10")
        self.event_frame = ttk.Frame(self.notebook, padding="10")

        self.notebook.add(self.site_frame, text="Statistics by Site")
        self.notebook.add(self.event_frame, text="Statistics by Event")

        # 3. Initialize the Treeviews
        self.tree_site = self._create_treeview(self.site_frame, "Site")
        self.tree_event = self._create_treeview(self.event_frame, "Event")

        # Configure Context Menu
        self.context_menu = tk.Menu(master, tearoff=0)
        self.context_menu.add_command(label="Copy Name", command=self._copy_selected_item_via_menu)
        # Show Games option
        self.context_menu.add_command(label="Show Games", command=self._display_selected_games)

        # Bind the context menu (right-click) event
        self.tree_site.bind('<Button-3>', lambda event: self._show_context_menu(event, self.tree_site))
        self.tree_event.bind('<Button-3>', lambda event: self._show_context_menu(event, self.tree_event))

        # Bind the selection event for feedback/standard copying (left-click)
        self.tree_site.bind('<<TreeviewSelect>>', self._copy_to_clipboard)
        self.tree_event.bind('<<TreeviewSelect>>', self._copy_to_clipboard)

        # 4. Populate the Treeviews and initially sort by ELO (descending)
        self._load_data_into_tree(self.tree_site, self.site_data)
        self._load_data_into_tree(self.tree_event, self.event_data)

        # Initial sorting
        self._sort_treeview(self.tree_site, 'AvgElo', True)
        self._sort_treeview(self.tree_event, 'AvgElo', True)
        self._update_header_indicator(self.tree_site, 'AvgElo', True)
        self._update_header_indicator(self.tree_event, 'AvgElo', True)

    def _show_context_menu(self, event, tree_widget: ttk.Treeview):
        """
        Displays the context menu at the mouse position if a row is selected.
        """
        item = tree_widget.identify_row(event.y)

        if item:
            # Select the item that was clicked
            tree_widget.selection_set(item)

            # Set the current treeview for use in the menu command handlers
            self.current_tree = tree_widget

            try:
                # Show the menu at the mouse position
                self.context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.context_menu.grab_release()

    def _get_selected_item_data(self) -> Optional[tuple[str, str, str]]:
        """Retrieves the name of the selected item and the column name."""
        if not self.current_tree:
            return None

        selected_item_ids = self.current_tree.selection()
        if not selected_item_ids:
            return None

        # Name of the column is the text of the first header (Site or Event)
        col_name_tag = self.current_tree.heading('Naam')['text'].split(' ')[0]

        # Values of the selected row
        values = self.current_tree.item(selected_item_ids[0], 'values')

        if values:
            item_name = str(values[0])  # Name or Event is at index 0
            return (col_name_tag, item_name, self.input_filename)
        return None

    def _copy_selected_item_via_menu(self):
        """Function called by the 'Copy Name' menu command."""
        if self.current_tree:
            self._copy_item(self.current_tree)

    def _display_selected_games(self):
        """Function called by the 'Show Games' menu command."""

        item_data = self._get_selected_item_data()

        if item_data:
            tag_name, tag_value, filename = item_data

            self.status_label.configure(text=f"Loading games for: {tag_name} - {tag_value}...")

            # --- PGN READ AND FILTER LOGIC ---
            filtered_games = _pgn_reader(filename, tag_name, tag_value, self.all_games)
            # --- END PGN LOGIC ---

            if filtered_games:
                GameListView(self.master, tag_value, filtered_games, self.input_filename)
                self.status_label.configure(text=f"{len(filtered_games)} games loaded for '{tag_value}'.")
            else:
                self.status_label.configure(text=f"No games found for '{tag_value}' in {filename}.")
        else:
            self.status_label.configure(text="No item selected.")

    def _copy_item(self, tree: ttk.Treeview):
        """Copies the 'Naam' (Site or Event) of the selected row in the given tree to the clipboard."""

        selected_item_ids = tree.selection()

        if not selected_item_ids:
            return

        values = tree.item(selected_item_ids[0], 'values')

        if values:
            name_to_copy = str(values[0])
            self.master.clipboard_clear()
            self.master.clipboard_append(name_to_copy)
            self.status_label.configure(text=f"'{name_to_copy}' copied to clipboard.")

    def _copy_to_clipboard(self, event):
        """Handles the standard left-click selection event to copy the item."""
        tree = event.widget
        self._copy_item(tree)

    def _create_treeview(self, parent_frame: ttk.Frame, name_column_title: str) -> ttk.Treeview:
        """Creates and configures a Treeview widget."""

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview.Heading", font=('Helvetica', 10, 'bold'),
                        background='#37474F', foreground='white')
        style.configure("Treeview", rowheight=28)

        # Note: Keeping 'Naam' as the column key internally, as it's used in _sort_treeview
        # and _get_selected_item_data, but translating the display text.
        tree = ttk.Treeview(parent_frame, columns=("Naam", "Count", "AvgElo"), show='headings')
        tree.pack(fill="both", expand=True)

        tree.heading("Naam", text=name_column_title, anchor=tk.W,
                     command=lambda: self._sort_wrapper(tree, "Naam", False))
        tree.heading("Count", text="Number of Games", anchor=tk.CENTER,
                     command=lambda: self._sort_wrapper(tree, "Count", True))
        tree.heading("AvgElo", text="Average ELO", anchor=tk.CENTER,
                     command=lambda: self._sort_wrapper(tree, "AvgElo", True))

        tree.column("Naam", width=250, anchor=tk.W)
        tree.column("Count", width=120, anchor=tk.CENTER)
        tree.column("AvgElo", width=120, anchor=tk.CENTER)

        vsb = ttk.Scrollbar(parent_frame, orient="vertical", command=tree.yview)
        vsb.place(relx=1.0, rely=0, relheight=1.0, anchor='ne')
        tree.configure(yscrollcommand=vsb.set)

        return tree

    def _load_data_into_tree(self, tree: ttk.Treeview, data: List[Dict[str, Any]]):
        """Inserts the structured data into the Treeview."""
        for item in tree.get_children():
            tree.delete(item)

        for i, item in enumerate(data):
            tag = 'oddrow' if i % 2 != 0 else 'evenrow'
            tree.insert("", tk.END, values=(item["Naam"], item["Count"], item["AvgElo"]), tags=(tag,))

        tree.tag_configure('evenrow', background='#F5F5F5')
        tree.tag_configure('oddrow', background='#FFFFFF')

    def _sort_wrapper(self, tree: ttk.Treeview, col_key: str, is_numeric: bool):
        """Wrapper for sorting, determines the new direction and calls the sorting function."""

        current_text = tree.heading(col_key)['text']
        reverse = False

        if "▲" in current_text:
            reverse = True
        elif "▼" in current_text:
            reverse = False
        elif col_key in ('Count', 'AvgElo'):
            reverse = True

        self._sort_treeview(tree, col_key, reverse)
        self._update_header_indicator(tree, col_key, reverse)

    def _sort_treeview(self, tree: ttk.Treeview, col_key: str, reverse: bool):
        """Sorts the rows in the Treeview based on the column and direction."""

        data = [(tree.set(child, col_key), child) for child in tree.get_children('')]

        if col_key in ('Count', 'AvgElo'):
            data.sort(key=lambda t: int(t[0]), reverse=reverse)
        else:
            data.sort(key=lambda t: t[0].lower(), reverse=reverse)

        for index, (val, child) in enumerate(data):
            tree.move(child, '', index)
            tag = 'oddrow' if index % 2 != 0 else 'evenrow'
            tree.item(child, tags=(tag,))

    def _update_header_indicator(self, tree: ttk.Treeview, col_key: str, reverse: bool):
        """Updates the column headers to show the sorting direction."""

        for c in tree['columns']:
            original_text = tree.heading(c)['text'].split(' ')[0]
            tree.heading(c, text=original_text, command=tree.heading(c, option='command'))

        indicator = " ▼" if reverse else " ▲"
        original_text = tree.heading(col_key)['text'].split(' ')[0]

        command = tree.heading(col_key, option='command')
        tree.heading(col_key, text=original_text + indicator, command=command)

# === Example Usage (FOR TESTING ONLY) ===
if __name__ == '__main__':
    # Mock Data for PGNStatsView
    SITE_DATA_MOCK = [
        {"Naam": "Chess.com", "Count": 15, "AvgElo": 1950},
        {"Naam": "Lichess.org", "Count": 22, "AvgElo": 1850},
        {"Naam": "ICC", "Count": 5, "AvgElo": 2500},
    ]

    EVENT_DATA_MOCK = [
        {"Naam": "World Championship Match", "Count": 12, "AvgElo": 2800},
        {"Naam": "TCEC Season 20", "Count": 10, "AvgElo": 3500},
        {"Naam": "Blitz Tournament", "Count": 6, "AvgElo": 1600},
    ]

    # Detailed mock game data (needed for GameListView)
    ALL_GAMES_MOCK = [
        {"White": "Carlsen, M.", "Black": "Caruana, F.", "Result": "1/2-1/2", "Site": "ICC", "Event": "World Championship Match", "Date": "2018.11.09"},
        {"White": "Kasparov, G.", "Black": "Karpov, A.", "Result": "1-0", "Site": "Chess.com", "Event": "World Championship Match", "Date": "1985.09.03"},
        {"White": "Fischer, R.", "Black": "Spassky, B.", "Result": "0-1", "Site": "Chess.com", "Event": "Blitz Tournament", "Date": "1972.07.11"},
        {"White": "AlphaZero", "Black": "Stockfish", "Result": "1-0", "Site": "Lichess.org", "Event": "TCEC Season 20", "Date": "2021.01.20"},
        {"White": "Stockfish", "Black": "AlphaZero", "Result": "1/2-1/2", "Site": "Lichess.org", "Event": "TCEC Season 20", "Date": "2021.01.21"},
        {"White": "Anon 1", "Black": "Anon 2", "Result": "1-0", "Site": "Lichess.org", "Event": "Blitz Tournament", "Date": "2023.05.01"},
        # Add more data to populate the list
        *[{"White": f"Player {i}W", "Black": f"Player {i}B", "Result": "1-0", "Site": "Chess.com", "Event": "Chess.com", "Date": f"2023.01.{i:02d}"} for i in range(1, 16)],
        *[{"White": f"Player {i}L", "Black": f"Player {i}R", "Result": "0-1", "Site": "Lichess.org", "Event": "Lichess.org", "Date": f"2023.02.{i:02d}"} for i in range(1, 23)],
        *[{"White": f"Engine {i}A", "Black": f"Engine {i}B", "Result": "1/2-1/2", "Site": "ICC", "Event": "ICC", "Date": f"2023.03.{i:02d}"} for i in range(1, 6)],
    ]


    root = tk.Tk()
    app = PGNStatsView(root, SITE_DATA_MOCK, EVENT_DATA_MOCK, input_filename="my_games.pgn", all_games=ALL_GAMES_MOCK)
    root.mainloop()