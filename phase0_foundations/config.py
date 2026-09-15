"""Configuration loader — reads config.yaml into typed dataclasses.

The deterministic thresholds (workflow record B3/A5) live in config.yaml and are
version-controlled. This is the only place thresholds are read; every phase
imports from here so there is a single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from phase0_foundations.fx import FXConfig


@dataclass
class Thresholds:
    collections_variance: float = 0.02
    disbursement_abs: float = 100.0
    disbursement_pct: float = 0.05
    cash_balance_variance: float = 0.01
    anomaly_score_high: float = 0.75
    pdf_confidence_floor: float = 0.85
    transaction_volume_multiple: float = 10.0
    round_dollar_materiality_multiple: float = 3.0
    # SOP-2 "Gross vs. Net Verification": a reported (gross) collections figure
    # exceeding the independently calculated (net) figure by no more than this
    # fraction is treated as plausibly explained by payment-gateway commission,
    # not a raw unexplained variance -- see reconcile.estimate_gateway_fee().
    gateway_fee_max_pct: float = 0.05
    # Per-account trailing-pattern jump: how many times an account's own recent
    # (rolling) average a new amount must exceed to flag, independent of the
    # portfolio-wide median used by the existing volume-anomaly rule -- catches
    # e.g. 1k, 2k, 4k then a sudden 1m on the same account even when the
    # portfolio median is already high enough that the global check misses it.
    sequence_jump_multiple: float = 10.0
    sequence_jump_min_window: int = 3   # need at least this many prior same-account rows to have a "pattern"
    # Off-hours window (local/source-reported clock, 24h) -- only evaluated
    # when a row's date value actually carries a time component; a bare date
    # never triggers this (no time data to judge "off-hours" from).
    off_hours_start_hour: int = 22
    off_hours_end_hour: int = 5
    # Micro-splitting: >= this many same-account, same-day transactions of
    # similar size (within amount_tolerance_pct of each other) whose combined
    # total is material (> median * materiality_multiple) are flagged as a
    # possible structured/split transaction.
    micro_split_min_count: int = 3
    micro_split_amount_tolerance_pct: float = 0.15
    micro_split_materiality_multiple: float = 3.0

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Thresholds:
        return cls(**{k: v for k, v in (d or {}).items() if hasattr(cls, k)})


@dataclass
class LLMConfig:
    parse_temperature: float = 0.3
    anomaly_temperature: float = 0.5
    max_tokens: int = 4000
    provider: str = ""
    model: str = ""
    deepinfra_base_url: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> LLMConfig:
        return cls(**{k: v for k, v in (d or {}).items() if hasattr(cls, k)})


@dataclass
class ServiceConfig:
    """Downstream service connectivity (data-driven infra settings)."""

    api_url: str = "http://127.0.0.1:8001"
    api_key_env: str = "REDSHIFT_API_KEY"

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> ServiceConfig:
        return cls(**{k: v for k, v in (d or {}).items() if hasattr(cls, k)})


@dataclass
class AssetsConfig:
    """Static web/CDN asset URLs referenced by the UI (data-driven)."""

    fonts_url: str = (
        "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700"
        "&family=IBM+Plex+Mono:wght@400;500&display=swap"
    )
    fonts_preconnect: str = "https://fonts.googleapis.com"
    fonts_gstatic: str = "https://fonts.gstatic.com"

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> AssetsConfig:
        return cls(**{k: v for k, v in (d or {}).items() if hasattr(cls, k)})


@dataclass
class AssetVerificationConfig:
    """Column-name candidates for the asset/vehicle-verification upload's
    auto-suggest dropdowns (app/streamlit_app.py's Asset & vehicle
    verification tab). Each list is checked in order, matched by substring
    against the uploaded file's actual headers -- the same keyword approach
    `phase1_ingestion_parsing.assets._find_col` already used, just made
    data-driven so a new exporter's header spelling is a config edit, not a
    code change."""

    plate_columns: tuple[str, ...] = ("plate", "registration", "asset id", "asset_id")
    borrower_columns: tuple[str, ...] = ("borrower",)
    expected_owner_columns: tuple[str, ...] = ("expected_owner", "expected owner", "owner", "propietario")
    expected_vehicle_columns: tuple[str, ...] = (
        "expected_vehicle",
        "expected vehicle",
        "vehicle description",
        "make model",
    )

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> AssetVerificationConfig:
        d = d or {}
        defaults = cls()
        return cls(
            plate_columns=tuple(d.get("plate_columns") or defaults.plate_columns),
            borrower_columns=tuple(d.get("borrower_columns") or defaults.borrower_columns),
            expected_owner_columns=tuple(d.get("expected_owner_columns") or defaults.expected_owner_columns),
            expected_vehicle_columns=tuple(d.get("expected_vehicle_columns") or defaults.expected_vehicle_columns),
        )


@dataclass
class VehiclePath:
    path: str = ""
    extra: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> VehiclePath:
        d = d or {}
        extra = d.get("extra") or []
        return cls(path=d.get("path", ""), extra=tuple(extra))


@dataclass
class VehicleRegistryConfig:
    """Vehicle-registry provider settings (endpoints / base URL, data-driven)."""

    provider: str = "verifik"
    base_url: str = "https://api.verifik.co"
    base_url_env: str = "VERIFIK_BASE_URL"
    token_env: str = "VERIFIK_TOKEN"
    timeout_seconds: int = 15
    param_rename: dict[str, str] = field(default_factory=dict)
    confirmed_paths: dict[str, VehiclePath] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> VehicleRegistryConfig:
        d = d or {}
        rename = d.get("param_rename") or {}
        paths = {
            code: VehiclePath.from_dict(v)
            for code, v in (d.get("confirmed_paths") or {}).items()
        }
        return cls(
            provider=d.get("provider", "verifik"),
            base_url=d.get("base_url", "https://api.verifik.co"),
            base_url_env=d.get("base_url_env", "VERIFIK_BASE_URL"),
            token_env=d.get("token_env", "VERIFIK_TOKEN"),
            timeout_seconds=int(d.get("timeout_seconds", 15)),
            param_rename=dict(rename),
            confirmed_paths=paths,
        )


# Every semantic field normalize_loan_tape_row() and _loan_tape_summary()
# (app/streamlit_app.py) need from a per-loan tape row, mapped to itself --
# i.e. what the code assumed unconditionally before this became configurable.
# A borrower survey (docs/loan_tape_column_survey.md, 2026-09-03) found only
# 1 of 46 dbt_source borrower tables matches this on every money/date field;
# per-borrower differences go in config.yaml's loan_tape_columns.overrides,
# not here.
DEFAULT_LOAN_TAPE_COLUMNS: dict[str, str] = {
    "loan_id": "loan_id",
    "begin_date": "begin_date",
    "principal_amount": "principal_amount",
    "total_loan_amount": "total_loan_amount",
    "principal_outstanding": "principal_outstanding",
    "interest_outstanding": "interest_outstanding",
    "fee_outstanding": "fee_outstanding",
    "penalty_outstanding": "penalty_outstanding",
    "closure_date": "closure_date",
    "company_due_date": "company_due_date",
    "currency": "currency",
    "status": "status",
    "country": "country",
    # Unlike the rest of this table, "product" has not been surveyed against
    # the real dbt_source tables the way docs/loan_tape_column_survey.md did
    # for the other fields -- real per-borrower names vary widely (product,
    # product_type, loan_product, product_group, producttype, ...). Add
    # confirmed per-borrower overrides to config.yaml's loan_tape_columns
    # as they're identified; until then this default silently finds nothing
    # for most borrowers (by_product/product filtering degrades to "(blank)"
    # rather than erroring).
    "product": "product",
    "days_past_due": "days_past_due",
}


@dataclass
class LoanTapeColumnsConfig:
    """Semantic field -> actual column name for dbt_source loan-tape rows,
    resolved per borrower (see docs/loan_tape_column_survey.md)."""

    default: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_LOAN_TAPE_COLUMNS))
    overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    # Borrowers whose money columns (principal_amount, total_loan_amount,
    # principal/interest/fee/penalty_outstanding) use a live negative-sign
    # accounting convention in the source -- confirmed 2026-09-09 by sampling
    # real rows: lendmn/lendmn_revolving's `principal` and autocheck__ci/ug's
    # `principal_out`/`interest_out`/`fees_out`/`penalty_out` are negative on
    # (effectively) every row, not an occasional anomaly. For these, take
    # abs() rather than floor-at-0 -- flooring would silently zero out real
    # debt instead of just fixing the sign. NOT the default behavior for
    # every borrower: a lone/sparse negative elsewhere is more likely a
    # genuine credit/overpayment, where floor-at-0 (nothing owed on that
    # component) is the safer reading -- abs() there would overstate a real
    # credit as more debt. Confirmed per-borrower only; never assumed from a
    # sibling in the same borrower family (lendmn_micro is normally signed).
    negative_sign_borrowers: set[str] = field(default_factory=set)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> LoanTapeColumnsConfig:
        d = d or {}
        default = dict(DEFAULT_LOAN_TAPE_COLUMNS)
        default.update(d.get("default") or {})
        overrides = {b: dict(m) for b, m in (d.get("overrides") or {}).items()}
        negative_sign_borrowers = set(d.get("negative_sign_borrowers") or [])
        return cls(default=default, overrides=overrides, negative_sign_borrowers=negative_sign_borrowers)

    def resolve(self, borrower: str | None) -> dict[str, str]:
        """Semantic field -> actual column name for one borrower: the
        default table with that borrower's overrides (if any) layered on."""
        resolved = dict(self.default)
        if borrower:
            resolved.update(self.overrides.get(borrower, {}))
        return resolved

    def uses_negative_sign(self, borrower: str | None) -> bool:
        return bool(borrower) and borrower in self.negative_sign_borrowers


@dataclass
class BankStatementsConfig:
    """SOP 1 / Drive drop-folder: reads bank/wallet statements humans place in a
    Google Drive folder per borrower, feeds them into reconciliation as the
    independent side, and retains the raw files as the audit trail.

    `inbox_folder` is either a Drive folder id or a folder *name* under the
    user's Drive root. `folder_by_borrower` overrides it per borrower. Requires
    a `drive.readonly` OAuth token (see `scripts/google_oauth_setup.py --feature inbox`);
    without one the watcher degrades to tape-only rather than crashing."""

    enabled: bool = False
    inbox_folder: str = ""
    folder_by_borrower: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> BankStatementsConfig:
        d = d or {}
        return cls(
            enabled=bool(d.get("enabled", False)),
            inbox_folder=str(d.get("inbox_folder", "")),
            folder_by_borrower={str(k): str(v) for k, v in (d.get("folder_by_borrower") or {}).items()},
        )

    def folder_for(self, borrower: str) -> str:
        return self.folder_by_borrower.get(borrower) or self.inbox_folder


@dataclass
class EmailConfig:
    """SOP 1 report email delivery — Gmail API via the project's Google OAuth.

    Sends the working paper (.md + .json) after every change-triggered run
    through the Gmail API (`gmail.send` scope, one-time browser grant in
    `scripts/google_oauth_setup.py --feature gmail`). Used instead of SMTP app passwords
    because corporate Google Workspace accounts commonly have app passwords
    disabled by the domain admin. The token file path is read from `.env` via
    `gmail_token_env`; recipients/from/subject are data-driven config. Disabled
    by default and only meaningful after a real authenticated send is verified
    (repo rule: no external integration goes live on configuration alone)."""

    enabled: bool = False
    gmail_token_env: str = "GOOGLE_OAUTH_GMAIL_TOKEN_JSON"
    from_addr: str = ""
    to_addrs: list[str] = field(default_factory=list)
    subject_prefix: str = "[Verifications] "

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> EmailConfig:
        d = d or {}
        return cls(
            enabled=bool(d.get("enabled", False)),
            gmail_token_env=str(d.get("gmail_token_env", "GOOGLE_OAUTH_GMAIL_TOKEN_JSON")),
            from_addr=str(d.get("from_addr", "")),
            to_addrs=[str(a) for a in (d.get("to_addrs") or [])],
            subject_prefix=str(d.get("subject_prefix", "[Verifications] ")),
        )


@dataclass
class WatcherConfig:
    """SOP 1 / Option A: change-detection watcher settings (poll cadence,
    target borrowers, cut-off guard). Data-driven so tuning is config-only."""

    enabled: bool = False
    interval_seconds: int = 600
    state_file: str = "out/cash_watcher_state.json"
    tape_fetch_limit: int = 0        # 0 = fetch the full tape; positive = sampled window
    run_on_change: bool = True
    borrowers: list[str] = field(default_factory=list)
    cut_off_date: str = ""
    cut_off_mode: str = "backdate"      # "hard_stop" | "backdate"
    reported: dict[str, float] = field(default_factory=dict)
    bank_statements: BankStatementsConfig = field(default_factory=BankStatementsConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    # SOP-1 ("Bi-Weekly Cash and Cash Equivalents Tracking") Step 4 literally
    # specifies "Flag discrepancies > 0.01%" -- 100x tighter than the general
    # cash_balance_variance threshold (1%, Thresholds.cash_balance_variance)
    # used everywhere else in the app. Kept as its own SOP-1-scoped field
    # rather than lowering the shared threshold globally: 0.01% would flag
    # nearly every reconciliation across every other entry point (Run tab,
    # runner.py CLI) on ordinary FX/rounding noise, which isn't what SOP-1's
    # bi-weekly cash-tracking language was written for. Applied only inside
    # cash_watcher.py's own reconciliation call.
    cash_discrepancy_pct: float = 0.0001

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> WatcherConfig:
        d = d or {}
        return cls(
            enabled=bool(d.get("enabled", False)),
            interval_seconds=int(d.get("interval_seconds", 600)),
            state_file=str(d.get("state_file", "out/cash_watcher_state.json")),
            tape_fetch_limit=int(d.get("tape_fetch_limit", 0)),
            run_on_change=bool(d.get("run_on_change", True)),
            borrowers=list(d.get("borrowers") or []),
            cut_off_date=str(d.get("cut_off_date", "")),
            cut_off_mode=str(d.get("cut_off_mode", "backdate")),
            reported={k: float(v) for k, v in (d.get("reported") or {}).items()},
            bank_statements=BankStatementsConfig.from_dict(d.get("bank_statements")),
            email=EmailConfig.from_dict(d.get("email")),
            cash_discrepancy_pct=float(d.get("cash_discrepancy_pct", 0.0001)),
        )


@dataclass
class Config:
    thresholds: Thresholds = field(default_factory=Thresholds)
    llm: LLMConfig = field(default_factory=LLMConfig)
    fx: FXConfig = field(default_factory=FXConfig)
    services: ServiceConfig = field(default_factory=ServiceConfig)
    assets: AssetsConfig = field(default_factory=AssetsConfig)
    vehicle_registry: VehicleRegistryConfig = field(default_factory=VehicleRegistryConfig)
    asset_verification: AssetVerificationConfig = field(default_factory=AssetVerificationConfig)
    loan_tape_columns: LoanTapeColumnsConfig = field(default_factory=LoanTapeColumnsConfig)
    sop1: WatcherConfig = field(default_factory=WatcherConfig)
    log_path: Path = Path("verifications.log.jsonl")
    out_dir: Path = Path("out")

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Config:
        d = d or {}
        cfg = cls(
            thresholds=Thresholds.from_dict(d.get("thresholds")),
            llm=LLMConfig.from_dict(d.get("llm")),
            fx=FXConfig.from_dict(d.get("fx")),
            services=ServiceConfig.from_dict((d.get("services") or {}).get("redshift")),
            assets=AssetsConfig.from_dict(d.get("assets")),
            vehicle_registry=VehicleRegistryConfig.from_dict(d.get("vehicle_registry")),
            asset_verification=AssetVerificationConfig.from_dict(d.get("asset_verification")),
            loan_tape_columns=LoanTapeColumnsConfig.from_dict(d.get("loan_tape_columns")),
            sop1=WatcherConfig.from_dict((d.get("sop1") or {}).get("watcher")),
        )
        logging_cfg = d.get("logging") or {}
        out_cfg = d.get("output") or {}
        if logging_cfg.get("path"):
            cfg.log_path = Path(logging_cfg["path"])
        if out_cfg.get("dir"):
            cfg.out_dir = Path(out_cfg["dir"])
        return cfg


def load_config(path: str | Path | None = None) -> Config:
    """Load a Config from YAML. If path is None, look for ./config.yaml."""
    path = Path(path) if path else Path("config.yaml")
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return Config.from_dict(data)
