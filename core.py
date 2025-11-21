 
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
import json
from pathlib import Path

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

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILENAME = "settings/configuration.json"
CONFIG_FILE_PATH = BASE_DIR / CONFIG_FILENAME
def _load_config() -> Dict[str, Any]:
    """
    Loads configuration from the JSON file.
    Provides robust error handling for missing files or invalid JSON.
    """
    print(f"Attempting to load configuration from: {CONFIG_FILE_PATH}")

    if not CONFIG_FILE_PATH.exists():
        print(f"Error: Configuration file not found at {CONFIG_FILE_PATH}. Using empty/default values.")
        # Return an empty dictionary to prevent the program from crashing
        return {}

    try:
        with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
        print("Configuration successfully loaded.", config)
        return config

    except json.JSONDecodeError as e:
        print(f"Error parsing JSON in {CONFIG_FILE_PATH}: {e}. Using empty/default values.")
        return {}
    except Exception as e:
        print(f"Unexpected error loading configuration: {e}. Using empty/default values.")
        return {}


# Function to extract the filename from the URL/Path
def extract_filename_from_inputfile(input_path: str) -> str:
    """
    Extracts the filename (the last path segment) from a URL or local path.
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

        # Ensures that .pgn is not duplicated in the name
        if filename.lower().endswith(".pgn"):
            filename = filename[:-4]

        return filename

    except Exception:
        return "default_game"

def matches_filter(game: chess.pgn.Game, filter_string: str) -> bool:
    """
    Checks if a game's metadata meets the filter criteria.
    """
    if not filter_string or filter_string == "Geen":
        return True

    # --- IMPLEMENTATION OF 'INTERESTING' FILTER ---
    if filter_string == "Interesting":
        # A game is considered "Interesting" if:
        # 1. The result is not a draw.
        # 2. AND (Both players >= HIGH_RATING) OR (A player with >= HIGH_RATING loses).

        HIGH_RATING = 2650 # Threshold value for 'high rating'

        # 1. Check for Draw
        result = game.headers.get("Result")
        if result == "1/2-1/2" or result is None:
            return False # Game is a draw or has no result

        # 2. Retrieve Ratings (safe parsing)
        try:
            white_rating = int(game.headers.get("WhiteElo", 0))
        except ValueError:
            white_rating = 0

        try:
            black_rating = int(game.headers.get("BlackElo", 0))
        except ValueError:
            black_rating = 0

        # --- Rating Criteria Evaluation ---

        is_high_rated_white = white_rating >= HIGH_RATING
        is_high_rated_black = black_rating >= HIGH_RATING

        # A. Condition met: two high-rated players?
        if is_high_rated_white and is_high_rated_black:
            return True

        # B. Condition met: a high-rated player loses?
        high_rated_lost = False

        # White won (1-0), check if high-rated Black lost
        if result == "1-0" and is_high_rated_black and not is_high_rated_white:
            # Only interesting if high-rated Black loses to a lower rating
            high_rated_lost = True

        # Black won (0-1), check if high-rated White lost
        elif result == "0-1" and is_high_rated_white and not is_high_rated_black:
            # Only interesting if high-rated White loses to a lower rating
            high_rated_lost = True

        if high_rated_lost:
            return True

        # Does not meet the 'Interesting' criteria
        return False
    # --- END OF 'INTERESTING' FILTER ---

    filters = [f.strip() for f in filter_string.split(';') if f.strip()]

    for filter_item in filters:
        if ':' not in filter_item:
            print(f"Warning: Invalid filter format '{filter_item}'. Must be 'Key:Value'.")
            continue

        key, value_str = [p.strip() for p in filter_item.split(':', 1)]
        values = [v.strip() for v in value_str.split(',') if v.strip()]

        passed_condition = False

        # 1. Special case: Player (searches in White AND Black)
        if key.lower() == 'player':
            for val in values:
                white_player = game.headers.get("White", "")
                black_player = game.headers.get("Black", "")
                if val.lower() in white_player.lower() or val.lower() in black_player.lower():
                    passed_condition = True
                    break

        # 2. Special case: Title (searches in WhiteTitle AND BlackTitle)
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
        # 3. General case: Standard header match (e.g., Event, Site, Result)
        else:
            header_value = game.headers.get(key, "")

            # Substring match
            if key.lower() not in ['result', 'round', 'date', 'eco']:
                for val in values:
                    if val.lower() in header_value.lower():
                        passed_condition = True
                        break
            # Exact match
            else:
                for val in values:
                    if val.lower() == header_value.lower():
                        passed_condition = True
                        break

        if not passed_condition:
            return False

    return True
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


# Define a score that is always greater than any centipawn evaluation,
# but smaller than an actual mate score.
# This is the 'magic' boundary used to translate DTM to CP.
MAX_CP_SCORE = 20000
# Assuming this is defined elsewhere in the user's actual code
# NEEDS_ANNOTATION_THRESHOLD = 10
# ERROR_THRESHOLD = {"BLUNDER": -300, "MISTAKE": -100, "DUBIOUS": -50}
# SHORT_PV_LEN = 10
# MAX_CPL = 500
# logger = logging.getLogger(__name__) # Assuming a logger is defined

def eval_numeric(result: chess.engine.AnalysisResult, board_turn: chess.Color) -> int:
    """
    Translates the result of engine.analyse() to a universal numeric score
    (centipawns) from the perspective of the player to move.

    - If the engine finds Mate in N (DTM), this is converted to a
      numeric score using MAX_CP_SCORE.
    - Otherwise, the centipawn (CP) value is returned.

    Args:
        result: The result of await engine.analyse().
        board_turn: The color of the player whose evaluation we want (True for White, False for Black).

    Returns:
        The numeric evaluation in centipawns.
    """

    # 1. Get the score.
    # We use .pov(board_turn) to always get the score from the perspective of the player to move.
    score = result.get("score").pov(board_turn)

    if score.is_mate():
        dtm = score.mate()

        if dtm > 0:
            # Win in N moves (e.g., Mate in 3): the smaller N, the higher the score.
            # (MAX_CP_SCORE - 1) is better than 10000 CP.
            return MAX_CP_SCORE - abs(dtm)
        elif dtm < 0:
            # Loss in N moves (e.g., Mated in 3): the smaller N, the lower the score.
            # (-MAX_CP_SCORE - 1) is worse than -10000 CP.
            return -(MAX_CP_SCORE - abs(dtm))
        else:
            # Should not happen unless Mate is already achieved, but for safety.
            return 0

    elif score.cp is not None:
        # If we have a normal centipawn score (no DTM), return it.
        return score.cp

    # Handling for unexpected cases (e.g., engine provides no score)
    raise RuntimeError("Engine evaluation result was unintelligible or missing score.")

def eval_human(white_to_move: chess.Color, result: chess.engine.AnalysisResult) -> str:
    """
    Returns a human-readable evaluation of the position:
        - If depth-to-mate was found, return plain-text mate announcement (e.g. "Mate in 4")
        - If depth-to-mate was not found, return an absolute numeric evaluation (e.g. "+1.50")

    Args:
        white_to_move: The color of the player whose move it was (the player AFTER whom the move is evaluated).
                       This is crucial for the absolute evaluation.
        result: The result of await engine.analyse() after the move.

    Returns:
        A human-readable string.
    """

    # 1. Get the score
    score = result.get("score")

    if score is None:
        return "No score available"

    # The score in the result is always POV of the player who was to move
    # WHEN the analysis began (i.e., the current board.turn).
    # We convert this to the perspective of WHITE (chess.WHITE).
    score_for_white = score.pov(chess.WHITE)

    if score_for_white.is_mate():
        dtm = score_for_white.mate()
        # dtm is the number of plies to mate. abs(dtm) is the number of *plies*.
        # The engine gives the score in plies, so divide by 2 for moves.
        moves_to_mate = abs(dtm) / 2

        # Give the name of the winning color (unused but translated for completeness)
        winning_color = "White" if dtm > 0 else "Black"

        # Since DTM is in plies, we ensure we show whole numbers to mate.
        # A mate in 1 (2 plies) is M2, so '1 move'.
        if moves_to_mate >= 1:
            return f"Mate in {int(moves_to_mate)}"
        else:
            # This would mean Mate in 0 (which has already happened)
            return "Mate"

    elif score_for_white.cp is not None:
        # We have a centipawn score, convert to pawns (divided by 100)
        score_in_pawns = score_for_white.cp / 100

        # Use the absolute evaluation (this is already from White's perspective,
        # so we only need to format).

        # Add the + sign for a clear display of advantage for White
        format_string = '{:+.2f}'
        return format_string.format(score_in_pawns)

    # If the engine returns a result without a score
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

    # NOTE: Assuming NEEDS_ANNOTATION_THRESHOLD is defined elsewhere (e.g., 10)
    return delta > NEEDS_ANNOTATION_THRESHOLD or best > played


async def judge_move(board: chess.Board, played_move: chess.Move, engine: chess.engine.UciProtocol, searchtime_s: float):
    """
    Evaluate the strength of a given move by comparing it to engine's best
    move and evaluation at a given depth, in a given board context.

    Returns a judgment dictionary.
    """

    # The engine.analyse() method automatically sets the FEN via the 'board' argument.
    analysis_limit = chess.engine.Limit(time=searchtime_s / 2)
    judgment = {}

    # --- THE INCORRECT LINE HAS BEEN REMOVED ---
    # await engine.set_fen(board.fen())
    # -----------------------------------------

    # First analysis: Determine the best move and the evaluation before the played move
    # =========================================================================
    try:
        # The 'board' parameter ensures the engine is synchronized
        best_move_result = await engine.analyse(
            board,
            limit=analysis_limit,
            info=chess.engine.Info(chess.engine.Info.ALL) # Request all info
        )
    except chess.engine.EngineTerminatedError:
        # Error handling for if the engine suddenly stops
        return {"error": "Engine terminated during analysis"}

    # Validate that the engine found a move and a score
    if not best_move_result.get("pv"):
        return {"error": "Engine found no primary variation (PV)"}


    # Populate the 'bestmove' part of the 'judgment'
    judgment["bestmove"] = best_move_result.get("pv")[0]
    judgment["besteval"] = eval_numeric(best_move_result, board.turn)
    judgment["pv"] = best_move_result.get("pv")
    judgment["depth"] = best_move_result.get("depth")
    judgment["nodes"] = best_move_result.get("nodes")
    # Annotate the best move
    judgment["bestcomment"] = eval_human(board.turn, best_move_result)

    # Second analysis: Evaluation of the played move
    # =========================================================================

    # If the played move is the best move, we don't need to analyze again
    if played_move == judgment["bestmove"]:
        judgment["playedeval"] = judgment["besteval"]
    else:
        # Make a copy of the board and play the move
        temp_board = board.copy()
        temp_board.push(played_move)

        # Perform the analysis on the NEW position (after the played move)
        played_move_result = await engine.analyse(
            temp_board, # This sets the engine to the position AFTER the move
            limit=analysis_limit,
            info=chess.engine.Info(chess.engine.Info.SCORE)
        )

        judgment["playedeval"] = eval_numeric(played_move_result, temp_board.turn)


    # Annotate the played move
    # Use the results of the second analysis (or the first if the move was the best)
    result_to_comment = played_move_result if played_move != judgment["bestmove"] else best_move_result
    judgment["playedcomment"] = eval_human(board.turn, result_to_comment)

    return judgment


def get_nags(judgment):
    """
    Returns a Numeric Annotation Glyph (NAG) according to how much worse the
    played move was vs the best move
    """
    # NOTE: Assuming ERROR_THRESHOLD is defined elsewhere
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
    # NOTE: Assuming SHORT_PV_LEN is defined elsewhere (e.g., 10)

    # We need a copy of the board to check for game end without modifying the original
    temp_board = board.copy()
    for move in pv:
        if not temp_board.is_legal(move):
            raise AssertionError
        temp_board.push(move)

    if temp_board.is_game_over(claim_draw=True):
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
    # The board is passed to truncate_pv to correctly check for game end
    variation = truncate_pv(prev_node.board().copy(), judgment["pv"])

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
        "code":           The ECO code of the matched opening
        "desc":           The long description of the matched opening
        "path":           The main variation of the opening
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
            break # Optimization: stop once a match is found

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
    # NOTE: Assuming 'logger' is defined elsewhere, the strings are translated.
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
    move. We put a ceiling on this value so that big blunders don't skew the
    acpl too much
    """
    # NOTE: Assuming MAX_CPL is defined elsewhere (e.g., 500)
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


    # ... (rest of the initialization logic) ...

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
                # CHANGE 6: judge_move must now use AWAIT and info_handler REMOVED
                judgment = await judge_move(prev_node.board(), node.move, engine, time_per_move)

                # Record the delta, to be referenced in the second pass
                node.comment = judgment

                # Count the number of mistakes that will have to be annotated later
                if needs_annotation(judgment):
                    error_count += 1

                # Print some debugging info
                debug_print(node, judgment)
            except chess.engine.EngineError as e:
                # Log the error cleanly in your own application.
                move_uci = node.move.uci()
                board_fen = prev_node.board().fen()
                logger.warning(f"EngineError for move {move_uci} on FEN {board_fen}. Error: {e}")

                # You can decide here whether to skip the move (as done with 'pass'),
                # or assign a default 'judgment'.
                node.comment = "Skipped due to engine error."

            except Exception as e:
                # Catch other unexpected errors that do not originate from the engine
                logger.error(f"Unexpected error during analysis: {e}")
                return

            node = prev_node

        # Calculate the average centipawn loss (ACPL) for each player
        game = add_acpl(game, root_node)
    except Exception as e:
        print(f"Fatal error during analysis: {e}")
        return

    ###########################################################################
    # Perform game analysis (Pass 2)
    ###########################################################################

    pass2_budget = get_pass2_budget(budget, pass1_budget)

    # ... (logic for determining time_per_move in pass 2) ...
    # Simplified:
    try:
        time_per_move = pass2_budget / error_count
    except ZeroDivisionError:
        # ... (error handling for no errors) ...
        pass


    logger.debug("Pass 2 budget is %i seconds, with %f seconds per move",
                 pass2_budget, time_per_move)
    logger.info("Performing second pass...")

    node = game.end()
    while not node == root_node:
        prev_node = node.parent

        judgment = node.comment

        if needs_annotation(judgment):
            # CHANGE 6: judge_move must now use AWAIT and info_handler REMOVED
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

    # Accessing identification data (.id is a dictionary)
    engine_id = engine.id

    # Get the primary name (e.g., "Stockfish 17")
    engine_name = engine_id.get("name", "Not found")

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
        errormsg = "Could not render the board. Is the file legal PGN? Aborting..."
        logger.critical(errormsg)
        return False
    return True

def change_nags(pgn):
    """
    Reformat PGN string to change NAGs (Numeric Annotation Glyphs) and ensure line wrapping.
    NAGs: blunder: $4 MISTAKE: $2 DUBIOUS: $6
    """
    pgn = str(pgn)
    # The following NAG replacements are commented out in the original, keeping them commented
    # pgn = pgn.replace("$6 {", "{Dubious ")
    # pgn = pgn.replace("$2 {", "{Mistake ")
    # pgn = pgn.replace("$4 {", "{Blunder ")
    # pgn = pgn.replace("$7 {", "{Good ")
    # pgn = pgn.replace("$9 {", "{Brilliant ")

    # Standardize spaces and split into lines
    strs = pgn.replace("  ", " ").split("\n")
    res = []
    # Process the first line (usually headers or FEN)
    res.append(strs.pop(0))

    # Re-wrap lines to a maximum of 80 characters, preserving header lines
    for line in strs:
        if len(line) < 80 or line.startswith("["):
            res.append(line)
        else:
            line_strs = line.split(" ")
            hl = "" # current line buffer
            for word in line_strs:
                # Check if the word fits on the current line or if it's a closing bracket
                if len(hl) + len(word) < 80 or word == "}" or word == ")":
                    sep = " "
                    if len(hl) == 0:
                        sep = ""
                    hl = hl + sep + word
                else:
                    # Current line is full, start a new line
                    res.append(hl)
                    hl = word
            # Append any remaining content in the buffer
            if len(hl) > 0:
                res.append(hl)

    pgn = "\n".join(res)
    return pgn

def start_analysis(pgnfile, engine_path, fine_name_file, add_to_library, gui, save_file=True, num_threads=2):
    """Synchronous wrapper to start asynchronous analysis."""
    # Note: loop closing logic removed as it's generally handled by asyncio.run
    return asyncio.run(start_analysis_async(pgnfile, engine_path, fine_name_file, add_to_library, gui, save_file, num_threads))

async def start_analysis_async(pgnfile, engine_path, fine_name_file, add_to_library, gui, save_file=True, num_threads=2):
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

                # Write to the files
                if not add_to_library:
                    # File 1: annotated_game.pgn
                    with open(os.path.join(gui.preferences.preferences["default_png_dir"], new_filename), 'w') as file1:
                        file1.writelines(annotated_content)
                    # File 2: fine_name_file
                    with open(fine_name_file, 'w') as file2:
                        file2.writelines(annotated_content)

                if add_to_library:
                    # Append to library.pgn
                    with open(os.path.join(gui.default_png_dir, "library.pgn"), 'a') as file3:
                        file3.writelines('\n\n' + annotated_content)

    # Clean up (Optional but Recommended)
    # The original cleanup logic was flawed because `loop` was not defined.
    # Await `engine.quit()` is the essential cleanup.
    engine.quit()

    return analyzed_game

def pgn_text_iterator(filepath: str) -> Iterator[str]:
    """
    Reads a large text file and iterates over items (games) that are separated
    by a line starting with '[Event'.

    This function is a generator: it does not read the entire file into memory,
    which is essential for very large files.

    Args:
        filepath: The path to the PGN-like text file.

    Yields:
        A string containing one complete item (game).
    """
    current_item_lines = []

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                stripped_line = line.strip()

                # Check if the line indicates the start of a new item
                if stripped_line.startswith('[Event '):
                    # If the buffer is not empty, the previous item is complete.
                    if current_item_lines:
                        # Combine and yield the buffer
                        yield "".join(current_item_lines).strip()

                        # Clear buffer and add the new item (the [Event line)
                        current_item_lines = [line]
                    else:
                        # This is the first line of the file
                        current_item_lines.append(line)
                else:
                    # Add the line to the current item
                    current_item_lines.append(line)

            # After the loop: yield the very last collected item
            if current_item_lines:
                yield "".join(current_item_lines).strip()

    except FileNotFoundError:
        print(f"Error: File not found at '{filepath}'")
    except Exception as e:
        print(f"An unexpected error occurred while reading: {e}")

async def get_engine(enginepath, threads):
    engine_name = ""

    ###########################################################################
    # Initialize the engine
    ###########################################################################

    try:
        # CHANGE 2: Store the transport object globally (Note: `engine_transport` is unused in the return)
        engine_transport, engine = await chess.engine.popen_uci(enginepath)
        await engine.configure({
            "Threads": threads
        })
        # previous_enginepath = enginepath # This variable is not used in this scope
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

# --- MAIN EXECUTION POINT (Synchronous Wrapper) ---

def run_annotate(pgnfile: str, enginepath: str, gametime: int, threads: int, filter_str: str, outputfile: str):
    """Synchronous wrapper to call the async function."""
    # This is the only point where asyncio.run() should be used.
    try:
        asyncio.run(run_annotate_async(pgnfile, enginepath, gametime, threads, filter_str, outputfile))
        return True # Success
    except KeyboardInterrupt:
        logger.critical("Process aborted by user (KeyboardInterrupt).")
    except Exception as e:
        logger.critical(f"FATAL ERROR: {e}")
        return False

def valid_engine(engine_path):
    if engine_path == 'Niet Gespecificeerd' or engine_path == '' or engine_path == 'Not Specified':
        return False
    else:
        return True

async def run_annotate_async(pgnfile, engine_path, gametime,threads, filter_str, outputfile):
    engine = None
    try:
        if valid_engine(engine_path):
            engine = await get_engine(engine_path, threads)
        processed_count = 0
        filtered_count = 0
        new_filename = outputfile
        if outputfile == "":
            new_filename = pgnfile[:-4] + "-annotated.pgn"
        # Truncate/create the output file
        with open(new_filename, 'w') as file1:
            file1.close()

        for item in pgn_text_iterator(pgnfile):
            pgn_io = io.StringIO(item.strip())
            chess_game = chess.pgn.read_game(pgn_io)

            white_player = chess_game.headers.get('White', 'Unknown')
            black_player = chess_game.headers.get('Black', 'Unknown')
            event_name = chess_game.headers.get('Event', 'Unknown Event')
            processed_count += 1

            # APPLYING THE FILTER
            if filter_str and filter_str != "None" and not matches_filter(chess_game, filter_str):
                filtered_count += 1
                continue # Skip to the next game

            # --- PROGRESS MESSAGE ONLY FOR SELECTED GAMES ---
            print(f"\n--- Game processing (Filter OK): {white_player} vs {black_player} ({event_name}) ---")

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
                with open(new_filename, 'a') as file1:
                    file1.writelines(str(analyzed_game))
                    # write one empty line to file1
                    file1.write('\n\n')

        if valid_engine(engine_path) and engine:
            await engine.quit()

        if processed_count > 1:
            print(f"\n--- Results ---")
            print(f"Total {processed_count} games found in the source.")
            print(f"Games skipped by filter: {filtered_count}")
            print(f"Games processed and saved: {processed_count - filtered_count}")

    except PermissionError:
        errormsg = "Input file not readable. Aborting..."
        logger.critical(errormsg)
        raise
    except FileNotFoundError:
        errormsg = f"Input file '{pgnfile}' not found. Aborting..."
        logger.critical(errormsg)
        raise
    finally:
        # Ensure engine is quit even if an error occurs outside the loop
        if engine and valid_engine(engine_path):
            try:
                engine.quit()
            except Exception:
                pass # Ignore if engine is already closed
