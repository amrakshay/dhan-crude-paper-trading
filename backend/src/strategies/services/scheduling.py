"""What a scheduled job REPORTED, in a place both automated modules can name.

Two dataclasses and nothing else. They were defined in
`src/swing/services/scheduler.py` and are unchanged -- field for field, method
for method -- but a second automated module has to be able to return them
without importing the rotation's package, and the rotation must not learn what
a BTST run kind is.

`scheduler.py` re-exports both, so every existing import of them still works
and the live component's public surface did not move.

There is deliberately no `ScheduledJob` abstraction here, and no base class for
a job. The two modules' jobs are genuinely different shapes -- one refreshes
five hundred symbols and decides, the other reads a live book and buys -- and a
shared skeleton invented for the second one would have had to be imposed on the
first while it is armed and trading. Extract it when a third module makes the
duplication real, which is the same judgement `src/swing/README.md` records
about everything else here.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List


@dataclass
class JobRun:
    """What one attempt did, for the status dict and the log."""

    strategy_key: str
    kind: str
    at: datetime
    portfolios: int = 0
    ok: bool = True
    detail: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "strategyKey": self.strategy_key,
            "kind": self.kind,
            "atIst": self.at.isoformat(),
            "portfolios": self.portfolios,
            "ok": self.ok,
            "detail": self.detail,
        }


@dataclass
class MissedRun:
    """Sessions this job should have a record for and does not."""

    strategy_key: str
    kind: str
    sessions: List[date] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "strategyKey": self.strategy_key,
            "kind": self.kind,
            "sessions": [one.isoformat() for one in self.sessions],
            "count": len(self.sessions),
        }
