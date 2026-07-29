"""Provider-level conformance checks for reloaded SourceBundle objects."""

from __future__ import annotations

from pathlib import Path

from ..models import SourceBundle, SourceBundleError


def structured_capture_path(
    bundle: SourceBundle,
    *,
    source_type: str,
) -> Path:
    """Return the sole structured capture path after checking its contract."""

    if bundle.identity.source_type != source_type:
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            f"{source_type} adapter 收到其它类型 bundle",
            path="identity.source_type",
        )
    if len(bundle.components) != 1:
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            f"{source_type} bundle 必须且只能包含一个 snapshot component",
            path="components",
        )
    component = bundle.components[0]
    if (
        component.name != "snapshot"
        or component.kind != "records"
        or component.mode != "canonical-json"
        or component.json_pointer != "/snapshot"
    ):
        raise SourceBundleError(
            "SOURCE_BUNDLE_ADAPTER_INVALID",
            f"{source_type} snapshot component 契约不一致",
            path="components[0]",
        )
    if bundle.snapshot_hash is None:
        raise SourceBundleError(
            "SOURCE_BUNDLE_HASH_MISMATCH",
            f"{source_type} bundle 缺少 snapshot_hash",
            path="snapshot_hash",
        )
    return Path(component.path)


def require_rebuilt_bundle_match(
    supplied: SourceBundle,
    rebuilt: SourceBundle,
    *,
    source_type: str,
) -> None:
    """Compare provider-derived fields while ignoring transaction payload hash."""

    supplied_value = supplied.to_dict()
    rebuilt_value = rebuilt.to_dict()
    supplied_value.pop("payload_hash", None)
    rebuilt_value.pop("payload_hash", None)
    if supplied_value != rebuilt_value:
        raise SourceBundleError(
            "SOURCE_BUNDLE_PROVIDER_MISMATCH",
            f"{source_type} bundle 与其 capture 派生结果不一致",
            path="bundle",
        )
