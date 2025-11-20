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
import math
import chess
import chess.pgn
import chess.engine
import asyncio
import chess.variant

print(f"--- Debugging in: {__file__} ---")
print(f"Current Working Directory: {os.getcwd()}")
print(f"Python Executable: {sys.executable}")
print("sys.path:")
for p_path in sys.path:
    print(f"  {p_path}")
print("--- End Debugging ---")


# Constants
ERROR_THRESHOLD = {
    'BLUNDER': -200,
    'MISTAKE': -100,
    'DUBIOUS': -30,
}
NEEDS_ANNOTATION_THRESHOLD = 1.0
MAX_SCORE = 10000
MAX_CPL = 2000
SHORT_PV_LEN = 10

# Initialize Logging Module
logger = logging.getLogger(__name__)
if not logger.handlers:
    ch = logging.StreamHandler()
    logger.addHandler(ch)
# Uncomment this line to get EXTREMELY verbose UCI communication logging:
# logging.basicConfig(level=logging.DEBUG)
logger.setLevel(logging.INFO)


def parse_args():
    """
    Define an argument parser and return the parsed arguments
    """
    parser = argparse.ArgumentParser(
        prog='annotator',
        description='takes chess games in a PGN file and prints '
        'annotations to standard output')
    parser.add_argument("--gui", "-g",
                        help="Start de grafische gebruikersinterface (GUI)",
                        action='store_true')
    parser.add_argument("--filter", "-i",
                        help="Filter games op metadata (bijv. Event:WK;Player:Carlsen)",
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
                        help="Expliciet pad voor het PGN uitvoerbestand. Overschrijft de standaard --pgn logica.",
                        default="")

    return parser.parse_args()


# Functie om de bestandsnaam uit de URL/Pad te halen
def extract_filename_from_inputfile(input_path: str) -> str:
    """
    Haalt de bestandsnaam (het laatste padsegment) uit een URL of lokaal pad.
    """
    try:
        if not input_path:
            return "default_game"

        is_url = urlparse(input_path).scheme in ('http', 'https')

        if is_url:
            parsed_url = urlparse(input_path)
            path = parsed_url.path
        else:
            path = input_path

        filename = os.path.basename(path)
        if '?' in filename:
            filename = filename.split('?')[0]
        if not filename:
            return "default_game"

        # Zorgt ervoor dat .pgn niet dubbel in de naam komt
        if filename.lower().endswith(".pgn"):
            filename = filename[:-4]

        return filename

    except Exception:
        return "default_game"

def matches_filter(game: chess.pgn.Game, filter_string: str) -> bool:
    """
    Controleert of de metagegevens van een game voldoen aan de filtercriteria.
    """
    if not filter_string or filter_string == "Geen":
        return True

    # --- IMPLEMENTATIE 'INTERESTING' FILTER ---
    if filter_string == "Interesting":
        # Een partij is "Interesting" als:
        # 1. Het resultaat niet remise is.
        # 2. EN (Beide spelers >= HIGH_RATING) OF (Een speler met >= HIGH_RATING verliest).

        HIGH_RATING = 2650 # Drempelwaarde voor 'hoge rating'

        # 1. Controleer op Remise
        result = game.headers.get("Result")
        if result == "1/2-1/2" or result is None:
            return False # Partij is remise of heeft geen resultaat

        # 2. Haal Ratings op (veilig parsen)
        try:
            white_rating = int(game.headers.get("WhiteElo", 0))
        except ValueError:
            white_rating = 0

        try:
            black_rating = int(game.headers.get("BlackElo", 0))
        except ValueError:
            black_rating = 0

        # --- Rating Criteria Evaluatie ---

        is_high_rated_white = white_rating >= HIGH_RATING
        is_high_rated_black = black_rating >= HIGH_RATING

        # A. Voldoet aan: twee spelers met hoge rating?
        if is_high_rated_white and is_high_rated_black:
            return True

        # B. Voldoet aan: een speler met hoge rating verliest?
        high_rated_lost = False

        # Wit won (1-0), controleer of hoog gewaardeerde Zwart verloor
        if result == "1-0" and is_high_rated_black and not is_high_rated_white:
            # Alleen interessant als de hoog gewaardeerde Zwart verliest van een lagere rating
            high_rated_lost = True

        # Zwart won (0-1), controleer of hoog gewaardeerde Wit verloor
        elif result == "0-1" and is_high_rated_white and not is_high_rated_black:
            # Alleen interessant als de hoog gewaardeerde Wit verliest van een lagere rating
            high_rated_lost = True

        if high_rated_lost:
            return True

        # Voldoet niet aan de 'Interesting' criteria
        return False
    # --- EINDE 'INTERESTING' FILTER ---

    filters = [f.strip() for f in filter_string.split(';') if f.strip()]

    for filter_item in filters:
        if ':' not in filter_item:
            print(f"Waarschuwing: Ongeldig filterformaat '{filter_item}'. Moet 'Key:Value' zijn.")
            continue

        key, value_str = [p.strip() for p in filter_item.split(':', 1)]
        values = [v.strip() for v in value_str.split(',') if v.strip()]

        passed_condition = False

        # 1. Speciaal geval: Player (zoekt in White EN Black)
        if key.lower() == 'player':
            for val in values:
                white_player = game.headers.get("White", "")
                black_player = game.headers.get("Black", "")
                if val.lower() in white_player.lower() or val.lower() in black_player.lower():
                    passed_condition = True
                    break

        # 2. Speciaal geval: Title (zoekt in WhiteTitle EN BlackTitle)
        elif key.lower() == 'title':
            for val in values:
                white_title = game.headers.get("WhiteTitle", "")
                black_title = game.headers.get("BlackTitle", "")
                if val.lower() in white_title.lower() or val.lower() in black_title.lower():
                    passed_condition = True
                    break

        elif key.lower() == 'site':
            for val in values:
                event_title = game.headers.get("Site", "")
                if val.lower() in event_title.lower():
                    passed_condition = True
                    break
        elif key.lower() == 'event':
            for val in values:
                event_title = game.headers.get("Event", "")
                if val.lower() in event_title.lower():
                    passed_condition = True
                    break
        # 3. Algemeen geval: Normale header match (bijv. Event, Site, Result)
        else:
            header_value = game.headers.get(key, "")

            # Substring match
            if key.lower() not in ['result', 'round', 'date', 'eco']:
                for val in values:
                    if val.lower() in header_value.lower():
                        passed_condition = True
                        break
            # Exacte match
            else:
                for val in values:
                    if val.lower() == header_value.lower():
                        passed_condition = True
                        break

        if not passed_condition:
            return False

    return True
# --- LOGGING EN STDOUT OMLEIDINGSKLASSE ---

class ConsoleRedirect(logging.Handler):
    """
    Een custom logging handler en stdout 'file-like' object dat alle
    uitvoer omleidt naar een Tkinter Text widget op een thread-veilige manier.
    """
    def __init__(self, text_widget: tk.Text):
        super().__init__()
        self.text_widget = text_widget
        self.queue = []
        self.running = True
        # Formatter voor logberichten
        self.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))

    def emit(self, record):
        """Wordt aangeroepen door de logging module."""
        # Formatteer het bericht met de logging formatter
        msg = self.format(record)
        self.write(msg + '\n')

    def write(self, s):
        """Wordt aangeroepen door print() (via sys.stdout)."""
        # Voeg bericht toe aan de wachtrij. We updaten het widget NIET direct.
        self.queue.append(s)

        # Plan de GUI-update (thread-safe)
        self.text_widget.after(0, self.process_queue)

    def flush(self):
        """Vereist voor file-like objecten, maar doet hier niets."""
        pass

    def process_queue(self):
        """Verwerkt de berichtenwachtrij in de hoofd-Tkinter thread."""
        while self.queue:
            message = self.queue.pop(0)
            self.text_widget.insert(tk.END, message)
            self.text_widget.see(tk.END) # Scroll automatisch naar beneden

def analyze_pgn_stats(input_file_path: str) -> Tuple[Dict[str, Any], Dict[str, Any]] | None:
    """
    Leest het PGN-bestand en berekent de statistieken per Site en Event.

    Retourneert de verwerkte statistieken in de vorm van twee dictionaries:
    (stats_site, stats_event).
    """
    stats_site = defaultdict(lambda: {'count': 0, 'total_elo': 0, 'player_count': 0})
    stats_event = defaultdict(lambda: {'count': 0, 'total_elo': 0, 'player_count': 0})

    # In een echte applicatie zou u een bestandspad valideren
    if not input_file_path or not os.path.exists(input_file_path):
        # Omdat we hier mocken, negeren we de file check en gebruiken we de mock-data
        if input_file_path == "MOCK_DATA":
            logger.info("Gebruik maken van gesimuleerde PGN-data voor Tkinter demonstratie.")
        else:
            logger.error(f"Fout: Inputbestand niet gevonden of niet gespecificeerd: {input_file_path}")
            return None

    try:
        if input_file_path != "MOCK_DATA":
             logger.info(f"Start PGN-analyse van: {os.path.basename(input_file_path)}")
             # Hier zou het bestand geopend worden:
             pgn_file = open(input_file_path, encoding="utf-8")
        else:
             # Gebruik een 'dummy' file handle voor de mock-lezer
             pgn_file = None

        game_counter = 0
        while True:
            # Gebruik de mock-lezer als het een simulatie is, anders de echte lezer
            if input_file_path == "MOCK_DATA":
                 game = chess.pgn.read_game(pgn_file)
            else:
                 game = chess.pgn.read_game(pgn_file) # Echte lezer

            if game is None:
                break
            game_counter += 1

            # 1. Headers ophalen
            site = game.headers.get("Site", "Onbekende Site")
            event = game.headers.get("Event", "Onbekend Event")

            white_elo_str = game.headers.get("WhiteElo", "0")
            black_elo_str = game.headers.get("BlackElo", "0")

            # 2. Elo en Aantal Spelers berekenen
            current_game_total_elo = 0
            current_game_player_count = 0

            try:
                white_elo = int(white_elo_str)
                current_game_total_elo += white_elo
                current_game_player_count += 1
            except ValueError:
                pass # Negeren als Elo ongeldig is

            try:
                black_elo = int(black_elo_str)
                current_game_total_elo += black_elo
                current_game_player_count += 1
            except ValueError:
                pass

            # Als er ten minste één geldige rating is, update de statistieken
            if current_game_player_count > 0:
                stats_site[site]['count'] += 1
                stats_site[site]['total_elo'] += current_game_total_elo
                stats_site[site]['player_count'] += current_game_player_count

                stats_event[event]['count'] += 1
                stats_event[event]['total_elo'] += current_game_total_elo
                stats_event[event]['player_count'] += current_game_player_count

        logger.info(f"Totaal {game_counter} partijen gelezen.")

        # Sluit bestand alleen als het echt geopend is
        if pgn_file:
             pgn_file.close()

        # 3. Formatteer de resultaten en retourneer ze

        # Functie om de ruwe data naar de Treeview-structuur te converteren
        def format_stats(raw_stats: Dict[str, Any]) -> List[Dict[str, Any]]:
            formatted = []
            for name, data in raw_stats.items():
                # Bereken Gemiddelde ELO
                avg_elo = data['total_elo'] / data['player_count'] if data['player_count'] > 0 else 0
                formatted.append({
                    "Naam": name,
                    "Count": data['count'],
                    "AvgElo": round(avg_elo)
                })
            return formatted

        return format_stats(stats_site), format_stats(stats_event)

    except Exception as e:
        logger.error(f"Onverwachte fout tijdens PGN-analyse: {e}")
        # In geval van fouten, retourneer een lege set om de GUI niet te breken
        return None

# ----------------------------------------------------------------------
# 2. TKINTER GUI KLASSE
# ----------------------------------------------------------------------

class PGNStatsView:
    def __init__(self, master, site_data: List[Dict[str, Any]], event_data: List[Dict[str, Any]]):
        self.master = master
        master.title("PGN Analyse Resultaten")
        master.geometry("700x500")
        master.configure(bg='#ECEFF1')

        self.site_data = site_data
        self.event_data = event_data

        # 0. Statusbalk voor feedback (NIEUW)
        self.status_label = ttk.Label(master, text="Klik op een rij om de naam te kopiëren.",
                                      anchor=tk.W, background='#CFD8DC', padding=(5, 2))
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)

        # 1. Notebook voor tabbladen (Site / Event)
        self.notebook = ttk.Notebook(master)
        self.notebook.pack(pady=10, padx=10, fill="both", expand=True)

        # 2. Creëer de twee frames voor de tabbladen
        self.site_frame = ttk.Frame(self.notebook, padding="10")
        self.event_frame = ttk.Frame(self.notebook, padding="10")

        self.notebook.add(self.site_frame, text="Statistieken per Site")
        self.notebook.add(self.event_frame, text="Statistieken per Event")

        # 3. Initialiseer de Treeviews
        self.tree_site = self._create_treeview(self.site_frame, "Site")
        self.tree_event = self._create_treeview(self.event_frame, "Event")

        # Bind de klik-gebeurtenis aan de klembord-functie (NIEUW)
        self.tree_site.bind('<<TreeviewSelect>>', self._copy_to_clipboard)
        self.tree_event.bind('<<TreeviewSelect>>', self._copy_to_clipboard)

        # 4. Vul de Treeviews en sorteer initiëel op ELO (aflopend)
        self._load_data_into_tree(self.tree_site, self.site_data)
        self._load_data_into_tree(self.tree_event, self.event_data)

        # Sorteer initiëel op Gemiddelde ELO (hoogste ELO bovenaan)
        self._sort_treeview(self.tree_site, 'AvgElo', True)
        self._sort_treeview(self.tree_event, 'AvgElo', True)

        # Werk de headers bij na de initiële sortering
        self._update_header_indicator(self.tree_site, 'AvgElo', True)
        self._update_header_indicator(self.tree_event, 'AvgElo', True)

    def _copy_to_clipboard(self, event):
        """Kopieert de 'Naam' (Site of Event) van de geselecteerde rij naar het klembord."""

        # Bepaal welke treeview de gebeurtenis heeft geactiveerd
        tree = event.widget
        selected_item_ids = tree.selection()

        if not selected_item_ids:
            return

        # Haal de waarden van het geselecteerde item op
        # selection() geeft een tuple van IDs terug, we gebruiken de eerste [0]
        values = tree.item(selected_item_ids[0], 'values')

        if values:
            name_to_copy = str(values[0]) # Naam of Event staat op index 0

            # Kopieer naar het klembord
            self.master.clipboard_clear()
            self.master.clipboard_append(name_to_copy)

            # Toon feedback in de statusbalk
            self.status_label.configure(text=f"'{name_to_copy}' is gekopieerd naar het klembord.")

    def _create_treeview(self, parent_frame: ttk.Frame, name_column_title: str) -> ttk.Treeview:
        """Maakt en configureert een Treeview widget."""

        # Gebruik de 'Treeview' stijl
        style = ttk.Style()
        style.theme_use("clam")

        # Aangepaste stijl voor een strakker uiterlijk
        style.configure("Treeview.Heading", font=('Helvetica', 10, 'bold'),
                         background='#37474F', foreground='white')
        style.configure("Treeview", rowheight=28)

        # Definieer kolommen
        tree = ttk.Treeview(parent_frame, columns=("Naam", "Count", "AvgElo"), show='headings')
        tree.pack(fill="both", expand=True)

        # Headers instellen
        tree.heading("Naam", text=name_column_title, anchor=tk.W,
                     command=lambda: self._sort_wrapper(tree, "Naam", False))
        tree.heading("Count", text="Aantal Partijen", anchor=tk.CENTER,
                     command=lambda: self._sort_wrapper(tree, "Count", True))
        tree.heading("AvgElo", text="Gemiddelde ELO", anchor=tk.CENTER,
                     command=lambda: self._sort_wrapper(tree, "AvgElo", True))

        # Breedtes instellen
        tree.column("Naam", width=250, anchor=tk.W)
        tree.column("Count", width=120, anchor=tk.CENTER)
        tree.column("AvgElo", width=120, anchor=tk.CENTER)

        # Scrollbar toevoegen
        vsb = ttk.Scrollbar(parent_frame, orient="vertical", command=tree.yview)
        # Gebruik place() om de scrollbar naast de treeview te positioneren
        vsb.place(relx=1.0, rely=0, relheight=1.0, anchor='ne')
        tree.configure(yscrollcommand=vsb.set)

        return tree

    def _load_data_into_tree(self, tree: ttk.Treeview, data: List[Dict[str, Any]]):
        """Voegt de gestructureerde data toe aan de Treeview."""
        for item in tree.get_children():
            tree.delete(item)

        for i, item in enumerate(data):
            tag = 'oddrow' if i % 2 != 0 else 'evenrow'
            tree.insert("", tk.END, values=(item["Naam"], item["Count"], item["AvgElo"]), tags=(tag,))

        # Configureer de tags voor afwisselende kleuren
        tree.tag_configure('evenrow', background='#F5F5F5')
        tree.tag_configure('oddrow', background='#FFFFFF')

    def _sort_wrapper(self, tree: ttk.Treeview, col_key: str, is_numeric: bool):
        """Wrapper voor sortering, bepaalt de nieuwe richting en roept de sortering aan."""

        current_text = tree.heading(col_key)['text']
        reverse = False

        if "▲" in current_text:
            reverse = True
        elif "▼" in current_text:
             reverse = False
        elif col_key in ('Count', 'AvgElo'):
             # Eerste klik op numerieke velden: aflopend (logisch voor aantallen/scores)
             reverse = True

        self._sort_treeview(tree, col_key, reverse)
        self._update_header_indicator(tree, col_key, reverse)

    def _sort_treeview(self, tree: ttk.Treeview, col_key: str, reverse: bool):
        """Sorteert de rijen in de Treeview op basis van de kolom en richting."""

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
        """Werkt de kolomheaders bij om de sorteerrichting te tonen."""

        # 1. Reset alle headers
        for c in tree['columns']:
            original_text = tree.heading(c)['text'].split(' ')[0]
            # Herstel de oorspronkelijke 'command'
            tree.heading(c, text=original_text, command=tree.heading(c, option='command'))

        # 2. Stel de nieuwe header in met indicator
        indicator = " ▼" if reverse else " ▲"
        original_text = tree.heading(col_key)['text'].split(' ')[0]

        # Behoud de command handler
        command = tree.heading(col_key, option='command')
        tree.heading(col_key, text=original_text + indicator, command=command)

# --- Tkinter Applicatie Klasse ---

class AnnotatorGUI(tk.Tk):
    def __init__(self, initial_filter, initial_engine_path, initial_gametime):
        super().__init__()
        self.title("Annotator Configuratie")
        self.geometry("800x800")

        # Zorg voor een single-threaded executor om te voorkomen dat twee analyses tegelijkertijd draaien
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.analysis_future = None
        self.original_stdout = sys.stdout
        self.console_handler = None

        # --- DEFINIEER ENGINE MAPPING ---
        self.default_pgn_dir = os.path.join(os.path.expanduser("~"), "Schaken")

        # 1. Lijst van (Weergavenaam, Technisch Pad)
        self.engine_options: List[Tuple[str, str]] = [
            ("Stockfish (Standaard Engine Pad)", "/home/user/Schaken/stockfish-python/Python-Easy-Chess-GUI/Engines/stockfish-ubuntu-x86-64-avx2"),
            ("Koivisto 9.2 (Linux Engine)", "/home/user/Schaken/stockfish-python/Python-Easy-Chess-GUI/Engines/Koivisto_9.2-linux-pgo-native"),
            ("Leeg (Handmatig invoeren of bladeren)", ""),
        ]

        # 2. Map voor snelle lookup
        self.engine_map: Dict[str, str] = {name: path for name, path in self.engine_options}

        # 3. Alleen de weergavenamen voor de Combobox
        self.default_engine_display_names: List[str] = [name for name, path in self.engine_options]

        # Initialiseer variabelen
        initial_inputfile = ""
        self.default_filters = ["Geen", "Interesting", "Result:1-0", "Site:Wijk;Result:1-0,0-1;Title:GM", "Player:Carlsen;TimeControl:600+5"]

        self.inputfile_var = tk.StringVar(value=initial_inputfile)
        self.pgn_var = tk.StringVar()
        self.filter_var = tk.StringVar(value=initial_filter)
        self.gametime_var = tk.StringVar(value=str(initial_gametime))
        self._pgn_manually_set = False
        self.update_pgn_path(initial_setup=True)

        # 4. Engine State Variabelen
        # a) engine_var houdt de WEERGAVENAAM van de Combobox bij
        initial_display_name = self.default_engine_display_names[0] if self.default_engine_display_names else ""
        self.engine_var = tk.StringVar(value=initial_display_name)

        # b) _engine_path_var houdt het TECHNISCHE PAD bij (de echte waarde voor analyse)
        initial_path = self.engine_map.get(initial_display_name, initial_engine_path)
        self._engine_path_var = tk.StringVar(value=initial_path)

        self.create_widgets()
        self.inputfile_var.trace_add("write", self.update_pgn_path)
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    # --- Engine Selector Logica ---

    def on_engine_selected(self, event):
        """Wordt geactiveerd wanneer een optie in de combobox wordt geselecteerd."""
        selected_display_name = self.engine_var.get()

        # Zoek het technische pad op basis van de weergavenaam
        selected_path = self.engine_map.get(selected_display_name)

        if selected_path is not None:
            # Update de interne variabele met het technische pad
            self._engine_path_var.set(selected_path)
        else:
            # Dit gebeurt als de gebruiker handmatig typt of als er een onverwachte waarde is.
            # We gaan ervan uit dat de getypte waarde ZELF het technische pad is
            # en updaten de interne variabele ook.
            self._engine_path_var.set(selected_display_name)


    def browse_engine_file(self):
        """Vraagt om een engine-pad en werkt zowel de weergave als het pad bij."""
        engine_path = filedialog.askopenfilename(
            title="Selecteer de schaakengine (bijv. stockfish)",
            filetypes=[("Uitvoerbare bestanden", ("*.exe", "*")), ("Alle bestanden", "*.*")]
        )
        if engine_path:
            # 1. Update de interne variabele (de echte waarde)
            self._engine_path_var.set(engine_path)

            # 2. Creëer een nieuwe weergavenaam voor de Combobox
            display_name = f"Aangepast pad: {os.path.basename(engine_path)}"

            # 3. Update de Combobox met de nieuwe weergavenaam
            self.engine_var.set(display_name)

            # Optioneel: voeg de nieuwe optie toe aan de map en values
            if display_name not in self.engine_map:
                self.engine_map[display_name] = engine_path
                current_values = list(self.engine_combobox['values'])
                if display_name not in current_values:
                    current_values.append(display_name)
                    self.engine_combobox['values'] = current_values

    # --- Applicatie Logica ---

    def on_closing(self):
        """Stop de executor en sluit de applicatie af."""
        if self.analysis_future and self.analysis_future.running():
            logger.warning("Taak wordt afgebroken.")
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
            title="Sla het geannoteerde PGN-bestand op als..."
        )
        if filename:
            self.pgn_var.set(filename)

    def run_annotate_start(self):
        """Start de engine-analyse in een aparte thread en leidt de uitvoer om."""

        if self.analysis_future and self.analysis_future.running():
            self.status_var.set("Fout: Analyse is al bezig.")
            return

        inputfile_arg = self.inputfile_var.get()
        outputfile_arg = self.pgn_var.get()
        filter_arg = self.filter_var.get()

        # GEBRUIK HIER DE INTERNE PATH VARIABELE (de technische waarde)
        engine_arg = self._engine_path_var.get()

        gametime_str = self.gametime_var.get()

        if not inputfile_arg or not engine_arg or not gametime_str:
            # We controleren op de technische waarde!
            self.status_var.set("Fout: Vul alle verplichte velden in (Input, Engine Pad, Tijd).")
            return

        try:
            gametime_arg = float(gametime_str)
        except ValueError:
            self.status_var.set("Fout: Analyse Tijd moet een geldig nummer zijn.")
            return

        self.console_text.delete(1.0, tk.END)
        self.redirect_output_start()

        self.status_var.set(f"Engine-analyse gestart voor {extract_filename_from_inputfile(inputfile_arg)}... (Bezig)")
        self.start_button.config(state=tk.DISABLED)
        self.analyze_button.config(state=tk.DISABLED) # Schakel Analyse-knop uit

        self.analysis_future = self.executor.submit(
            run_annotate, inputfile_arg, engine_arg, gametime_arg, 8, filter_arg, outputfile_arg
        )

        self.after(100, lambda: self.check_analysis_status(outputfile_arg))

    # --- FUNCTIES VOOR PGN-ANALYSE ---

    def run_pgn_analysis(self):
        """Start de PGN-analyse (statistieken) in een aparte thread."""

        if self.analysis_future and self.analysis_future.running():
            self.status_var.set("Fout: Analyse is al bezig.")
            return

        inputfile_arg = self.inputfile_var.get()
        if not inputfile_arg:
            self.status_var.set("Fout: Selecteer eerst een Input Bestand/URL.")
            return

        self.console_text.delete(1.0, tk.END)
        self.redirect_output_start() # Zorgt ervoor dat output naar de console gaat

        self.status_var.set(f"PGN-analyse gestart voor {extract_filename_from_inputfile(inputfile_arg)}... (Bezig)")

        self.start_button.config(state=tk.DISABLED)
        self.analyze_button.config(state=tk.DISABLED)

        # De analyse wordt als een toekomstige taak ingestuurd
        # self.analysis_future = self.executor.submit(
        #     analyze_pgn_stats, inputfile_arg
        # )

        results = analyze_pgn_stats(inputfile_arg)

        if results:
            self.check_analysis_status_pgn(True)
            site_stats, event_stats = results

            root = tk.Tk()
            # Initialiseer de GUI met de geretourneerde data
            PGNStatsView(root, site_stats, event_stats)
            root.mainloop()
        else:
            self.check_analysis_status_pgn(False)
            # Toon een foutmelding als de analyse mislukt (bijv. geen data of foute lezing)
            messagebox.showerror("Fout", "PGN-analyse mislukt. Controleer de logging voor de reden.")


    def check_analysis_status_pgn(self, success):
            self.start_button.config(state=tk.NORMAL)
            self.analyze_button.config(state=tk.NORMAL)

            try:
                #success = self.analysis_future.result()
                if success:
                    self.status_var.set("✅ PGN-analyse voltooid. Zie Logboek voor statistieken.")
                else:
                    self.status_var.set("❌ PGN-analyse mislukt. Zie logboek voor details.")
            except Exception as e:
                self.status_var.set(f"❌ Er is een onverwachte fout opgetreden: {e}")
            finally:
                self.analysis_future = None
    # --- EINDE NIEUWE FUNCTIES VOOR PGN-ANALYSE ---


    def redirect_output_start(self):
        """Leidt sys.stdout en de logging handlers om naar het Text widget."""
        # Verwijder eerst eventuele bestaande handlers om duplicatie te voorkomen
        if self.console_handler:
             logging.getLogger().removeHandler(self.console_handler)

        self.console_handler = ConsoleRedirect(self.console_text)
        sys.stdout = self.console_handler # Leidt print() calls om
        logging.getLogger().addHandler(self.console_handler) # Leidt logger calls om


    def redirect_output_stop(self):
        """Herstelt sys.stdout en verwijdert de custom logging handler."""
        sys.stdout = self.original_stdout
        if self.console_handler:
            # We moeten de handler van de root logger verwijderen
            logging.getLogger().removeHandler(self.console_handler)
            self.console_handler = None


    def check_analysis_status(self, outputfile_arg):
        """Controleert of de annotatietaak in de aparte thread is voltooid."""
        if not self.analysis_future:
            return

        if self.analysis_future.running():
            self.after(100, lambda: self.check_analysis_status(outputfile_arg))
        else:
            self.redirect_output_stop()
            self.start_button.config(state=tk.NORMAL)
            self.analyze_button.config(state=tk.NORMAL) # Schakel Analyse-knop weer in

            try:
                success = self.analysis_future.result()
                if success:
                    self.status_var.set(f"✅ Engine-analyse voltooid. Games opgeslagen in {os.path.basename(outputfile_arg)}.")
                else:
                    self.status_var.set("❌ Engine-analyse mislukt. Zie logboek voor details.")
            except Exception as e:
                self.status_var.set(f"❌ Er is een onverwachte fout opgetreden: {e}")
            finally:
                self.analysis_future = None


    def create_widgets(self):
        # Configureer de grid layout
        self.columnconfigure(1, weight=1)
        self.rowconfigure(8, weight=1) # Nu rij 8 wegens de extra knoppenrij

        style = ttk.Style()
        style.configure("TLabel", padding=5, font=('Arial', 10))
        style.configure("TButton", padding=5, font=('Arial', 10, 'bold'))
        style.configure("TCombobox", padding=5, font=('Arial', 10))

        row_index = 0

        # --- Configuratie Velden ---

        # 1. INPUT FILE/URL Entry
        ttk.Label(self, text="Input Bestand/URL (-i):").grid(row=row_index, column=0, sticky="w", padx=10, pady=5)
        inputfile_entry = ttk.Entry(self, textvariable=self.inputfile_var, width=80)
        inputfile_entry.grid(row=row_index, column=1, sticky="ew", padx=10, pady=5)
        inputfile_entry.focus_set()
        row_index += 1

        # 2. PGN Entry (Output File)
        ttk.Label(self, text="PGN Uitvoerbestand (-o):").grid(row=row_index, column=0, sticky="w", padx=10, pady=5)
        pgn_entry = ttk.Entry(self, textvariable=self.pgn_var)
        pgn_entry.grid(row=row_index, column=1, sticky="ew", padx=10, pady=5)
        pgn_entry.bind('<Key>', self.set_pgn_manually_set)
        browse_button = ttk.Button(self, text="Bladeren Output...", command=self.browse_pgn_file)
        browse_button.grid(row=row_index, column=2, sticky="e", padx=(0, 10), pady=5)
        row_index += 1

        # 3. FILTER Combobox
        ttk.Label(self, text="Game Filter (-f):").grid(row=row_index, column=0, sticky="w", padx=10, pady=5)
        filter_combobox = ttk.Combobox(self, textvariable=self.filter_var, values=self.default_filters, width=80)
        filter_combobox.grid(row=row_index, column=1, sticky="ew", padx=10, pady=5)
        ttk.Label(self, text="Bijv: Player:Carlsen;Result:1-0").grid(row=row_index, column=2, sticky="w", padx=(0, 10), pady=5)
        row_index += 1

        # 4. ENGINE Combobox
        ttk.Label(self, text="Engine Pad (-e):").grid(row=row_index, column=0, sticky="w", padx=10, pady=5)

        self.engine_combobox = ttk.Combobox(self,
                                            textvariable=self.engine_var,
                                            values=self.default_engine_display_names, # Gebruik alleen de weergavenamen
                                            width=80)
        self.engine_combobox.grid(row=row_index, column=1, sticky="ew", padx=10, pady=5)

        self.engine_combobox.bind("<<ComboboxSelected>>", self.on_engine_selected)

        browse_engine_button = ttk.Button(self, text="Bladeren Engine...", command=self.browse_engine_file)
        browse_engine_button.grid(row=row_index, column=2, sticky="e", padx=(0, 10), pady=5)
        row_index += 1

        # 5. GAMETIME Entry
        ttk.Label(self, text="Analyse Tijd (-t) [sec]:").grid(row=row_index, column=0, sticky="w", padx=10, pady=5)
        gametime_entry = ttk.Entry(self, textvariable=self.gametime_var, width=10)
        gametime_entry.grid(row=row_index, column=1, sticky="w", padx=10, pady=5)
        row_index += 1

        # 6. Start- en Analyseknoppen (NIEUWE RIJ MET TWEE KNOPPEN)
        button_frame = ttk.Frame(self)
        button_frame.grid(row=row_index, column=0, columnspan=3, sticky="ew", padx=10, pady=15)
        button_frame.columnconfigure(0, weight=1) # Voor Start Knop
        button_frame.columnconfigure(1, weight=1) # Voor Analyse Knop

        # Start Knop (Engine Analyse)
        self.start_button = ttk.Button(button_frame, text="Start Analyse (Engine)", command=self.run_annotate_start)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 5))

        # NIEUWE KNOP (Statistieken Analyse)
        self.analyze_button = ttk.Button(button_frame, text="Analyse PGN (Statistieken)", command=self.run_pgn_analysis)
        self.analyze_button.grid(row=0, column=1, sticky="ew", padx=(5, 0))

        row_index += 1

        # 7. Statuslabel
        self.status_var = tk.StringVar(value="Wacht op configuratie of druk op Start.")
        status_label = ttk.Label(self, textvariable=self.status_var, relief=tk.SUNKEN, anchor="w")
        status_label.grid(row=row_index, column=0, columnspan=3, sticky="ew", padx=10, pady=(5, 10), ipady=5)
        row_index += 1

        # --- Console Output ---

        # 8. Console Label
        ttk.Label(self, text="Analyse Logboek:").grid(row=row_index, column=0, sticky="w", padx=10, pady=5)
        row_index += 1

        # 9. Console Text Widget met Scrollbar (Frame voor layout)
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

def setup_logging(args):
    """
    Sets logging module verbosity according to runtime arguments
    """
    if args.verbose:
        if args.verbose >= 3:
            # EVERYTHING TO LOG FILE
            logger.setLevel(logging.DEBUG)
            hldr = logging.FileHandler('annotator.log')
            logger.addHandler(hldr)
        elif args.verbose == 2:
            # DEBUG TO STDERR
            logger.setLevel(logging.DEBUG)
        elif args.verbose == 1:
            # INFO TO STDERR
            logger.setLevel(logging.INFO)


# Definieer een score die altijd groter is dan elke centipawn-evaluatie,
# maar kleiner dan een werkelijke matscore.
# Dit is de 'magische' grens die wordt gebruikt om DTM te vertalen naar CP.
MAX_CP_SCORE = 20000

def eval_numeric(result: chess.engine.AnalysisResult, board_turn: chess.Color) -> int:
    """
    Vertaalt het resultaat van engine.analyse() naar een universele numerieke score
    (centipionnen) vanuit het perspectief van de speler die aan zet is.

    - Als de engine Mat in N vindt (DTM), wordt dit geconverteerd naar een
      numerieke score met behulp van MAX_CP_SCORE.
    - Anders wordt de centipawn (CP) waarde geretourneerd.

    Args:
        result: Het resultaat van await engine.analyse().
        board_turn: De kleur van de speler wiens evaluatie we willen (True voor Wit, False voor Zwart).

    Returns:
        De numerieke evaluatie in centipionnen.
    """

    # 1. Haal de score op. We gebruiken .pov(board_turn) om de score altijd
    #    vanuit het perspectief van de speler die aan zet is te krijgen.
    score = result.get("score").pov(board_turn)

    if score.is_mate():
        dtm = score.mate()

        if dtm > 0:
            # Winst in N zetten (bijv. Mat in 3): hoe kleiner N, hoe hoger de score.
            # (MAX_CP_SCORE - 1) is beter dan 10000 CP.
            return MAX_CP_SCORE - abs(dtm)
        elif dtm < 0:
            # Verlies in N zetten (bijv. Gemateerd in 3): hoe kleiner N, hoe lager de score.
            # (-MAX_CP_SCORE - 1) is slechter dan -10000 CP.
            return -(MAX_CP_SCORE - abs(dtm))
        else:
            # Zou niet moeten gebeuren tenzij er al Mat is, maar voor de zekerheid.
            return 0

    elif score.cp is not None:
        # Als we een normale centipawn score hebben (geen DTM), retourneer deze.
        return score.cp

    # Afhandeling voor onverwachte gevallen (bijv. engine geeft geen score)
    raise RuntimeError("Engine evaluation result was unintelligible or missing score.")

def eval_human(white_to_move: chess.Color, result: chess.engine.AnalysisResult) -> str:
    """
    Returns a human-readable evaluation of the position:
        - If depth-to-mate was found, return plain-text mate announcement (e.g. "Mate in 4")
        - If depth-to-mate was not found, return an absolute numeric evaluation (e.g. "+1.50")

    Args:
        white_to_move: De kleur van de speler wiens beurt het was (de speler NA wie de zet wordt geëvalueerd).
                       Dit is cruciaal voor de absolute evaluatie.
        result: Het resultaat van await engine.analyse() na de zet.

    Returns:
        Een menselijk leesbare string.
    """

    # 1. Haal de score op
    score = result.get("score")

    if score is None:
        return "Geen score beschikbaar"

    # Gebruik .pov(white_to_move) om de score te krijgen vanuit het perspectief
    # van de speler die de zet deed (in de 'judge_move' functie is dit de speler
    # die net de zet heeft uitgevoerd, of de speler die aan zet is).
    # Voor de annotatie willen we de ABSOLUTE score vanuit het oogpunt van Wit.

    # De score in het resultaat is altijd POV van de speler die de beurt had
    # TOEN de analyse begon (dus de huidige board.turn).
    # We converteren deze naar het perspectief van WIT (chess.WHITE).
    score_for_white = score.pov(chess.WHITE)

    if score_for_white.is_mate():
        dtm = score_for_white.mate()
        # dtm is het aantal zetten tot mat. abs(dtm) is het aantal *plies*.
        # De engine geeft de score als plies, dus delen door 2 voor zetten.
        moves_to_mate = abs(dtm) / 2

        # Geef de naam van de winnende kleur
        winning_color = "Wit" if dtm > 0 else "Zwart"

        # Aangezien DTM in plies is, zorgen we dat we hele nummers tot mat tonen.
        # Een mat in 1 (2 plies) is M2, dus '1 zet'.
        if moves_to_mate >= 1:
            return f"Mat in {int(moves_to_mate)}"
        else:
             # Dit zou Mat in 0 betekenen (wat al gebeurd is)
             return "Mat"

    elif score_for_white.cp is not None:
        # We hebben een centipawn score, converteer naar pionnen (gedeeld door 100)
        score_in_pawns = score_for_white.cp / 100

        # Gebruik de absolute evaluatie (deze is al vanuit het oogpunt van Wit,
        # dus we hoeven alleen te formatteren).

        # Voeg het + teken toe voor een duidelijke weergave van voordeel voor wit
        format_string = '{:+.2f}'
        return format_string.format(score_in_pawns)

    # Als de engine een resultaat retourneert zonder score
    raise RuntimeError("Engine evaluation result was unintelligible or missing score.")


def eval_absolute(number, white_to_move):
    """
    Accepts a relative evaluation (from the point of view of the player to
    move) and returns an absolute evaluation (from the point of view of white)
    """

    return number if white_to_move else -number


def winning_chances(centipawns):
    """
    Takes an evaluation in centipawns and returns an integer value estimating
    the chance the player to move will win the game

    winning chances = 50 + 50 * (2 / (1 + e^(-0.004 * centipawns)) - 1)
    """
    return 50 + 50 * (2 / (1 + math.exp(-0.004 * centipawns)) - 1)


def needs_annotation(judgment):
    """
    Returns a boolean indicating whether a node with the given evaluations
    should have an annotation added
    """
    if not (judgment and "besteval" in judgment and "playedeval" in judgment):
        return False
    best = winning_chances(int(judgment["besteval"]))
    played = winning_chances(int(judgment["playedeval"]))
    delta = abs(best - played)

    return delta > NEEDS_ANNOTATION_THRESHOLD or best > played


async def judge_move(board: chess.Board, played_move: chess.Move, engine: chess.engine.UciProtocol, searchtime_s: float):
    """
    Evaluate the strength of a given move by comparing it to engine's best
    move and evaluation at a given depth, in a given board context.

    Returns a judgment dictionary.
    """

    # De engine.analyse() methode stelt de FEN automatisch in via het 'board' argument.
    analysis_limit = chess.engine.Limit(time=searchtime_s / 2)
    judgment = {}

    # --- DE ONJUISTE REGEL IS VERWIJDERD ---
    # await engine.set_fen(board.fen())
    # -------------------------------------

    # Eerste analyse: Bepaal de beste zet en de evaluatie vóór de gespeelde zet
    # =========================================================================
    try:
        # De 'board' parameter zorgt ervoor dat de engine gesynchroniseerd wordt
        best_move_result = await engine.analyse(
            board,
            limit=analysis_limit,
            info=chess.engine.Info(chess.engine.Info.ALL) # Vraag alle info op
        )
    except chess.engine.EngineTerminatedError:
        # Foutafhandeling voor als de engine plots stopt
        return {"error": "Engine terminated during analysis"}

    # Valideer dat de engine een zet en score heeft gevonden
    if not best_move_result.get("pv"):
          return {"error": "Engine found no primary variation (PV)"}


    # Vul het 'bestmove' deel van de 'judgment'
    judgment["bestmove"] = best_move_result.get("pv")[0]
    judgment["besteval"] = eval_numeric(best_move_result, board.turn)
    judgment["pv"] = best_move_result.get("pv")
    judgment["depth"] = best_move_result.get("depth")
    judgment["nodes"] = best_move_result.get("nodes")
    # Annotate the best move
    judgment["bestcomment"] = eval_human(board.turn, best_move_result)

    # Tweede analyse: Evaluatie van de gespeelde zet
    # =========================================================================

    # Als de gespeelde zet de beste zet is, hoeven we niet opnieuw te analyseren
    if played_move == judgment["bestmove"]:
        judgment["playedeval"] = judgment["besteval"]
    else:
        # Maak een kopie van het bord en speel de zet
        temp_board = board.copy()
        temp_board.push(played_move)

        # Voer de analyse uit op de NÍEUWE positie (na de gespeelde zet)
        played_move_result = await engine.analyse(
            temp_board, # Dit stelt de engine in op de positie NA de zet
            limit=analysis_limit,
            info=chess.engine.Info(chess.engine.Info.SCORE)
        )

        judgment["playedeval"] = eval_numeric(played_move_result, temp_board.turn)


    # Annotate the played move
    # Gebruik de resultaten van de tweede analyse (of de eerste als de zet de beste was)
    result_to_comment = played_move_result if played_move != judgment["bestmove"] else best_move_result
    judgment["playedcomment"] = eval_human(board.turn, result_to_comment)

    return judgment


def get_nags(judgment):
    """
    Returns a Numeric Annotation Glyph (NAG) according to how much worse the
    played move was vs the best move
    """
    if "besteval" in judgment and "playedeval" in judgment:
        delta = judgment["playedeval"] - judgment["besteval"]

        if delta < ERROR_THRESHOLD["BLUNDER"]:
            #print("blunder:", chess.pgn.NAG_BLUNDER)
            return [chess.pgn.NAG_BLUNDER]
        elif delta < ERROR_THRESHOLD["MISTAKE"]:
            #print("MISTAKE:", chess.pgn.NAG_MISTAKE)
            return [chess.pgn.NAG_MISTAKE]
        elif delta < ERROR_THRESHOLD["DUBIOUS"]:
            return [chess.pgn.NAG_DUBIOUS_MOVE]
        elif judgment["playedeval"] > judgment["besteval"]+0.5:
            return [9]
        elif judgment["playedeval"] > judgment["besteval"]:
            return [7]
        else:
            return []
    else:
       return []


def var_end_comment(board, judgment):
    """
    Return a human-readable annotation explaining the board state (if the game
    is over) or a numerical evaluation (if it is not)
    """
    score = judgment["bestcomment"]
    depth = judgment["depth"]

    if board.is_stalemate():
        return "Stalemate"
    elif board.is_insufficient_material():
        return "Insufficient material to mate"
    elif board.can_claim_fifty_moves():
        return "Fifty move rule"
    elif board.can_claim_threefold_repetition():
        return "Three-fold repetition"
    elif board.is_checkmate():
        # checkmate speaks for itself
        return ""
    return "{}/{}".format(str(score), str(depth))


def truncate_pv(board, pv):
    """
    If the pv ends the game, return the full pv
    Otherwise, return the pv truncated to 10 half-moves
    """

    for move in pv:
        if not board.is_legal(move):
            raise AssertionError
        board.push(move)

    if board.is_game_over(claim_draw=True):
        return pv
    else:
        return pv[:SHORT_PV_LEN]


def add_annotation(node, judgment):
    """
    Add evaluations and the engine's primary variation as annotations to a node
    """
    prev_node = node.parent

    # Add the engine evaluation
    if judgment["bestmove"] != node.move:
        node.comment = judgment["playedcomment"]

    # Get the engine primary variation
    variation = truncate_pv(prev_node.board(), judgment["pv"])

    # Add the engine's primary variation as an annotation
    prev_node.add_line(moves=variation)

    # Add a comment to the end of the variation explaining the game state
    var_end_node = prev_node.variation(judgment["pv"][0]).end()
    var_end_node.comment = var_end_comment(var_end_node.board(), judgment)

    # Add a Numeric Annotation Glyph (NAG) according to how weak the played
    # move was
    node.nags = get_nags(judgment)


def classify_fen(fen, ecodb):
    """
    Searches a JSON file with Encyclopedia of Chess Openings (ECO) data to
    check if the given FEN matches an existing opening record

    Returns a classification

    A classfication is a dictionary containing the following elements:
        "code":         The ECO code of the matched opening
        "desc":         The long description of the matched opening
        "path":         The main variation of the opening
    """
    classification = {}
    classification["code"] = ""
    classification["desc"] = ""
    classification["path"] = ""

    for opening in ecodb:
        if opening['f'] == fen:
            classification["code"] = opening['c']
            classification["desc"] = opening['n']
            classification["path"] = opening['m']

    return classification


def eco_fen(board):
    """
    Takes a board position and returns a FEN string formatted for matching with
    eco.json
    """
    board_fen = board.board_fen()
    castling_fen = board.castling_xfen()

    to_move = 'w' if board.turn else 'b'

    return "{} {} {}".format(board_fen, to_move, castling_fen)


def debug_print(node, judgment):
    """
    Prints some debugging info about a position that was just analyzed
    """

    logger.debug(node.board())
    logger.debug(node.board().fen())
    logger.debug("Played move: %s", format(node.parent.board().san(node.move)))
    logger.debug("Best move: %s",
                 format(node.parent.board().san(judgment["bestmove"])))
    logger.debug("Best eval: %s", format(judgment["besteval"]))
    logger.debug("Best comment: %s", format(judgment["bestcomment"]))
    logger.debug("PV: %s",
                 format(node.parent.board().variation_san(judgment["pv"])))
    logger.debug("Played eval: %s", format(judgment["playedeval"]))
    logger.debug("Played comment: %s", format(judgment["playedcomment"]))
    logger.debug("Delta: %s",
                 format(judgment["besteval"] - judgment["playedeval"]))
    logger.debug("Depth: %s", format(judgment["depth"]))
    logger.debug("Nodes: %s", format(judgment["nodes"]))
    logger.debug("Needs annotation: %s", format(needs_annotation(judgment)))
    logger.debug("")


def cpl(string):
    """
    Centipawn Loss
    Takes a string and returns an integer representing centipawn loss of the
    move We put a ceiling on this value so that big blunders don't skew the
    acpl too much
    """

    cpl = int(string)

    return min(cpl, MAX_CPL)


def acpl(cpl_list):
    """
    Average Centipawn Loss
    Takes a list of integers and returns an average of the list contents
    """
    try:
        return sum(cpl_list) / len(cpl_list)
    except ZeroDivisionError:
        return 0


def clean_game(game):
    """
    Takes a game and strips all comments and variations, returning the
    "cleaned" game
    """
    node = game.end()

    while True:
        prev_node = node.parent

        node.comment = None
        node.nags = []
        for variation in reversed(node.variations):
            if not variation.is_main_variation():
                node.remove_variation(variation)

        if node == game.root():
            break

        node = prev_node

    return node.root()


def game_length(game):
    """
    Takes a game and returns an integer corresponding to the number of
    half-moves in the game
    """
    ply_count = 0
    node = game.end()

    while not node == game.root():
        node = node.parent
        ply_count += 1

    return ply_count


def classify_opening(game):
    """
    Takes a game and adds an ECO code classification for the opening
    Returns the classified game and root_node, which is the node where the
    classification was made
    """
    ecopath = os.path.join(os.path.dirname(__file__), 'eco/eco.json')
    with open(ecopath, 'r') as ecofile:
        ecodata = json.load(ecofile)

        ply_count = 0

        root_node = game.root()
        node = game.end()

        # Opening classification for variant games is not implemented (yet?)
        is_960 = root_node.board().chess960
        if is_960:
            variant = "chess960"
        else:
            variant = type(node.board()).uci_variant

        if variant != "chess":
            logger.info("Skipping opening classification in variant "
                        "game: {}".format(variant))
            return node.root(), root_node, game_length(game)

        logger.info("Classifying the opening for non-variant {} "
                    "game...".format(variant))

        while not node == game.root():
            prev_node = node.parent

            fen = eco_fen(node.board())
            classification = classify_fen(fen, ecodata)

            if classification["code"] != "":
                # Add some comments classifying the opening
                node.root().headers["ECO"] = classification["code"]
                node.root().headers["Opening"] = classification["desc"]
                node.comment = "{} {}".format(classification["code"],
                                              classification["desc"])
                # Remember this position so we don't analyze the moves
                # preceding it later
                root_node = node
                # Break (don't classify previous positions)
                break

            ply_count += 1
            node = prev_node

        return node.root(), root_node, ply_count


def add_acpl(game, root_node):
    """
    Takes a game and a root node, and adds PGN headers with the computed ACPL
    (average centipawn loss) for each player. Returns a game with the added
    headers.
    """
    white_cpl = []
    black_cpl = []

    node = game.end()
    while not node == root_node:
        prev_node = node.parent

        judgment = node.comment
        if judgment and "besteval" in judgment and "playedeval" in judgment:
            delta = judgment["besteval"] - judgment["playedeval"]

            if node.board().turn:
                black_cpl.append(cpl(delta))
            else:
                white_cpl.append(cpl(delta))

        node = prev_node

    node.root().headers["WhiteACPL"] = str(round(acpl(white_cpl)))
    node.root().headers["BlackACPL"] = str(round(acpl(black_cpl)))

    return node.root()


def get_total_budget(arg_gametime):
    return float(arg_gametime) * 60


def get_pass1_budget(total_budget):
    return total_budget / 10


def get_pass2_budget(total_budget, pass1_budget):
    return total_budget - pass1_budget


def get_time_per_move(pass_budget, ply_count):
    try:
        count_ = float(pass_budget) / float(ply_count)
    except:
        count_ = 60
    return count_


async def analyze_game(game, arg_gametime, engine, threads):
    """
    Take a PGN game and return a GameNode with engine analysis added
    ...
    """

    # First, check the game for PGN parsing errors
    if not checkgame(game):
        return


    # ... (rest van de initialisatie logic) ...

    # Start keeping track of the root node
    root_node = game.end()
    node = root_node

    # Clear existing comments and variations
    game = clean_game(game)

    # Attempt to classify the opening and calculate the game length
    game, root_node, ply_count = classify_opening(game)

    ###########################################################################
    # Perform game analysis (Pass 1)
    ###########################################################################

    budget = get_total_budget(arg_gametime)
    pass1_budget = get_pass1_budget(budget)
    time_per_move = get_time_per_move(pass1_budget, ply_count)

    logger.debug("Pass 1 budget is %i seconds, with %f seconds per move",
                 pass1_budget, time_per_move)
    logger.info("Performing first pass...")
    try:
        error_count = 0
        node = game.end()
        while not node == root_node:
            prev_node = node.parent

            try:
                # WIJZIGING 6: judge_move moet nu AWAIT gebruiken en info_handler VERWIJDERD
                judgment = await judge_move(prev_node.board(), node.move, engine, time_per_move)

                # Record the delta, to be referenced in the second pass
                node.comment = judgment

                # Count the number of mistakes that will have to be annotated later
                if needs_annotation(judgment):
                    error_count += 1

                # Print some debugging info
                debug_print(node, judgment)
            except chess.engine.EngineError as e:
                # Log de fout netjes in uw eigen applicatie.
                move_uci = node.move.uci()
                board_fen = prev_node.board().fen()
                logger.warning(f"EngineError voor zet {move_uci} op FEN {board_fen}. Fout: {e}")

                # U kunt hier beslissen of u de zet overslaat (zoals u deed met 'pass'),
                # of een standaard 'judgment' toewijst.
                node.comment = "Overslagen wegens engine fout."

            except Exception as e:
                # Vang andere onverwachte fouten op die niet van de engine komen
                logger.error(f"Onverwachte fout tijdens analyse: {e}")
                return

            node = prev_node

        # Calculate the average centipawn loss (ACPL) for each player
        game = add_acpl(game, root_node)
    except Exception as e:
        print(f"Fatale fout tijdens de analyse: {e}")
        return

    ###########################################################################
    # Perform game analysis (Pass 2)
    ###########################################################################

    pass2_budget = get_pass2_budget(budget, pass1_budget)

    # ... (logica voor het bepalen van time_per_move in pass 2) ...
    # Vereenvoudigd:
    try:
        time_per_move = pass2_budget / error_count
    except ZeroDivisionError:
        # ... (error handling voor geen fouten) ...
        pass


    logger.debug("Pass 2 budget is %i seconds, with %f seconds per move",
                 pass2_budget, time_per_move)
    logger.info("Performing second pass...")

    node = game.end()
    while not node == root_node:
        prev_node = node.parent

        judgment = node.comment

        if needs_annotation(judgment):
            # WIJZIGING 6: judge_move moet nu AWAIT gebruiken en info_handler VERWIJDERD
            judgment = await judge_move(prev_node.board(), node.move, engine, time_per_move)

            # Verify that the engine still dislikes the played move
            if needs_annotation(judgment):
                add_annotation(node, judgment)
            else:
                node.comment = None

            # Print some debugging info
            debug_print(node, judgment)
        else:
            node.comment = None

        node = prev_node

    # Toegang tot de identificatiegegevens (.id is een dictionary)
    engine_id = engine.id

    # Haal de belangrijkste naam op (bijv. "Stockfish 17")
    engine_name = engine_id.get("name", "Niet gevonden")

    node.root().comment = engine_name
    node.root().headers["Annotator"] = engine_name

    return change_nags(node.root())

def checkgame(game):
    """
    Check for PGN parsing errors and abort if any were found
    This prevents us from burning up CPU time on nonsense positions
    """
    if game.errors:
        errormsg = "There were errors parsing the PGN game:"
        logger.critical(errormsg)
        for error in game.errors:
            logger.critical(error)
        logger.critical("Aborting...")
        return False

    # Try to verify that the PGN file was readable
    if game.end().parent is None:
        errormsg = "Could not render the board. Is the file legal PGN?" \
            "Aborting..."
        logger.critical(errormsg)
        return False
    return True

def change_nags(pgn):
    """"
    blunder: 4 MISTAKE: 2 DUBIOUS: 6"""
    pgn = str(pgn)
    # pgn = pgn.replace("$6 {", "{Dubious ")
    # pgn = pgn.replace("$2 {", "{Mistake ")
    # pgn = pgn.replace("$4 {", "{Blunder ")
    # pgn = pgn.replace("$7 {", "{Good ")
    # pgn = pgn.replace("$9 {", "{Brilliant ")
    strs = pgn.replace("  ", " ").split("\n")
    res = []
    res.append(strs.pop(0))
    for line in strs:
        if len(line) < 80 or line.startswith("["):
            res.append(line)
        else:
            line_strs = line.split(" ")
            hl = ""
            for word in line_strs:
                if len(hl) + len(word) < 80 or word == "}" or word == ")":
                    sep = " "
                    if len(hl) == 0:
                        sep = ""
                    hl = hl + sep + word
                else:
                    res.append(hl)
                    hl = word
            if len(hl) > 0:
                res.append(hl)
    pgn = "\n".join(res)
    return pgn
def start_analise(pgnfile, engine_path, fine_name_file, add_to_library, gui, save_file=True, num_threads=2):
    return asyncio.run(start_analise_async(pgnfile, engine_path, fine_name_file, add_to_library, gui, save_file, num_threads))

async def start_analise_async(pgnfile, engine_path, fine_name_file, add_to_library, gui, save_file=True, num_threads=2):
    engine = await get_engine(engine_path, num_threads)

    analyzed_game = ""

    fine_name_file = os.path.join(gui.default_png_dir, fine_name_file)

    with open(pgnfile) as pgn:
        for item in pgn_text_iterator(pgnfile):
            pgn_io = io.StringIO(item.strip())
            chess_game = chess.pgn.read_game(pgn_io)
            try:
                analyzed_game = await analyze_game(chess_game, 1,
                                            engine, num_threads)

            except KeyboardInterrupt:
                logger.critical("\nReceived KeyboardInterrupt.")
                raise
            except Exception as e:
                logger.critical("\nAn unhandled exception occurred: {}".format(type(e)))
                raise e
            else:
                if not save_file:
                    return analyzed_game

                new_filename = pgnfile[:-4] + "-annotated.pgn"
                annotated_content = str(analyzed_game)

                # Schrijf naar de bestanden
                if not add_to_library:
                    # Bestand 1: annotated_game.pgn
                    with open(os.path.join(gui.preferences.preferences["default_png_dir"], new_filename), 'w') as file1:
                        file1.writelines(annotated_content)
                    # Bestand 2: fine_name_file
                    with open(fine_name_file, 'w') as file2:
                        file2.writelines(annotated_content)

                if add_to_library:
                    # Voeg toe aan library.pgn
                    with open(os.path.join(gui.default_png_dir, "library.pgn"), 'a') as file3:
                        file3.writelines('\n\n' + annotated_content)

    # --- 3. CLEANUP (Optioneel maar Aangeraden) ---
    # Sluit de event loop af
    if loop.is_running():
        loop.stop()
    if not loop.is_closed():
        loop.close()
    engine.quit()

    return analyzed_game

def pgn_text_iterator(filepath: str) -> Iterator[str]:
    """
    Leest een groot tekstbestand en itereert over items die gescheiden zijn
    door een regel die begint met '[Event'.

    Deze functie is een generator: hij leest het bestand niet in zijn geheel
    in het geheugen, wat essentieel is voor zeer grote bestanden.

    Args:
        filepath: Het pad naar het PGN-achtige tekstbestand.

    Yields:
        Een string die één compleet item (game) bevat.
    """
    current_item_lines = []

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                stripped_line = line.strip()

                # Controleer of de regel de start van een nieuw item aangeeft
                if stripped_line.startswith('[Event '):
                    # Als de buffer niet leeg is, is het vorige item compleet.
                    if current_item_lines:
                        # De buffer samenvoegen en opleveren (yield)
                        yield "".join(current_item_lines).strip()

                        # Buffer legen en het nieuwe item (de [Event-regel) toevoegen
                        current_item_lines = [line]
                    else:
                        # Dit is de eerste regel van het bestand
                        current_item_lines.append(line)
                else:
                    # Voeg de regel toe aan het huidige item
                    current_item_lines.append(line)

            # Na de lus: het allerlaatste verzamelde item opleveren
            if current_item_lines:
                yield "".join(current_item_lines).strip()

    except FileNotFoundError:
        print(f"Fout: Bestand niet gevonden op '{filepath}'")
    except Exception as e:
        print(f"Er is een onverwachte fout opgetreden tijdens het lezen: {e}")

async def get_engine(enginepath, threads):
    engine_name = ""

    ###########################################################################
    # Initialize the engine
    ###########################################################################

    try:
        # WIJZIGING 2: Bewaar het transport-object globaal
        engine_transport, engine = await chess.engine.popen_uci(enginepath)
        await engine.configure({
            "Threads": threads
        })
        previous_enginepath = enginepath
    except FileNotFoundError:
        errormsg = "Engine '{}' was not found. Aborting...".format(enginepath)
        logger.critical(errormsg)
        raise
    except PermissionError:
        errormsg = "Engine '{}' could not be executed. Aborting...".format(
            enginepath)
        logger.critical(errormsg)
        raise

    return engine

# --- HOOFD EXECUTIE PUNT (Synchronous Wrapper) ---

def run_annotate(pgnfile: str, enginepath: str, gametime: int, threads: int, filter_str: str, outputfile: str):
    """Synchronous wrapper om de async functie aan te roepen."""
    # Dit is het enige punt waar asyncio.run() moet worden gebruikt.
    try:
        asyncio.run(run_annotate_async(pgnfile, enginepath, gametime, threads, filter_str, outputfile))
        return True # Succes
    except KeyboardInterrupt:
        logger.critical("Process afgebroken door gebruiker (KeyboardInterrupt).")
    except Exception as e:
        logger.critical(f"FATALE FOUT: {e}")
        return False

def valid_engine(engine_path):
    if engine_path == 'Niet Gespecificeerd' or engine_path == '':
                        return False
    else:
        return True

async def run_annotate_async(pgnfile, engine_path, gametime,threads, filter_str, outputfile):
    try:
        if valid_engine(engine_path):
            engine = await get_engine(engine_path, threads)
        processed_count = 0
        filtered_count = 0
        new_filename = outputfile
        if outputfile == "":
            new_filename = pgnfile[:-4] + "-annotated.pgn"
        file1 = open(new_filename, 'w')
        file1.close()

        for item in pgn_text_iterator(pgnfile):
            pgn_io = io.StringIO(item.strip())
            chess_game = chess.pgn.read_game(pgn_io)

            white_player = chess_game.headers.get('White', 'Onbekend')
            black_player = chess_game.headers.get('Black', 'Onbekend')
            event_name = chess_game.headers.get('Event', 'Onbekend Evenement')
            processed_count += 1

            # TOEPASSEN VAN HET FILTER
            if filter_str and not matches_filter(chess_game, filter_str):
                filtered_count += 1
                continue # Ga naar de volgende game

            # --- VOORTGANGSBERICHT ALLEEN VOOR GESELECTEERDE GAMES ---
            print(f"\n--- Game verwerkt (Filter OK): {white_player} vs {black_player} ({event_name}) ---")

            try:
                if valid_engine(engine_path):
                    analyzed_game = await analyze_game(chess_game, gametime,
                                            engine, threads)
            except KeyboardInterrupt:
                logger.critical("\nReceived KeyboardInterrupt.")
                raise
            except Exception as e:
                logger.critical("\nAn unhandled exception occurred: {}"
                                .format(type(e)))
                raise e
            else:
                print(analyzed_game, '\n')
                file1 = open(new_filename, 'a')
                file1.writelines(str(analyzed_game))
                #write one empty line to file1
                file1.write('\n\n')
                file1.close()
        if valid_engine(engine_path):
            await engine.quit()

        if processed_count > 1:
            print(f"\n--- Resultaten ---")
            print(f"Totaal {processed_count} games gevonden in de bron.")
            print(f"Games overgeslagen door filter: {filtered_count}")
            print(f"Games verwerkt en opgeslagen: {processed_count - filtered_count}")

    except PermissionError:
        errormsg = "Input file not readable. Aborting..."
        logger.critical(errormsg)
        raise

def main():
    """
    Main function

    - Load games from the PGN file
    - Annotate each game, and print the game with the annotations
    """
    args = parse_args()
    setup_logging(args)
    # engine = args.engine.split()
    gui = args.gui
    outputfile = args.outputfile
    if args.filter:
        filter_str = args.filter
    else:
        filter_str =  "Geen"
    engine = args.engine # Nieuw
    gametime = args.gametime # Nieuw
    threads = args.threads


    pgnfile = args.file
    if gui: # Start de GUI als de vlag is ingesteld
        # Geef de CLI-argumenten door aan de GUI om de startwaarden in te vullen
        app = AnnotatorGUI(filter_str, engine, gametime)
        app.mainloop()
    else:
        run_annotate(pgnfile, engine, gametime, threads, filter_str, outputfile)


if __name__ == "__main__":
    main()

# vim: ft=python expandtab smarttab shiftwidth=4 softtabstop=4
