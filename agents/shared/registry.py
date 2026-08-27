"""The ten bank-specialist identities and their prompt modules."""

from dataclasses import dataclass
from importlib import import_module

from banks.providers import BANKS


@dataclass(frozen=True)
class SpecialistSpec:
    bank: str
    display_name: str
    prompt_module: str

    @property
    def tool_name(self) -> str:
        return f"ask_{self.bank}"


SPECS = tuple(
    SpecialistSpec(
        bank=bank.name,
        display_name=bank.display_name,
        prompt_module=f"agents.{bank.name}.prompt",
    )
    for bank in BANKS
)


def prompt_for(bank: str) -> str:
    """Load the prompt owned by the bank's own package."""
    for spec in SPECS:
        if spec.bank == bank:
            return import_module(spec.prompt_module).NAME
    raise ValueError(f"Unknown bank specialist: {bank}")
