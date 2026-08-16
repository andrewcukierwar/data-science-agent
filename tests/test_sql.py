"""Unit tests for approved-workspace DuckDB execution."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from orchestration.ledger import AnalysisLedger
from scenarios.generator import SyntheticEcommerceConfig, SyntheticEcommerceGenerator
from tools.sql import DuckDBExecutionService, InputRelationError
from tools.workspace import WorkspaceManager


class RecordingLedger:
    """Small test double for the future persistent Analysis Ledger."""

    def __init__(self) -> None:
        self.events = []
        self.sql_executions = 0

    def append_tool_event(self, event) -> None:  # noqa: ANN001
        self.events.append(event)

    def increment_budget(self, **usage: int) -> None:
        self.sql_executions += usage["sql_executions"]


def _workspace_with_csv(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "orders.csv").write_text(
        "customer_id,revenue\nC001,12.50\nC002,8.00\n",
        encoding="utf-8",
    )
    return WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-sql", inputs_source=source
    )


def _write_parquet(path: Path, values: list[int]) -> None:
    pq.write_table(pa.table({"value": values}), path)


def _workspace_with_parquet(tmp_path: Path, *names: str):
    source = tmp_path / "source"
    source.mkdir()
    for index, name in enumerate(names):
        _write_parquet(source / name, [index, index + 1, index + 2])
    return WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-sql", inputs_source=source
    )


def test_execute_queries_approved_input_and_records_success(
    tmp_path: Path,
) -> None:
    workspace = _workspace_with_csv(tmp_path)
    ledger = RecordingLedger()
    service = DuckDBExecutionService(workspace, ledger)
    input_path = workspace.inputs / "orders.csv"
    sql = (
        "SELECT customer_id, revenue "
        f"FROM read_csv_auto('{input_path}') ORDER BY customer_id"
    )

    result = service.execute(sql, query_id="Q001")

    assert result.success is True
    assert result.columns == ["customer_id", "revenue"]
    assert result.rows == [["C001", 12.5], ["C002", 8.0]]
    assert result.row_count == 2
    assert result.query_path.read_text(encoding="utf-8") == sql

    assert len(ledger.events) == 1
    event = ledger.events[0]
    assert event.status.value == "succeeded"
    assert event.tool_name == "run_sql"
    assert event.artifact_refs == ["working/queries/Q001.sql"]
    assert event.output == {
        "columns": result.columns,
        "row_count": 2,
        "max_rows": 10_000,
        "truncated": False,
        "truncation_message": None,
    }
    assert ledger.sql_executions == 1


def test_parquet_inputs_are_registered_as_read_only_views(tmp_path: Path) -> None:
    workspace = _workspace_with_parquet(
        tmp_path, "customers.parquet", "marketing_spend.parquet"
    )
    service = DuckDBExecutionService(workspace)

    result = service.execute(
        "SELECT * FROM customers ORDER BY value LIMIT 3", query_id="Q-VIEWS"
    )
    catalog = service.execute(
        "SELECT table_name, table_type "
        "FROM information_schema.tables "
        "WHERE table_schema = 'main' "
        "ORDER BY table_name",
        query_id="Q-CATALOG",
    )

    assert result.success is True
    assert result.columns == ["value"]
    assert result.rows == [[0], [1], [2]]
    assert set(service.input_relations) == {"customers", "marketing_spend"}
    assert catalog.success is True
    assert catalog.rows == [
        ["customers", "VIEW"],
        ["marketing_spend", "VIEW"],
    ]


def test_inspect_relations_returns_exact_canonical_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dataset = SyntheticEcommerceGenerator(
        SyntheticEcommerceConfig(
            seed=42,
            num_customers=10,
            num_orders=20,
            num_sessions=30,
            num_products=3,
            period_days=30,
        )
    ).generate()
    dataset.write(source)
    workspace = WorkspaceManager(tmp_path / "workspaces").create_workspace(
        "run-relations",
        inputs_source=source,
    )
    ledger = RecordingLedger()
    service = DuckDBExecutionService(workspace, ledger)

    result = service.inspect_relations()

    assert result.total_relations == 4
    assert result.truncated is False
    assert result.row_counts_included is True
    relations = {item.relation_name: item for item in result.relations}
    assert set(relations) == {"customers", "orders", "sessions", "marketing_spend"}
    assert relations["marketing_spend"].source_path == "marketing_spend.parquet"
    assert relations["marketing_spend"].row_count == 30 * 5
    assert [
        (column.name, column.data_type)
        for column in relations["marketing_spend"].columns
    ] == [
        ("date", "TIMESTAMP"),
        ("channel", "VARCHAR"),
        ("spend", "DOUBLE"),
        ("impressions", "BIGINT"),
        ("clicks", "BIGINT"),
    ]
    assert [column.name for column in relations["customers"].columns] == [
        "customer_id",
        "acquisition_date",
        "acquisition_channel",
        "region",
        "device",
    ]
    assert [column.name for column in relations["orders"].columns] == [
        "order_id",
        "customer_id",
        "order_date",
        "product_id",
        "quantity",
        "gross_revenue",
        "discount",
        "refund",
        "net_revenue",
        "cogs",
    ]
    assert [column.name for column in relations["sessions"].columns] == [
        "session_id",
        "session_date",
        "channel",
        "device",
        "converted",
        "customer_id",
    ]
    assert ledger.sql_executions == 1
    assert ledger.events[0].tool_name == "inspect_relations"
    assert ledger.events[0].status.value == "succeeded"


def test_parquet_relation_stems_are_sanitized_deterministically(tmp_path: Path) -> None:
    workspace = _workspace_with_parquet(
        tmp_path, "marketing-spend.data.parquet", "1orders.parquet"
    )
    service = DuckDBExecutionService(workspace)

    assert set(service.input_relations) == {"marketing_spend_data", "_1orders"}
    assert service.execute("SELECT count(*) AS rows FROM _1orders").rows == [[3]]


def test_duplicate_sanitized_parquet_relation_names_are_rejected(
    tmp_path: Path,
) -> None:
    workspace = _workspace_with_parquet(
        tmp_path, "marketing-spend.parquet", "marketing spend.parquet"
    )

    with pytest.raises(InputRelationError, match="duplicate approved input relation"):
        DuckDBExecutionService(workspace)


def test_parquet_stems_without_a_safe_relation_name_are_rejected(
    tmp_path: Path,
) -> None:
    workspace = _workspace_with_parquet(tmp_path, "!!!.parquet")

    with pytest.raises(InputRelationError, match="unsafe Parquet input stem"):
        DuckDBExecutionService(workspace)


def test_execute_captures_duckdb_errors_and_records_failure(tmp_path: Path) -> None:
    workspace = _workspace_with_csv(tmp_path)
    ledger = RecordingLedger()
    service = DuckDBExecutionService(workspace, ledger)

    result = service.execute("SELECT * FROM missing_table", query_id="Q002")

    assert result.success is False
    assert result.rows == []
    assert result.row_count == 0
    assert result.error is not None
    assert "missing_table" in result.error
    assert result.query_path.is_file()
    assert ledger.events[0].status.value == "failed"
    assert ledger.events[0].error == result.error
    assert ledger.sql_executions == 1


def test_execute_bounds_materialization_and_explains_truncation(
    tmp_path: Path,
) -> None:
    workspace = _workspace_with_csv(tmp_path)
    ledger = RecordingLedger()
    service = DuckDBExecutionService(workspace, ledger, max_rows=2)

    result = service.execute(
        "SELECT value FROM range(5) AS generated(value) ORDER BY value",
        query_id="Q-TRUNCATED",
    )

    assert result.success is True
    assert result.rows == [[0], [1]]
    assert result.row_count == 2
    assert result.max_rows == 2
    assert result.truncated is True
    assert result.truncation_message is not None
    assert "Aggregate or filter" in result.truncation_message
    assert ledger.events[0].output == {
        "columns": ["value"],
        "row_count": 2,
        "max_rows": 2,
        "truncated": True,
        "truncation_message": result.truncation_message,
    }
    assert ledger.sql_executions == 1


def test_max_rows_must_be_a_reasonable_positive_bound(tmp_path: Path) -> None:
    workspace = _workspace_with_csv(tmp_path)

    with pytest.raises(ValueError):
        DuckDBExecutionService(workspace, max_rows=0)
    with pytest.raises(ValueError):
        DuckDBExecutionService(workspace, max_rows=100_001)


def test_execute_rejects_external_files(tmp_path: Path) -> None:
    workspace = _workspace_with_csv(tmp_path)
    outside = tmp_path / "outside.csv"
    outside.write_text("value\nnot-approved\n", encoding="utf-8")
    service = DuckDBExecutionService(workspace)

    result = service.execute(
        f"SELECT * FROM read_csv_auto('{outside}')", query_id="Q003"
    )

    assert result.success is False
    assert result.error is not None
    assert "Permission" in result.error


def test_execute_rejects_external_parquet_even_when_approved_views_exist(
    tmp_path: Path,
) -> None:
    workspace = _workspace_with_parquet(tmp_path, "customers.parquet")
    outside = tmp_path / "outside.parquet"
    _write_parquet(outside, [99])
    service = DuckDBExecutionService(workspace)

    result = service.execute(
        f"SELECT * FROM read_parquet('{outside}')", query_id="Q-OUTSIDE-PARQUET"
    )

    assert result.success is False
    assert result.error is not None
    assert "Permission" in result.error


def test_query_ids_cannot_escape_query_artifact_directory(tmp_path: Path) -> None:
    workspace = _workspace_with_csv(tmp_path)
    service = DuckDBExecutionService(workspace)

    with pytest.raises(ValueError):
        service.execute("SELECT 1", query_id="../escape")

    assert not (workspace.working / "escape.sql").exists()


def test_sql_events_persist_through_analysis_ledger(tmp_path: Path) -> None:
    workspace = _workspace_with_csv(tmp_path)
    ledger = AnalysisLedger(workspace, objective="Validate SQL event persistence.")
    service = DuckDBExecutionService(workspace, ledger)

    result = service.execute("SELECT 1 AS value", query_id="Q004")
    reloaded = AnalysisLedger(ledger.state_path)

    assert result.success is True
    assert len(reloaded.tool_events) == 1
    assert reloaded.tool_events[0].id == "tool-Q004"
    assert reloaded.tool_events[0].artifact_refs == ["working/queries/Q004.sql"]
    assert reloaded.budget.sql_executions == 1
