"""Every alert this build can raise, declared once.

The Alerts tab answers "what is this thing going to tell me about, and when did
it last do so". That question needs a LIST, and the list has to come from one
place or it will drift from the code that raises them -- the same reason
`SwingExplainer` renders thresholds from the strategy's own YAML rather than
restating them in JSX, and the same reason `TASK_DESCRIPTIONS` is the set that
decides what a known task is.

So this module is the catalogue, and the modules that raise alerts use its
event names. It holds no logic and does no I/O: what each rule DOES lives in
`alert_watcher`, `alert_service` and `alert_sink`; this is what each one IS.

**It is read-only on every surface.** There is deliberately no edit and no
delete. What gets alerted is a property of the build, decided in code and
reviewed like code -- not a preference. When something here should be
switchable, that is its own piece of work with its own decision, exactly as
root `CLAUDE.md` section 3a says about a strategy's parameters.

**Scope decides which page shows it.** A rule about ONE strategy appears on
that strategy's page; a process-wide rule -- a dead task, the feed, the token --
appears only on the system page, because it is not any strategy's business. The
System Health page shows both, because it is the system view.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.connections.database.db_models.alert_model import (
    KIND_COMMAND,
    KIND_ERROR,
    KIND_HEALTH,
    KIND_IPO,
    KIND_TEST,
    KIND_TRADE_BOUGHT,
    KIND_TRADE_SOLD,
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SEVERITY_WARNING,
)

# --- categories, for grouping on the page ----------------------------------
CATEGORY_SYSTEM = "SYSTEM"
CATEGORY_STRATEGY = "STRATEGY"
CATEGORY_TRADE = "TRADE"
CATEGORY_ERROR = "ERROR"
CATEGORY_ADMIN = "ADMIN"
# The IPO dashboard. Its own category rather than SYSTEM or ADMIN: these rules
# are not about whether this process is well, and not about administering it --
# they are about something the OPERATOR has to do today, on somebody else's
# deadline. The Alerts tab groups by whatever categories exist, so adding one
# costs nothing but says what it is.
CATEGORY_IPO = "IPO"

CATEGORY_LABELS = {
    CATEGORY_SYSTEM: "System health",
    CATEGORY_STRATEGY: "Strategy",
    CATEGORY_TRADE: "Trades",
    CATEGORY_ERROR: "Errors",
    CATEGORY_ADMIN: "Administration",
    CATEGORY_IPO: "IPO dashboard",
}

# --- the health events, named once and used by the watcher -----------------
EVENT_MISSING_TASKS = "missing-tasks"
EVENT_TOKEN_LAPSED = "token-lapsed"
EVENT_TOKEN_REJECTED = "token-rejected"
EVENT_TOKEN_RENEWAL_FAILED = "token-renewal-failed"
EVENT_FEED_DOWN = "feed-down"
EVENT_FEED_STALE = "feed-stale"
EVENT_MISSED_SESSIONS = "missed-sessions"
EVENT_STALE_BARS = "stale-bars"
EVENT_DEFERRED_STOPS = "deferred-stops"
EVENT_BTST_EXIT_INCOMPLETE = "btst-exit-incomplete"
EVENT_APP_STARTED = "app-started"
# The IPO dashboard's own two. Neither is `strategy_scoped`: no strategy owns
# an IPO, and `alerts.strategy_key` stays NULL on both.
EVENT_IPO_CLOSING_REMINDER = "ipo-closing-reminder"
EVENT_IPO_SOURCE_UNREACHABLE = "ipo-source-unreachable"
# The two modules whose rules are not interchangeable. Spelled here rather than
# beside each rule so there is one place to look when a third arrives -- and
# spelled at all because these two rules describe machinery the OTHER strategy
# does not have, which no amount of `strategy_scoped` can express.
STRATEGY_SWING = "nse-swing-momentum"
STRATEGY_BTST = "nse-btst-overnight"



@dataclass(frozen=True)
class AlertRule:
    """One thing this application will tell somebody about."""

    key: str
    title: str
    # What makes it fire, in the words an operator would use.
    trigger: str
    # Why it is worth a message at all. A rule that cannot answer this should
    # not be in the list.
    why: str
    severity: str
    category: str
    # The `Alert.kind` it writes.
    kind: str
    # How a stored row is recognised as this rule. A health event's dedupe key
    # is `health|<event>` and may carry a suffix (the task names, the sessions),
    # so matching is on the PREFIX.
    dedupe_prefix: Optional[str] = None
    # Whether it is about ONE strategy. Strategy rules appear on that
    # strategy's page; the rest are process-wide and appear only on the system
    # page.
    strategy_scoped: bool = False
    # WHICH strategies raise it, when only some do. `None` means every
    # automated module can, which is true of a missed session, stale bars and
    # the two trade rules.
    #
    # It exists because a second automated strategy arrived. Until 2026-09-19
    # there was one, so "about a strategy" and "about the rotation" were the
    # same statement and `strategy_scoped` alone was enough. With two, the
    # unqualified list put "a triggered stop is waiting for the next open" on
    # the page of a strategy whose specification says B15 is none and none is
    # possible -- a page promising a message that cannot be sent -- and put the
    # BTST exit rule on the rotation's, which has no overnight book to get
    # stuck. A rule nobody will ever fire is worse than an absent one: it reads
    # as cover somebody has and does not.
    strategy_keys: Optional[Tuple[str, ...]] = None
    # How repeats are collapsed, so the page can say it rather than an operator
    # having to infer it from a count.
    collapsing: str = ""
    # What it will NOT do. Stated where it could otherwise be assumed.
    caveat: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "trigger": self.trigger,
            "why": self.why,
            "severity": self.severity,
            "category": self.category,
            "categoryLabel": CATEGORY_LABELS.get(self.category, self.category),
            "kind": self.kind,
            "strategyScoped": self.strategy_scoped,
            "collapsing": self.collapsing,
            "caveat": self.caveat,
        }


def _health(event: str) -> str:
    """The dedupe prefix a `record_health` row is written with."""
    return f"health|{event}"


RULES: Tuple[AlertRule, ...] = (
    # --- system health, in the order they matter -------------------------
    AlertRule(
        key=EVENT_MISSING_TASKS,
        title="A background task that should be running is not",
        trigger=(
            "Every 60 s the watcher compares the live asyncio task table against "
            "what should be running for the current configuration. Any task "
            "that is expected and absent fires this."
        ),
        why=(
            "A task that dies does not announce itself. A dead swing-scheduler "
            "on an ARMED strategy means nothing decides and nothing trades, "
            "silently, until somebody notices the journal is empty. This is the "
            "single most valuable alert here."
        ),
        severity=SEVERITY_CRITICAL,
        category=CATEGORY_SYSTEM,
        kind=KIND_HEALTH,
        dedupe_prefix=_health(EVENT_MISSING_TASKS),
        collapsing="One message per distinct set of missing tasks.",
        caveat=(
            "CRITICAL only when a strategy is armed and one of its own tasks is "
            "the missing one; otherwise WARNING."
        ),
    ),
    AlertRule(
        key=EVENT_TOKEN_LAPSED,
        title="The Dhan access token has expired",
        trigger="The stored token's own `exp` claim is in the past.",
        why=(
            "Automatic renewal cannot recover from this: Dhan renews only a "
            "token that is still active, and this application deliberately "
            "cannot mint one. It needs a person, and until then the feed, the "
            "chart, the option chain and the overnight bar refresh are stopped."
        ),
        severity=SEVERITY_CRITICAL,
        category=CATEGORY_SYSTEM,
        kind=KIND_HEALTH,
        dedupe_prefix=_health(EVENT_TOKEN_LAPSED),
        collapsing="One message per de-duplication window while it stays expired.",
    ),
    AlertRule(
        key=EVENT_TOKEN_REJECTED,
        title="Dhan is refusing the access token, though it has not expired",
        trigger=(
            "A real Dhan request came back with an authentication failure -- a "
            "401, or a 400 carrying DH-906 'Invalid Token' -- while the "
            "token's own `exp` claim is still in the future."
        ),
        why=(
            "This is the failure the expiry countdown cannot see. A token can "
            "be revoked server-side long before it expires: generating a new "
            "one for the same client id invalidates the previous one, and a "
            "lapsed Data APIs subscription has the same effect. It happened on "
            "2026-09-19, four hours into a twenty-four hour token, while every "
            "surface in this application went on showing twenty hours "
            "remaining. Without this rule the first symptom is a strategy "
            "quietly deciding nothing."
        ),
        severity=SEVERITY_CRITICAL,
        category=CATEGORY_SYSTEM,
        kind=KIND_HEALTH,
        dedupe_prefix=_health(EVENT_TOKEN_REJECTED),
        collapsing=(
            "A CONDITION, so one message when Dhan starts refusing and silence "
            "while it keeps refusing -- not one per request."
        ),
        caveat=(
            "It reports what Dhan SAID, not why. Dhan answers a revoked token, "
            "an unpaid Data APIs subscription and a mistyped client id with "
            "the same 808, so the fix is to generate a fresh token and, if "
            "that does not work, to check the subscription."
        ),
    ),
    AlertRule(
        key=EVENT_TOKEN_RENEWAL_FAILED,
        title="Dhan token renewal is failing",
        trigger="The renewal task reported an error and the token is still valid.",
        why=(
            "Kept apart from the lapsed case on purpose: this one will try "
            "again and usually fixes itself, so it is a warning rather than "
            "something to wake somebody for. Only a token that has already "
            "died needs a human."
        ),
        severity=SEVERITY_WARNING,
        category=CATEGORY_SYSTEM,
        kind=KIND_HEALTH,
        dedupe_prefix=_health(EVENT_TOKEN_RENEWAL_FAILED),
        collapsing="One message per de-duplication window while it keeps failing.",
    ),
    AlertRule(
        key=EVENT_FEED_DOWN,
        title="The market feed is not connected while a market is open",
        trigger=(
            "The feed reports DISCONNECTED, ERROR or no state at all, during a "
            "session of some enabled strategy."
        ),
        why=(
            "Prices are not arriving, and anything reading a mark is reading "
            "the last one it saw."
        ),
        severity=SEVERITY_CRITICAL,
        category=CATEGORY_SYSTEM,
        kind=KIND_HEALTH,
        dedupe_prefix=_health(EVENT_FEED_DOWN),
        collapsing="One message per de-duplication window while it stays down.",
        caveat=(
            "Never fires on the synthetic feed: locally generated prices are a "
            "mode, not a fault, and every screen already says so permanently."
        ),
    ),
    AlertRule(
        key=EVENT_FEED_STALE,
        title="The market feed has gone quiet while a market is open",
        trigger=(
            "Connected, but no message for 90 s during a session -- past Dhan's "
            "own 40 s drop cliff, so the watchdog has already had its chance to "
            "reconnect."
        ),
        why=(
            "A stale book that looks live is the worst failure this tool can "
            "have: every price on screen is wrong and nothing says so."
        ),
        severity=SEVERITY_WARNING,
        category=CATEGORY_SYSTEM,
        kind=KIND_HEALTH,
        dedupe_prefix=_health(EVENT_FEED_STALE),
        collapsing="One message per de-duplication window while it stays quiet.",
        caveat="Suppressed outside market hours and on the synthetic feed.",
    ),
    AlertRule(
        key=EVENT_APP_STARTED,
        title="The application started",
        trigger="The process finished starting up.",
        why=(
            "Low volume, high signal: a restart you did not perform is worth "
            "knowing about, and it is the one alert whose ABSENCE is also "
            "informative."
        ),
        severity=SEVERITY_INFO,
        category=CATEGORY_SYSTEM,
        kind=KIND_HEALTH,
        dedupe_prefix=_health(EVENT_APP_STARTED),
        collapsing=(
            "Collapsed within the de-duplication window, so a process that is "
            "crash-looping sends one message with a count rather than one per "
            "restart."
        ),
    ),
    # --- per strategy -----------------------------------------------------
    AlertRule(
        key=EVENT_MISSED_SESSIONS,
        title="A scheduled session was missed",
        trigger=(
            "The decision journal has no record for a session the regime "
            "index's own bar dates say the market traded."
        ),
        why=(
            "Nothing is re-decided days later on bars that may since have been "
            "restated -- that would be a record of a decision nobody took. So "
            "the gap is reported instead, and stays reported."
        ),
        severity=SEVERITY_WARNING,
        category=CATEGORY_STRATEGY,
        kind=KIND_HEALTH,
        dedupe_prefix=_health(EVENT_MISSED_SESSIONS),
        strategy_scoped=True,
        collapsing="One message per distinct set of missed sessions.",
    ),
    AlertRule(
        key=EVENT_STALE_BARS,
        title="The stored daily bars are too old to trade on",
        trigger=(
            "The newest stored session is further back than "
            "`swing.max_bar_staleness_days` -- the same rule, and the same "
            "sentence, the rebalance would refuse with."
        ),
        why=(
            "Sent when the condition appears rather than at the order window, "
            "when it is too late to fix. Trading on a ranking computed from "
            "prices that old is acting on a market that no longer exists."
        ),
        severity=SEVERITY_WARNING,
        category=CATEGORY_STRATEGY,
        kind=KIND_HEALTH,
        dedupe_prefix=_health(EVENT_STALE_BARS),
        strategy_scoped=True,
        collapsing="One message per de-duplication window while the bars stay stale.",
    ),
    AlertRule(
        key=EVENT_DEFERRED_STOPS,
        title="A triggered stop is waiting for the next open",
        trigger=(
            "A trailing stop fired but its exit could not be placed, because "
            "NSE's Closing Auction Session ends continuous trading at 15:15 for "
            "F&O-eligible names."
        ),
        why=(
            "A decision the strategy took that a person would want to know "
            "about the same evening rather than discovering at the next open. "
            "This simulator has no model of a call auction, so it records the "
            "trigger and waits rather than inventing a fill."
        ),
        severity=SEVERITY_INFO,
        category=CATEGORY_STRATEGY,
        kind=KIND_HEALTH,
        dedupe_prefix=_health(EVENT_DEFERRED_STOPS),
        strategy_scoped=True,
        # THE ROTATION'S ONLY. BTST has no stop and none is possible, so this
        # could never fire for it.
        strategy_keys=(STRATEGY_SWING,),
        collapsing="One message per distinct set of waiting stops.",
    ),
    AlertRule(
        key=EVENT_BTST_EXIT_INCOMPLETE,
        title="An overnight position is still held after the exit ran",
        trigger=(
            "The BTST exit job ran at its configured time and one or more "
            "positions were not sold -- the order was refused, the market was "
            "not in continuous trading, or the book had no depth to fill "
            "against."
        ),
        why=(
            "For this strategy the exit IS the edge, which is not true of "
            "anything else in this application. The same positions held to the "
            "next close instead of the next open measure a 49.0% win rate "
            "against 71.4%, and a net edge of +0.128% against +0.317%. A "
            "missed rotation run costs a session's decision and is reported; a "
            "missed BTST exit costs the trade thesis, so it is alerted."
        ),
        severity=SEVERITY_CRITICAL,
        category=CATEGORY_STRATEGY,
        kind=KIND_HEALTH,
        dedupe_prefix=_health(EVENT_BTST_EXIT_INCOMPLETE),
        strategy_scoped=True,
        # BTST'S ONLY. The rotation holds for weeks and has no overnight book
        # with a due exit to be stuck in.
        strategy_keys=(STRATEGY_BTST,),
        collapsing=(
            "A CONDITION, so one message when a position gets stuck and "
            "silence while it stays stuck -- not one per pass."
        ),
        caveat=(
            "It reports that a position is still held, never that it was sold "
            "late. A sale after the window succeeds and is recorded EXITED_LATE "
            "with the delay on the row; look at the Live tab for those."
        ),
    ),
    # --- trades -----------------------------------------------------------
    AlertRule(
        key="trade-bought",
        title="Bought",
        trigger=(
            "Any fill that opens or adds to a position, from any source -- the "
            "rotation, chart trading or an order placed by hand."
        ),
        why=(
            "Raised from `PositionService.apply_fill`, where every path "
            "converges, so a new way to trade inherits the alert rather than "
            "having to remember it."
        ),
        severity=SEVERITY_INFO,
        category=CATEGORY_TRADE,
        kind=KIND_TRADE_BOUGHT,
        strategy_scoped=True,
        collapsing="Never collapsed. Every trade is its own message.",
        caveat=(
            "Carries the reason the order was placed -- for the rotation, its "
            "rank and score -- and the cash remaining."
        ),
    ),
    AlertRule(
        key="trade-sold",
        title="Sold",
        trigger="Any fill that reduces or closes a position.",
        why=(
            "Carries realised P&L in rupees and percent, how long it was held, "
            "the charges, and WHY it left -- read from `swing_stops.exit_kind`, "
            "a stored fact, never guessed. A sale with no exit-kind row is "
            "reported as a manual close rather than given a category it never "
            "had."
        ),
        severity=SEVERITY_INFO,
        category=CATEGORY_TRADE,
        kind=KIND_TRADE_SOLD,
        strategy_scoped=True,
        collapsing="Never collapsed. Every trade is its own message.",
        caveat=(
            "The P&L is the figure the position row already holds, never a "
            "third calculation that could disagree with the reports."
        ),
    ),
    # --- errors -----------------------------------------------------------
    AlertRule(
        key="error-records",
        title="An ERROR was logged",
        trigger=(
            "Any log record at ERROR or above, from any component, seen by a "
            "logging handler rather than by anything having to remember to "
            "alert."
        ),
        why=(
            "WARNING is the right floor for a page somebody is reading and far "
            "too chatty for a phone, so the floor here is ERROR."
        ),
        severity=SEVERITY_WARNING,
        category=CATEGORY_ERROR,
        kind=KIND_ERROR,
        collapsing=(
            "Collapsed on the logger name plus a NORMALISED message, so a "
            "component failing once a second sends one message and then a "
            "count. This codebase has produced exactly that flood twice."
        ),
        caveat=(
            "The alerting machinery is excluded from its own sink, or a "
            "delivery failure would alert about itself in a loop."
        ),
    ),
    # --- administration ---------------------------------------------------
    AlertRule(
        key="command",
        title="A Telegram command was issued or refused",
        trigger=(
            "Any state-changing command, and EVERY refusal, whether the sender "
            "was authorised or not."
        ),
        why=(
            "An action with no record is the thing this codebase most "
            "consistently refuses, and a refusal that leaves no record is how "
            "you fail to notice an attempt."
        ),
        severity=SEVERITY_INFO,
        category=CATEGORY_ADMIN,
        kind=KIND_COMMAND,
        collapsing="Never collapsed.",
        caveat=(
            "What a command CHANGED also carries the sender's application user "
            "id, exactly as a click does."
        ),
    ),
    AlertRule(
        key=EVENT_IPO_CLOSING_REMINDER,
        title="A mainboard IPO closes today and a step is still outstanding",
        trigger=(
            "Every hour on the hour from 10:00 to 17:00 IST on an IPO's "
            "closing day, for every mainboard IPO closing that day that is "
            "still outstanding. The GMP is re-fetched immediately before each "
            "sweep so the message carries a current figure."
        ),
        why=(
            "An application with an unaccepted UPI mandate is a failed "
            "application, and the mandate is the step that actually gets "
            "forgotten -- so being marked Applied does NOT stop these. Only "
            "Applied AND Accepted together, or an explicit Reject, does. The "
            "deadline belongs to somebody else and does not move."
        ),
        severity=SEVERITY_WARNING,
        category=CATEGORY_IPO,
        kind=KIND_IPO,
        dedupe_prefix="ipo|closing-reminder",
        collapsing=(
            "One message per hour slot. The dedupe key carries the IST date "
            "and the hour, so the hourly cadence survives while a restart or a "
            "double tick inside the same hour cannot produce two messages."
        ),
        caveat=(
            "Nothing is sent when nothing is outstanding -- there is no "
            "all-clear message. It reminds; it cannot apply for anything."
        ),
    ),
    AlertRule(
        key=EVENT_IPO_SOURCE_UNREACHABLE,
        title="The IPO GMP source could not be read",
        trigger=(
            "A scheduled IPO refresh -- the 13:00 daily pass, or the targeted "
            "one before an hourly closing-day reminder -- failed to fetch or "
            "parse the source."
        ),
        why=(
            "A failed refresh must not silently serve stale data as fresh. The "
            "reminder still goes out, because the reminder is the point and "
            "the GMP is context, but it goes out with the GMP labelled stale "
            "and this says why."
        ),
        severity=SEVERITY_WARNING,
        category=CATEGORY_IPO,
        kind=KIND_HEALTH,
        dedupe_prefix=_health(EVENT_IPO_SOURCE_UNREACHABLE),
        collapsing=(
            "A CONDITION: one message when the source goes unreachable, "
            "silence while it stays that way, and a new one once it has "
            "recovered and failed again."
        ),
    ),
    AlertRule(
        key="test",
        title="A test message sent by hand",
        trigger="The 'Send test message' button on the Connections page.",
        why=(
            "Proves the outbound half -- the token, the channel and permission "
            "to post -- in one press, and identifies which installation sent it."
        ),
        severity=SEVERITY_INFO,
        category=CATEGORY_ADMIN,
        kind=KIND_TEST,
        collapsing="Never collapsed.",
    ),
)


def all_rules() -> List[AlertRule]:
    return list(RULES)


def rules_for(strategy_key: Optional[str] = None) -> List[AlertRule]:
    """The rules to show. A strategy's page shows only what is about it.

    A process-wide rule is deliberately NOT repeated onto a strategy's page: a
    dead feed and an expiring token are facts about this process, the system
    page owns them, and duplicating them would make two pages that disagree the
    moment one of them changes.
    """
    if strategy_key is None:
        return all_rules()
    return [
        rule
        for rule in RULES
        if rule.strategy_scoped
        # A rule that names its modules appears only on theirs. One that names
        # none is true of every automated strategy -- a missed session, stale
        # bars and the two trade rules.
        and (rule.strategy_keys is None or strategy_key in rule.strategy_keys)
    ]


def rules_for_category(category: str) -> List[AlertRule]:
    """The rules in one category.

    A SELECTOR, not a second scoping concept. `strategy_scoped` answers "is
    this about one strategy"; this answers "which group is it in", which the
    page already groups by. It exists because the IPO dashboard's two rules are
    deliberately NOT strategy-scoped -- no strategy owns an IPO -- so
    `rules_for(strategy_key)` correctly refuses to return them, and without
    this the only surface that could show them is the system page.

    Nothing here is withdrawn from the system page by being selected: that page
    shows `all_rules()` and still does.
    """
    return [rule for rule in RULES if rule.category == category]


def known_categories() -> Tuple[str, ...]:
    """Every category a rule actually uses. The API validates against this."""
    return tuple(sorted({rule.category for rule in RULES}))


def by_key(key: str) -> Optional[AlertRule]:
    for rule in RULES:
        if rule.key == key:
            return rule
    return None
