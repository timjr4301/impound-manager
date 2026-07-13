"""
Unified post-title disposition pipeline.

One ordered stage ladder that carries a vehicle from "title in hand" through
either the auction track (ending SOLD) or the scrap track (ending JUNKED),
with an off-ladder HOLD for court/lien parking. This REPLACES the two
conflicting `tina_stage` vocabularies that used to coexist on the same column:

    old model comment:   QUEUED, TITLE_WORK, COURT, READY, COMPLETE
    old pipeline board:  TITLE_PENDING, TITLE_COMPLETE, SERVICE_EVAL,
                         AUCTION_CAND, KEY_INSPECT, ROUTED_LIVE,
                         ROUTED_ONLINE, ROUTED_JUNK

Modeled on B&J's external Base44 "pulled → sold/junked" workflow, collapsed to
the stages staff actually act on. Keep this list as the single source of truth
— relabeling a stage is a one-line change here.

Each stage: (key, label, track, terminal)
  track: 'both'  — shared spine before the sell/junk decision
         'sell'  — auction track
         'junk'  — scrap track
         'hold'  — parked, off the main ladder
  terminal: True  — reaching it finalizes the vehicle (needs outcome data)
"""

STAGES = [
    ('AWAITING_TITLE', 'Awaiting Title', 'both', False),
    ('TITLE_FILED',    'Title Filed',    'both', False),
    ('AUCTION_PREP',   'Auction Prep',   'sell', False),
    ('AUCTION_READY',  'Auction Ready',  'sell', False),
    ('AT_AUCTION',     'At Auction',     'sell', False),
    ('SOLD',           'Sold',           'sell', True),
    ('JUNK_PREP',      'Junk Prep',      'junk', False),
    ('JUNKED',         'Junked',         'junk', True),
    ('HOLD',           'Hold',           'hold', False),
]

STAGE_KEYS      = [s[0] for s in STAGES]
STAGE_LABELS    = {s[0]: s[1] for s in STAGES}
STAGE_TRACK     = {s[0]: s[2] for s in STAGES}
TERMINAL_STAGES = {s[0] for s in STAGES if s[3]}

# Stages that mean "title not yet in hand" — the pre-disposition spine.
PRE_TITLE_STAGES = {'AWAITING_TITLE'}

# The two decision → first-working-stage jumps.
DISPOSITION_ENTRY = {
    'SELL': 'AUCTION_PREP',
    'JUNK': 'JUNK_PREP',
    'HOLD': 'HOLD',
}

# Terminal stage per disposition, and the outcome value it records.
DISPOSITION_TERMINAL = {'SELL': 'SOLD', 'JUNK': 'JUNKED'}
STAGE_OUTCOME = {'SOLD': 'SOLD', 'JUNKED': 'JUNKED'}

# One-time remap of every legacy tina_stage value to the new ladder. COMPLETE
# is handled separately (it's terminal and its outcome comes from the vehicle's
# existing invoice/disposition, not a blind remap).
LEGACY_STAGE_MAP = {
    'QUEUED':          'AWAITING_TITLE',
    'TITLE_WORK':      'AWAITING_TITLE',
    'COURT':           'AWAITING_TITLE',
    'AFFIDAVIT':       'AWAITING_TITLE',
    'READY':           'TITLE_FILED',
    'TITLE_PENDING':   'AWAITING_TITLE',
    'TITLE_COMPLETE':  'TITLE_FILED',
    'SERVICE_EVAL':    'AUCTION_PREP',
    'AUCTION_CAND':    'AUCTION_PREP',
    'KEY_INSPECT':     'AUCTION_PREP',
    'ROUTED_LIVE':     'AT_AUCTION',
    'ROUTED_ONLINE':   'AT_AUCTION',
    'ROUTED_JUNK':     'JUNK_PREP',
}


# Directed transitions between stages — what a card may move to from each stage.
# Terminal stages (SOLD/JUNKED) are reachable but the move is routed through the
# invoice/capture form, not a bare drag (see the board + pipeline_move). HOLD is
# a side option from every working stage, and resumes back onto either track.
TRANSITIONS = {
    'AWAITING_TITLE': ['TITLE_FILED', 'HOLD'],
    'TITLE_FILED':    ['AUCTION_PREP', 'JUNK_PREP', 'HOLD'],
    'AUCTION_PREP':   ['AUCTION_READY', 'HOLD'],
    'AUCTION_READY':  ['AT_AUCTION', 'HOLD'],
    'AT_AUCTION':     ['SOLD', 'HOLD'],
    'JUNK_PREP':      ['JUNKED', 'HOLD'],
    'HOLD':           ['TITLE_FILED', 'AUCTION_PREP', 'JUNK_PREP'],
    'SOLD':           [],
    'JUNKED':         [],
}


def move_targets(stage):
    """Ordered list of (key, label, is_terminal) a card in `stage` may move to."""
    return [(k, STAGE_LABELS[k], k in TERMINAL_STAGES)
            for k in TRANSITIONS.get(stage, [])]


def board_columns(disposition=None):
    """Ordered (key, label, track) columns to show on the board. With no
    disposition, shows the full ladder; filtering happens per-card client-side."""
    return [(k, l, t) for (k, l, t, _term) in STAGES]


def allowed_stages_for(disposition):
    """Stage keys a vehicle with this disposition may legitimately occupy:
    the shared spine + HOLD + that disposition's track."""
    base = {'AWAITING_TITLE', 'TITLE_FILED', 'HOLD'}
    if disposition == 'SELL':
        base |= {'AUCTION_PREP', 'AUCTION_READY', 'AT_AUCTION', 'SOLD'}
    elif disposition == 'JUNK':
        base |= {'JUNK_PREP', 'JUNKED'}
    else:
        # Undecided: every stage is reachable (the move itself sets disposition).
        base = set(STAGE_KEYS)
    return base


def disposition_for_stage(stage):
    """The disposition a stage implies, if any — used so dragging a card into a
    track lane also sets the SELL/JUNK decision. Returns None for shared/hold."""
    track = STAGE_TRACK.get(stage)
    if track == 'sell':
        return 'SELL'
    if track == 'junk':
        return 'JUNK'
    return None
