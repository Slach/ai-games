"""Per-kind Verbalized Sampling k overrides.

DIVERSITY_HINTS keys in verbalize_sampling.py are the canonical VS-kind
namespace. This module lets specific kinds override the global VS_K default
without touching call sites — useful for token-heavy kinds
(combined_outcome) that degrade in quality when the model generates many
candidate sets in one pass.
"""

# Per-kind k overrides. Kinds not listed here fall back to the global VS_K env value.
# Keep this small: only override kinds where k>3 measurably hurts output quality
# or where generation is so token-heavy that latency/cost matters.
DEFAULT_VS_K_OVERRIDES: dict[str, int] = {}


def resolve_vs_k(kind: str, default_k: int) -> int:
    """Return k for a VS kind, falling back to default_k if not overridden."""
    return DEFAULT_VS_K_OVERRIDES.get(kind, default_k)
