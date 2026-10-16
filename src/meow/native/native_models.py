"""Resolve native dispatch models without changing Claude SDK configuration."""

from collections.abc import Sequence


def resolve_native_model(
    configured_model: str | None,
    *,
    host: str | None = None,
    available_models: Sequence[str] | None = None,
) -> dict:
    """Return a usable dispatch choice and the reason for inheriting a default.

    An omitted host preserves legacy Claude dispatch. Other hosts must advertise
    the exact configured ID; provider model names are never translated. An
    explicit capability list is authoritative for every host, including Claude.
    """
    resolved_host = host.strip().lower() if host is not None else "claude"
    reason = None
    model = configured_model
    resolution = "inherit" if configured_model is None else "configured"
    if configured_model is not None:
        if available_models is not None and configured_model not in available_models:
            reason = (
                f"Configured model {configured_model!r} is unavailable on host "
                f"{resolved_host!r}; inherit the host default."
            )
        elif available_models is None and resolved_host != "claude":
            reason = (
                f"Host {resolved_host!r} capabilities are unspecified; cannot "
                f"confirm configured model {configured_model!r}. "
                "Inherit the host default."
            )
        if reason:
            model, resolution = None, "fallback"
    return {
        "model": model,
        "configured_model": configured_model,
        "host": resolved_host,
        "model_resolution": resolution,
        "model_fallback_reason": reason,
    }
