"""Run-budget enforcement and stopping-rule primitives."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from orchestration.ledger import AnalysisLedger


class BudgetResource(StrEnum):
    """Observable resource counters with configured run limits."""

    SPECIALIST_INVOCATIONS = "specialist_invocations"
    SQL_EXECUTIONS = "sql_executions"
    PYTHON_EXECUTIONS = "python_executions"
    CRITIC_LOOPS = "critic_loops"
    CHARTS_CREATED = "charts_created"


_LIMIT_FIELDS = {
    BudgetResource.SPECIALIST_INVOCATIONS: "max_specialist_invocations",
    BudgetResource.SQL_EXECUTIONS: "max_sql_executions",
    BudgetResource.PYTHON_EXECUTIONS: "max_python_executions",
    BudgetResource.CRITIC_LOOPS: "max_critic_loops",
    BudgetResource.CHARTS_CREATED: "max_charts",
}


class BudgetSnapshot(BaseModel):
    """Current usage and remaining capacity for one budget resource."""

    model_config = ConfigDict(extra="forbid")

    resource: BudgetResource
    used: int = Field(ge=0)
    limit: int = Field(ge=0)
    remaining: int = Field(ge=0)


class BudgetExhaustedError(RuntimeError):
    """Raised before work starts when a run resource is exhausted."""

    code = "budget_exhausted"

    def __init__(self, snapshot: BudgetSnapshot) -> None:
        self.snapshot = snapshot
        super().__init__(
            f"run budget exhausted for {snapshot.resource.value}: "
            f"{snapshot.used}/{snapshot.limit} used"
        )


class RunBudgetManager:
    """Check and consume typed usage counters in an analysis ledger."""

    def __init__(self, ledger: AnalysisLedger) -> None:
        self.ledger = ledger

    def snapshot(self, resource: BudgetResource | str) -> BudgetSnapshot:
        """Return current usage, limit, and remaining capacity."""

        resource = BudgetResource(resource)
        with self.ledger.budget_lock:
            budget = self.ledger.budget
            used = getattr(budget, resource.value)
            limit = getattr(budget, _LIMIT_FIELDS[resource])
        return BudgetSnapshot(
            resource=resource,
            used=used,
            limit=limit,
            remaining=max(limit - used, 0),
        )

    def check(self, resource: BudgetResource | str) -> BudgetSnapshot:
        """Raise before work starts if one unit cannot be consumed."""

        snapshot = self.snapshot(resource)
        if snapshot.used >= snapshot.limit:
            raise BudgetExhaustedError(snapshot)
        return snapshot

    def consume(self, resource: BudgetResource | str) -> BudgetSnapshot:
        """Atomically reserve one unit and return the resulting snapshot."""

        resource = BudgetResource(resource)
        self.ledger.reserve_budget(resource.value)
        return self.snapshot(resource)

    def consume_many(
        self,
        *resources: BudgetResource | str,
    ) -> tuple[BudgetSnapshot, ...]:
        """Atomically reserve several resource units as one operation."""

        normalized = tuple(BudgetResource(resource) for resource in resources)
        self.ledger.reserve_budgets(resource.value for resource in normalized)
        return tuple(self.snapshot(resource) for resource in normalized)


# A descriptive alias keeps the boundary easy to discover for callers that
# think of this object as a controller rather than a service.
RunBudgetController = RunBudgetManager
