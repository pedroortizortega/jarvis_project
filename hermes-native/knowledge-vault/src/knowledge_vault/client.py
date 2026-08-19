from typing import Protocol

from .models import Proposal


class ProposalClient(Protocol):
    def submit(self, proposal: Proposal) -> Proposal: ...
