"""Shared helpers for querying and parsing systemd service state over SSH.

`systemctl show` reports machine-readable properties (LoadState=...,
ActiveState=...) as fixed English keywords regardless of the remote host's
locale, unlike `systemctl status`, whose text is translated.
"""

import shlex

PENDING = ("…", "#888888")

# The whole machine could not be contacted — distinct from a service-level
# problem, so it renders as muted grey rather than an alarming colour.
UNREACHABLE = ("UNREACHABLE", "#6f6f6f")


def _state_from_props(load, active):
    """Map LoadState/ActiveState keywords to a (label, colour) pill."""
    if load in ("not-found", "masked"):
        return "NOT FOUND", "#aaaaaa"
    if active == "active":
        return "ACTIVE", "#2fa572"
    if active == "failed":
        return "FAILED", "#e74c3c"
    if active in ("activating", "reloading"):
        return "STARTING", "#d48806"
    if active == "deactivating":
        return "STOPPING", "#d48806"
    if active == "inactive":
        return "STOPPED", "#e74c3c"
    return "ERROR", "#d48806"


def _parse_props(text):
    """Parse KEY=value lines into a dict with lowercased values."""
    props = {}
    for line in (text or "").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            props[key.strip()] = value.strip().lower()
    return props


def parse_service_state(ok, raw):
    """Map a single-service `systemctl show` result to a (label, colour) pill."""
    if not ok:
        return "ERROR", "#d48806"

    props = _parse_props(raw)
    load = props.get("LoadState", "")
    active = props.get("ActiveState", "")

    if not active and "not found" in (raw or "").lower():
        return "NOT FOUND", "#aaaaaa"
    return _state_from_props(load, active)


def batch_status_command(services):
    """Return one `systemctl show` command covering several services."""
    quoted = " ".join(shlex.quote(s) for s in services)
    return f"systemctl show {quoted} --property=Id,LoadState,ActiveState"


def parse_batch_states(ok, raw, services):
    """Map a batched `systemctl show` result to {service: (label, colour)}.

    systemctl emits one blank-line-separated block per requested unit, in
    request order; the Id property is used as a cross-check where present.
    """
    if not ok:
        # Connection-level failure: the machine itself is unreachable.
        return {s: UNREACHABLE for s in services}

    blocks = [b for b in (raw or "").strip().split("\n\n") if b.strip()]
    parsed = [_parse_props(b) for b in blocks]

    results = {}

    # Prefer matching by Id (systemd normalises names, e.g. sshd -> sshd.service).
    by_id = {}
    for props in parsed:
        unit_id = props.get("Id", "")
        if unit_id:
            by_id[unit_id] = props
            by_id.setdefault(unit_id.removesuffix(".service"), props)

    for index, service in enumerate(services):
        props = by_id.get(service) or by_id.get(f"{service}.service")
        if props is None and len(parsed) == len(services):
            props = parsed[index]

        if props is None:
            results[service] = ("ERROR", "#d48806")
        else:
            results[service] = _state_from_props(
                props.get("LoadState", ""), props.get("ActiveState", "")
            )

    return results
