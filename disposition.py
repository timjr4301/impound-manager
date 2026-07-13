"""
Unified post-title disposition pipeline.

One ordered stage ladder that carries a vehicle from "title in hand" through the
real yard process: locate it, decide junk vs auction, cut a key, inspect it,
route it to live or online auction, and end Sold or Junked — with chain of
custody (where's the car / where's the key) tracked the whole way.

This is the single source of truth for the pipeline. Relabeling or reordering a
stage is a one-line change here.

Real-world mapping:
  Awaiting Title   – title not yet in hand
  To Locate        – title obtained (Tina booked it); on the driver Find List
  Key Row          – driver called it auction; "K in the window", needs a key
  Inspection Pool  – key cut; behind service at 4301, needs a tech look
  Needs Repairs    – tech says it needs work; waiting on Jim/Tina approval
  Auction Ready    – diagnosed good; awaiting an auction event
  At Auction       – routed to Peacock (online) or Fifth Ave (live)
  Sold             – terminal (proceeds captured)
  Junk — Pending   – driver/tech called it junk; awaiting Tina's Ohio Steel sign-off
  Junked           – terminal (Ohio Steel; $/car + converter count captured)
  Hold             – parked (court/lien/other)

Each stage: (key, label, track, terminal)
  track: 'both' | 'sell' | 'junk' | 'hold'
  terminal: reaching it finalizes the vehicle (needs outcome data)
"""

STAGES = [
    ('AWAITING_TITLE', 'Awaiting Title',  'both', False),
    ('TO_LOCATE',      'To Locate',       'both', False),
    ('KEY_ROW',        'Key Row',         'sell', False),
    ('INSPECT_POOL',   'Inspection Pool', 'sell', False),
    ('NEEDS_REPAIRS',  'Needs Repairs',   'sell', False),
    ('AUCTION_READY',  'Auction Ready',   'sell', False),
    ('AT_AUCTION',     'At Auction',      'sell', False),
    ('SOLD',           'Sold',            'sell', True),
    ('JUNK_PENDING',   'Junk — Pending',  'junk', False),
    ('JUNKED',         'Junked',          'junk', True),
    ('HOLD',           'Hold',            'hold', False),
]

STAGE_KEYS      = [s[0] for s in STAGES]
STAGE_LABELS    = {s[0]: s[1] for s in STAGES}
STAGE_TRACK     = {s[0]: s[2] for s in STAGES}
TERMINAL_STAGES = {s[0] for s in STAGES if s[3]}

# Stages meaning "title not yet in hand" — the pre-locate spine.
PRE_TITLE_STAGES = {'AWAITING_TITLE'}

# Decision → the track's first working stage.
DISPOSITION_ENTRY = {'SELL': 'KEY_ROW', 'JUNK': 'JUNK_PENDING', 'HOLD': 'HOLD'}
# Terminal stage per disposition, and the outcome value it records.
DISPOSITION_TERMINAL = {'SELL': 'SOLD', 'JUNK': 'JUNKED'}
STAGE_OUTCOME = {'SOLD': 'SOLD', 'JUNKED': 'JUNKED'}

# Directed transitions — what a card may move to from each stage. Terminal moves
# (Sold/Junked) are routed through the invoice/capture form, not a bare drag.
# HOLD is a side option; Junk — Pending is reachable from most working stages
# because a car can be condemned at any point (driver, key guy, or tech).
TRANSITIONS = {
    'AWAITING_TITLE': ['TO_LOCATE', 'HOLD'],
    'TO_LOCATE':      ['KEY_ROW', 'JUNK_PENDING', 'HOLD'],
    'KEY_ROW':        ['INSPECT_POOL', 'JUNK_PENDING', 'HOLD'],
    'INSPECT_POOL':   ['AUCTION_READY', 'NEEDS_REPAIRS', 'JUNK_PENDING', 'HOLD'],
    'NEEDS_REPAIRS':  ['AUCTION_READY', 'JUNK_PENDING', 'HOLD'],
    'AUCTION_READY':  ['AT_AUCTION', 'HOLD'],
    'AT_AUCTION':     ['SOLD', 'AUCTION_READY', 'HOLD'],   # AUCTION_READY = no-sale return
    'JUNK_PENDING':   ['JUNKED', 'AUCTION_READY', 'HOLD'], # AUCTION_READY = changed our mind
    'HOLD':           ['TO_LOCATE', 'KEY_ROW', 'INSPECT_POOL', 'AUCTION_READY', 'JUNK_PENDING'],
    'SOLD':           [],
    'JUNKED':         [],
}

# One-time remap of every legacy tina_stage value (both the original two
# vocabularies AND the interim single-track ladder) onto this ladder. Idempotent
# — only rewrites known-old keys. COMPLETE is handled separately (terminal).
LEGACY_STAGE_MAP = {
    # original model-comment vocabulary
    'QUEUED':          'AWAITING_TITLE',
    'TITLE_WORK':      'AWAITING_TITLE',
    'COURT':           'AWAITING_TITLE',
    'AFFIDAVIT':       'AWAITING_TITLE',
    'READY':           'TO_LOCATE',
    # original pipeline-board vocabulary
    'TITLE_PENDING':   'AWAITING_TITLE',
    'TITLE_COMPLETE':  'TO_LOCATE',
    'SERVICE_EVAL':    'INSPECT_POOL',
    'AUCTION_CAND':    'KEY_ROW',
    'KEY_INSPECT':     'KEY_ROW',
    'ROUTED_LIVE':     'AT_AUCTION',
    'ROUTED_ONLINE':   'AT_AUCTION',
    'ROUTED_JUNK':     'JUNK_PENDING',
    # interim single-track ladder
    'TITLE_FILED':     'TO_LOCATE',
    'AUCTION_PREP':    'KEY_ROW',
    'JUNK_PREP':       'JUNK_PENDING',
}


def board_columns():
    """Ordered (key, label, track) columns for the board."""
    return [(k, l, t) for (k, l, t, _term) in STAGES]


def move_targets(stage):
    """Ordered list of (key, label, is_terminal) a card in `stage` may move to."""
    return [(k, STAGE_LABELS[k], k in TERMINAL_STAGES)
            for k in TRANSITIONS.get(stage, [])]


def allowed_stages_for(disposition):
    """Stage keys a vehicle with this disposition may legitimately occupy."""
    base = {'AWAITING_TITLE', 'TO_LOCATE', 'HOLD'}
    if disposition == 'SELL':
        base |= {'KEY_ROW', 'INSPECT_POOL', 'NEEDS_REPAIRS', 'AUCTION_READY', 'AT_AUCTION', 'SOLD'}
    elif disposition == 'JUNK':
        base |= {'JUNK_PENDING', 'JUNKED'}
    else:
        base = set(STAGE_KEYS)
    return base


def disposition_for_stage(stage):
    """The disposition a stage implies, if any — so moving a card into a track
    lane also sets the SELL/JUNK decision. None for the shared spine and Hold."""
    track = STAGE_TRACK.get(stage)
    if track == 'sell':
        return 'SELL'
    if track == 'junk':
        return 'JUNK'
    return None


# Chain-of-custody: where a key can be.
KEY_LOCATIONS = [
    ('NONE',           'No key / not made'),
    ('IGNITION',       'In the ignition'),
    ('TINA',           'With Tina'),
    ('KEY_MAKER',      'With the key maker'),
    ('SERVICE_HOLDER', 'Service key holder'),
    ('DRIVER',         'With a driver'),
]
KEY_LOCATION_LABELS = dict(KEY_LOCATIONS)

# Inspection diagnosis outcomes.
DIAGNOSES = [
    ('AUCTION',  'Auction-ready'),
    ('REPAIRS',  'Needs repairs'),
    ('JUNK',     'Junk'),
]
DIAGNOSIS_LABELS = dict(DIAGNOSES)

# Auction venue for the sell track.
AUCTION_VENUES = [
    ('ONLINE', 'Online — Peacock Auto Auction'),
    ('LIVE',   'Live — Fifth Ave Auto Sales (3865)'),
]
AUCTION_VENUE_LABELS = dict(AUCTION_VENUES)
