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

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Thresholds":
        return cls(**{k: v for k, v in (d or {}).items() if hasattr(cls, k)})


@dataclass
class LLMConfig:
    parse_temperature: float = 0.3
    anomaly_temperature: float = 0.5
    max_tokens: int = 4000
    provider: str = ""
    model: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "LLMConfig":
        return cls(**{k: v for k, v in (d or {}).items() if hasattr(cls, k)})


@dataclass
class Config:
    thresholds: Thresholds = field(default_factory=Thresholds)
    llm: LLMConfig = field(default_factory=LLMConfig)
    log_path: Path = Path("verifications.log.jsonl")
    out_dir: Path = Path("out")

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Config":
        d = d or {}
        cfg = cls(
            thresholds=Thresholds.from_dict(d.get("thresholds")),
            llm=LLMConfig.from_dict(d.get("llm")),
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
