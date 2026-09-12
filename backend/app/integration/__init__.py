"""ERP integration layer (docs/DESIGN_CONTRACT.md §8).

Read-only connectors produce raw records; the normalizer maps them to the
domain model; ``SyncService`` persists them and records every run. Import
order matters: ``codes``/``parsers`` are pulled in first because the field
maps import them through this package.

The mock connector is *not* re-exported here because it depends on the
``synthetic`` package; obtain it through ``ConnectorRegistry.default()``.
"""

from __future__ import annotations

from app.integration import codes, parsers
from app.integration.capabilities import CapabilityReport, FieldAssessment, RequiredField, assess_capabilities
from app.integration.connector import (
    ENTITY_NAMES,
    ConnectorCapabilities,
    ConnectorFactory,
    ConnectorHealth,
    ConnectorRegistry,
    ERPConnector,
    RawRecord,
    count_by_entity,
)
from app.integration.field_maps import FIELD_MAPS, FieldMap, target_fields
from app.integration.normalizer import (
    NormalizationIssue,
    NormalizationIssueCode,
    NormalizationResult,
    Normalizer,
    ProductionStatusUpdate,
)
from app.integration.reconciliation import (
    EntityDelta,
    ReconciliationReport,
    ReconciliationStatus,
    ReconciliationThresholds,
    reconcile,
)
from app.integration.retry import RetryPolicy, SleepFn, call_with_retry
from app.integration.snapshot_builder import (
    FetchBundle,
    build_snapshot_from_connector,
    build_snapshot_from_records,
    fetch_all,
)
from app.integration.sync_service import (
    SYNC_STATUS_COMPLETED,
    SYNC_STATUS_FAILED,
    SYNC_STATUS_RUNNING,
    SyncIssue,
    SyncIssueCode,
    SyncOptions,
    SyncRunSummary,
    SyncService,
    SyncStage,
)
from app.integration.writeback import (
    AutoPublishRule,
    MockWritebackGateway,
    ReadOnlyWritebackGateway,
    WritebackGateway,
    WritebackReceipt,
    WritebackStatus,
    check_publish_preconditions,
)

__all__ = [
    "ENTITY_NAMES",
    "FIELD_MAPS",
    "SYNC_STATUS_COMPLETED",
    "SYNC_STATUS_FAILED",
    "SYNC_STATUS_RUNNING",
    "AutoPublishRule",
    "CapabilityReport",
    "ConnectorCapabilities",
    "ConnectorFactory",
    "ConnectorHealth",
    "ConnectorRegistry",
    "ERPConnector",
    "EntityDelta",
    "FetchBundle",
    "FieldAssessment",
    "FieldMap",
    "MockWritebackGateway",
    "NormalizationIssue",
    "NormalizationIssueCode",
    "NormalizationResult",
    "Normalizer",
    "ProductionStatusUpdate",
    "RawRecord",
    "ReadOnlyWritebackGateway",
    "ReconciliationReport",
    "ReconciliationStatus",
    "ReconciliationThresholds",
    "RequiredField",
    "RetryPolicy",
    "SleepFn",
    "SyncIssue",
    "SyncIssueCode",
    "SyncOptions",
    "SyncRunSummary",
    "SyncService",
    "SyncStage",
    "WritebackGateway",
    "WritebackReceipt",
    "WritebackStatus",
    "assess_capabilities",
    "build_snapshot_from_connector",
    "build_snapshot_from_records",
    "call_with_retry",
    "check_publish_preconditions",
    "codes",
    "count_by_entity",
    "fetch_all",
    "parsers",
    "reconcile",
    "target_fields",
]
