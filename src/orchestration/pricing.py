"""Centralized model pricing and token-cost calculations."""

from __future__ import annotations

from schemas.run_state import CostBreakdown, ModelPricing, ModelUsage

# Pricing is application configuration, not a secret. Keep it in source control
# rather than in .env so cost estimates are reproducible and reviewable.
MODEL_PRICING: dict[str, ModelPricing] = {
    "gpt-5.6-luna": ModelPricing(
        input_per_1m=0.20,
        cached_input_per_1m=0.02,
        output_per_1m=1.20,
    ),
}


def pricing_for_model(model: str | None) -> ModelPricing | None:
    """Return registered pricing for a model, if the registry knows it."""

    if model is None:
        return None
    return MODEL_PRICING.get(model.strip())


def resolve_model_pricing(
    model: str | None,
    *,
    input_per_1m: float | None = None,
    cached_input_per_1m: float | None = None,
    output_per_1m: float | None = None,
) -> ModelPricing | None:
    """Resolve registry pricing with optional per-million-token overrides.

    A known model may override any subset of its rates. An unknown model needs
    all three rates before a complete estimate can be produced.
    """

    overrides = (input_per_1m, cached_input_per_1m, output_per_1m)
    registered = pricing_for_model(model)
    if not any(rate is not None for rate in overrides):
        return registered
    if registered is None and any(rate is None for rate in overrides):
        return None
    base = registered or ModelPricing(
        input_per_1m=0,
        cached_input_per_1m=0,
        output_per_1m=0,
    )
    return ModelPricing(
        input_per_1m=(base.input_per_1m if input_per_1m is None else input_per_1m),
        cached_input_per_1m=(
            base.cached_input_per_1m
            if cached_input_per_1m is None
            else cached_input_per_1m
        ),
        output_per_1m=(base.output_per_1m if output_per_1m is None else output_per_1m),
    )


def calculate_cost_breakdown(
    usage: ModelUsage,
    pricing: ModelPricing,
    *,
    pricing_model: str,
) -> CostBreakdown:
    """Calculate a cost estimate using cached and uncached input separately."""

    if usage.cached_tokens > usage.input_tokens:
        raise ValueError("cached_tokens cannot exceed input_tokens")
    uncached_input = usage.input_tokens - usage.cached_tokens
    uncached_input_cost = uncached_input / 1_000_000 * pricing.input_per_1m
    cached_input_cost = usage.cached_tokens / 1_000_000 * pricing.cached_input_per_1m
    output_cost = usage.output_tokens / 1_000_000 * pricing.output_per_1m
    return CostBreakdown(
        pricing_model=pricing_model,
        input_per_1m=pricing.input_per_1m,
        cached_input_per_1m=pricing.cached_input_per_1m,
        output_per_1m=pricing.output_per_1m,
        input_tokens=usage.input_tokens,
        cached_tokens=usage.cached_tokens,
        uncached_input_tokens=uncached_input,
        output_tokens=usage.output_tokens,
        uncached_input_cost_usd=uncached_input_cost,
        cached_input_cost_usd=cached_input_cost,
        output_cost_usd=output_cost,
        estimated_cost_usd=uncached_input_cost + cached_input_cost + output_cost,
    )


__all__ = [
    "MODEL_PRICING",
    "ModelPricing",
    "calculate_cost_breakdown",
    "pricing_for_model",
    "resolve_model_pricing",
]
