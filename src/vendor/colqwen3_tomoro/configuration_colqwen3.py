# Provenance:
# Repo: TomoroAI/tomoro-colqwen3-embed-4b
# Revision: bf790bd8780b098b86453444632a184bb770be1a
# License: Apache-2.0
# Reason for vendoring: Remote configuration_colqwen3.py has a bug where a mutable default dictionary is used for sub_configs without default_factory, breaking on python 3.11 dataclass. We vendored to apply a local one-line patch.
"""
# Copyright 2025 The HuggingFace Inc. team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

Configuration for ColQwen3, adapted to mirror the ColQwen2 structure.
"""

from copy import deepcopy
from typing import Any, ClassVar

from transformers.configuration_utils import PretrainedConfig
from transformers.models.auto import CONFIG_MAPPING
from transformers.models.qwen3_vl.configuration_qwen3_vl import Qwen3VLTextConfig, Qwen3VLVisionConfig
from transformers.utils import logging


logger = logging.get_logger(__name__)


class ColQwen3Config(PretrainedConfig):
    """Configuration for ColQwen3 retrieval model."""

    model_type = "colqwen3"
    sub_configs: ClassVar[dict[str, Any]] = {"vision_config": Qwen3VLVisionConfig, "text_config": Qwen3VLTextConfig}

    def __init__(
        self,
        vision_config: Any = None,
        text_config: Any = None,
        embed_dim: int = 320,
        padding_side: str = "left",
        initializer_range: float = 0.02,
        dtype: str | None = None,
        **kwargs,
    ):
        if vision_config is None or text_config is None:
            base_vlm_config = CONFIG_MAPPING["qwen3_vl"]()
            if vision_config is None:
                vision_config = deepcopy(base_vlm_config.vision_config)
                logger.info("`vision_config` is `None`. Initializing with the default `Qwen3VLVisionConfig`.")
            if text_config is None:
                text_config = deepcopy(base_vlm_config.text_config)
                logger.info("`text_config` is `None`. Initializing with the default `Qwen3VLTextConfig`.")

        if isinstance(vision_config, dict):
            vision_config = Qwen3VLVisionConfig(**deepcopy(vision_config))
        elif not isinstance(vision_config, PretrainedConfig):
            raise TypeError(
                f"Invalid type for `vision_config`. Expected `PretrainedConfig`, `dict`, or `None`, got {type(vision_config)}."
            )

        if isinstance(text_config, dict):
            text_config = Qwen3VLTextConfig(**deepcopy(text_config))
        elif not isinstance(text_config, PretrainedConfig):
            raise TypeError(
                f"Invalid type for `text_config`. Expected `PretrainedConfig`, `dict`, or `None`, got {type(text_config)}."
            )

        if embed_dim <= 0:
            raise ValueError(f"`embed_dim` must be positive, got {embed_dim}.")

        self.vision_config = vision_config
        self.text_config = text_config
        self.embed_dim = embed_dim
        self.padding_side = padding_side
        self.initializer_range = initializer_range
        super().__init__(**kwargs)
        
        # Preserve incoming dtype so downstream models avoid attribute errors
        self.dtype = dtype or getattr(self, "dtype", None)

    @classmethod
    def from_base_config(cls, base_config: PretrainedConfig) -> "ColQwen3Config":
        """Upgrade a base Qwen3VLConfig-like config into ColQwen3Config."""
        if isinstance(base_config, dict):
            data = dict(base_config)
        else:
            data = base_config.to_dict()

        vision_cfg = data.get("vision_config")
        if isinstance(vision_cfg, dict):
            data["vision_config"] = Qwen3VLVisionConfig.from_dict(vision_cfg)

        text_cfg = data.get("text_config")
        if isinstance(text_cfg, dict):
            data["text_config"] = Qwen3VLTextConfig.from_dict(text_cfg)

        data.setdefault("model_type", cls.model_type)
        if hasattr(base_config, "dtype"):
            data.setdefault("dtype", getattr(base_config, "dtype"))
        elif hasattr(base_config, "torch_dtype") and base_config.torch_dtype is not None:
            data.setdefault("dtype", str(base_config.torch_dtype))

        return cls.from_dict(data)

    def get_text_config(self, *args, **kwargs) -> PretrainedConfig:
        return self.text_config


DEFAULT_CONFIG = ColQwen3Config()

__all__ = ["ColQwen3Config", "DEFAULT_CONFIG"]
