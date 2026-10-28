"""Explicit request-level Compute configuration, independent of plan contents."""
from dataclasses import dataclass, fields, replace
import json
from pathlib import Path

from simulator.system.tile_memory import TileMemoryConfig


@dataclass(frozen=True)
class TileComputeConfig:
    memory: TileMemoryConfig = TileMemoryConfig()
    operand_slots: int = 2
    mac_interval: int = 1

    def __post_init__(self):
        if not isinstance(self.memory, TileMemoryConfig):
            raise ValueError("memory must be TileMemoryConfig")
        if any(type(v) is not int or v < 1 for v in (self.operand_slots, self.mac_interval)):
            raise ValueError("operand_slots and mac_interval must be positive integers")
        if self.memory.weight.word_bytes % 4 or self.memory.result.word_bytes % 4:
            raise ValueError("Weight/Result word_bytes must be a multiple of four for the fixed Tile layout")

    @classmethod
    def from_dict(cls, payload):
        """Accept partial overrides while preserving per-region defaults."""
        if not isinstance(payload, dict):
            raise ValueError("Tile configuration must be a JSON object")
        unknown = set(payload) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"unknown Tile configuration fields: {sorted(unknown)}")
        values = dict(payload)
        if 'memory' in values:
            overrides = values['memory']
            if not isinstance(overrides, dict):
                raise ValueError("memory must be a JSON object")
            baseline = TileMemoryConfig()
            unknown = set(overrides) - {f.name for f in fields(baseline)}
            if unknown:
                raise ValueError(f"unknown memory configuration fields: {sorted(unknown)}")
            memory = {}
            for name, value in overrides.items():
                if name == 'share_weight_input':
                    memory[name] = value
                else:
                    if not isinstance(value, dict):
                        raise ValueError(f"{name} geometry must be a JSON object")
                    try:
                        memory[name] = replace(getattr(baseline, name), **value)
                    except TypeError as error:
                        raise ValueError(f"invalid {name} geometry: {error}") from error
            values['memory'] = replace(baseline, **memory)
        return cls(**values)

    @classmethod
    def load(cls, path):
        try:
            return cls.from_dict(json.loads(Path(path).read_text(encoding='utf-8')))
        except (OSError, ValueError) as error:
            raise ValueError(f"invalid Tile configuration {path}: {error}") from error
