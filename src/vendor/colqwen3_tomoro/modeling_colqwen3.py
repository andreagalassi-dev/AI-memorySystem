# Provenance:
# Repo: TomoroAI/tomoro-colqwen3-embed-4b
# Revision: bf790bd8780b098b86453444632a184bb770be1a
# License: Apache-2.0
# Reason for vendoring: Patches applied: #1 ClassVar sub_configs (in configuration_colqwen3.py), #2 firma tie_weights
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

Modeling for ColQwen3 retrieval, aligned with the ColQwen2 reference implementation.
"""

from dataclasses import dataclass
from typing import Optional

from torch import nn
from transformers import AutoModelForImageTextToText
from transformers.configuration_utils import PretrainedConfig
from transformers.cache_utils import Cache
from transformers.modeling_utils import PreTrainedModel
from transformers.utils import ModelOutput, auto_docstring, can_return_tuple, is_torch_available, logging
from transformers.models.qwen3_vl.configuration_qwen3_vl import Qwen3VLConfig

from .configuration_colqwen3 import ColQwen3Config


if is_torch_available():
    import torch

logger = logging.get_logger(__name__)


@auto_docstring
class ColQwen3PreTrainedModel(PreTrainedModel):
    config_class = ColQwen3Config
    base_model_prefix = "model"
    _no_split_modules = []
    _supports_sdpa = True
    _supports_flash_attn = True
    _supports_flex_attn = True

    def _init_weights(self, module):
        std = (
            self.config.initializer_range
            if hasattr(self.config, "initializer_range")
            else getattr(self.config.text_config, "initializer_range", 0.02)
        )

        if isinstance(module, (nn.Linear, nn.Conv2d)):
            module.weight.data.normal_(mean=0.0, std=std)
            if module.bias is not None:
                module.bias.data.zero_()
        elif isinstance(module, nn.Embedding):
            module.weight.data.normal_(mean=0.0, std=std)
            if module.padding_idx is not None:
                module.weight.data[module.padding_idx].zero_()


@dataclass
@auto_docstring(
    custom_intro="""
    Base class for ColQwen3 embeddings output.
    """
)
class ColQwen3ForRetrievalOutput(ModelOutput):
    r"""
    embeddings (`torch.FloatTensor` of shape `(batch_size, sequence_length, hidden_size)`):
        The embeddings of the model.
    past_key_values (`Cache`, *optional*, returned when `use_cache=True` is passed or when `config.use_cache=True`):
        It is a [`~cache_utils.Cache`] instance.
    """

    loss: Optional[torch.FloatTensor] = None
    embeddings: Optional[torch.Tensor] = None
    past_key_values: Optional[Cache] = None
    hidden_states: Optional[tuple[torch.FloatTensor]] = None
    attentions: Optional[tuple[torch.FloatTensor]] = None


@auto_docstring(
    custom_intro="""
    ColQwen3 retrieval model that mirrors the ColQwen2 late-interaction pipeline while using a Qwen3-VL backbone.
    """
)
class ColQwen3(ColQwen3PreTrainedModel):
    _checkpoint_conversion_mapping = {
        # Legacy checkpoints saved from a bare Qwen3VLModel (no `vlm.` nesting).
        r"^model\.visual": "vlm.model.visual",
        r"^model\.language_model": "vlm.model.language_model",
        r"^model\.": "vlm.model.",
        r"^visual": "vlm.model.visual",
        r"^language_model": "vlm.model.language_model",
        r"^custom_text_proj": "embedding_proj_layer",
    }
    config_class = ColQwen3Config
    model_type = ColQwen3Config.model_type

    def __init__(
        self,
        config: ColQwen3Config,
        attn_impl: Optional[str] = None,
        mask_non_image_embeddings: bool = False,
    ):
        """
        Args:
            config (ColQwen3Config): Configuration carrying nested vision/text configs for the retrieval model.
            attn_impl (Optional[str], optional): Attention implementation forwarded to the VLM (e.g., "flash_attention_2"). Defaults to None.
            mask_non_image_embeddings (bool, optional): If True, zero out non-image embeddings after projection. Defaults to False.
        """
        super().__init__(config)
        self.config = config

        vision_cfg = (
            config.vision_config.to_dict() if isinstance(config.vision_config, PretrainedConfig) else config.vision_config
        )
        text_cfg = config.text_config.to_dict() if isinstance(config.text_config, PretrainedConfig) else config.text_config

        vlm_config = Qwen3VLConfig(
            text_config=text_cfg,
            vision_config=vision_cfg,
            image_token_id=getattr(config, "image_token_id", 151655),
            video_token_id=getattr(config, "video_token_id", 151656),
            vision_start_token_id=getattr(config, "vision_start_token_id", 151652),
            vision_end_token_id=getattr(config, "vision_end_token_id", 151653),
            tie_word_embeddings=getattr(config.text_config, "tie_word_embeddings", False),
        )
        self.vlm = AutoModelForImageTextToText.from_config(vlm_config)

        self.embedding_dim = self.config.embed_dim
        self.embedding_proj_layer = nn.Linear(
            self.vlm.config.text_config.hidden_size,
            self.embedding_dim,
        )
        self.padding_side = getattr(config, "padding_side", "left")
        self.mask_non_image_embeddings = mask_non_image_embeddings
        self._tied_weights_keys = [f"vlm.{k}" for k in (self.vlm._tied_weights_keys or [])]

        self.post_init()

        if attn_impl is not None and hasattr(self.vlm, "set_attn_implementation"):
            self.vlm.set_attn_implementation(attn_impl)

    @classmethod
    def from_pretrained(cls, *args, config: Optional[ColQwen3Config] = None, **kwargs):
        key_mapping = kwargs.pop("key_mapping", None)
        if key_mapping is None:
            key_mapping = getattr(cls, "_checkpoint_conversion_mapping", None)

        return super().from_pretrained(*args, config=config, **kwargs, key_mapping=key_mapping)

    @can_return_tuple
    @auto_docstring
    def forward(
        self,
        input_ids: Optional[torch.LongTensor] = None,
        attention_mask: Optional[torch.Tensor] = None,
        position_ids: Optional[torch.LongTensor] = None,
        past_key_values: Optional[Cache] = None,
        inputs_embeds: Optional[torch.FloatTensor] = None,
        labels: Optional[torch.LongTensor] = None,
        use_cache: Optional[bool] = None,
        output_attentions: Optional[bool] = None,
        output_hidden_states: Optional[bool] = None,
        return_dict: Optional[bool] = None,
        pixel_values: Optional[torch.Tensor] = None,
        image_grid_thw: Optional[torch.LongTensor] = None,
        cache_position: Optional[torch.LongTensor] = None,
        pixel_values_videos: Optional[torch.Tensor] = None,
        video_grid_thw: Optional[torch.LongTensor] = None,
    ) -> ColQwen3ForRetrievalOutput:
        r"""
        image_grid_thw (`torch.LongTensor` of shape `(num_images, 3)`, *optional*):
            The temporal, height and width of feature shape of each image in LLM.
        video_grid_thw (`torch.LongTensor` of shape `(num_videos, 3)`, *optional*):
            The temporal, height and width of feature shape of each video in LLM.
        """
        output_attentions = output_attentions if output_attentions is not None else self.config.output_attentions
        output_hidden_states = (
            output_hidden_states if output_hidden_states is not None else self.config.output_hidden_states
        )
        return_dict = return_dict if return_dict is not None else self.config.use_return_dict

        vlm_output = self.vlm.model(
            input_ids=input_ids,
            position_ids=position_ids,
            attention_mask=attention_mask,
            past_key_values=past_key_values,
            inputs_embeds=inputs_embeds,
            pixel_values_videos=pixel_values_videos,
            use_cache=use_cache,
            output_attentions=output_attentions,
            output_hidden_states=output_hidden_states,
            return_dict=return_dict,
            pixel_values=pixel_values,
            image_grid_thw=image_grid_thw,
            video_grid_thw=video_grid_thw,
            cache_position=cache_position,
        )

        vlm_hidden_states = vlm_output.hidden_states if output_hidden_states else None

        last_hidden_states = vlm_output[0]
        proj_dtype = self.embedding_proj_layer.weight.dtype
        embeddings = self.embedding_proj_layer(last_hidden_states.to(proj_dtype))

        denom = embeddings.norm(dim=-1, keepdim=True).clamp_min(torch.finfo(embeddings.dtype).eps)
        embeddings = embeddings / denom
        if attention_mask is not None:
            embeddings = embeddings * attention_mask.unsqueeze(-1)

        if pixel_values is not None and self.mask_non_image_embeddings:
            image_mask = (input_ids == self.vlm.config.image_token_id).unsqueeze(-1)
            embeddings = embeddings * image_mask

        return ColQwen3ForRetrievalOutput(
            embeddings=embeddings,
            past_key_values=vlm_output.past_key_values,
            hidden_states=vlm_hidden_states,
            attentions=vlm_output.attentions,
        )

    def get_input_embeddings(self):
        return self.vlm.get_input_embeddings()

    def set_input_embeddings(self, value):
        self.vlm.set_input_embeddings(value)

    def get_output_embeddings(self):
        return self.vlm.get_output_embeddings()

    def set_output_embeddings(self, new_embeddings):
        self.vlm.set_output_embeddings(new_embeddings)

    def tie_weights(self, missing_keys: set[str] | None = None,
                    recompute_mapping: bool = True):
        # Allineamento alla firma di transformers 5.12.1 (modeling_utils.tie_weights);
        # self.vlm e' un modello transformers standard: inoltra gli argomenti.
        return self.vlm.tie_weights(missing_keys, recompute_mapping)

    def resize_token_embeddings(
        self,
        new_num_tokens: Optional[int] = None,
        pad_to_multiple_of: Optional[int] = None,
        mean_resizing: bool = True,
    ) -> nn.Embedding:
        model_embeds = self.vlm.resize_token_embeddings(
            new_num_tokens=new_num_tokens,
            pad_to_multiple_of=pad_to_multiple_of,
            mean_resizing=mean_resizing,
        )

        self.vlm.config.text_config.vocab_size = model_embeds.num_embeddings
        self.vlm.config.vocab_size = model_embeds.num_embeddings
        return model_embeds


__all__ = ["ColQwen3", "ColQwen3PreTrainedModel", "ColQwen3ForRetrievalOutput"]
