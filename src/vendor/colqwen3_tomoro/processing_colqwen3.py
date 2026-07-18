# Provenance:
# Repo: TomoroAI/tomoro-colqwen3-embed-4b
# Revision: bf790bd8780b098b86453444632a184bb770be1a
# License: Apache-2.0
# Reason for vendoring: Required to work with patched configuration_colqwen3.py without trust_remote_code
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

Processing utilities for ColQwen3, aligned with the ColQwen2 reference implementation.
"""

import importlib
import numpy as np
from typing import Any, ClassVar, List, Optional, Tuple, Union

import torch
from PIL import Image
from transformers import BatchEncoding
from transformers.feature_extraction_utils import BatchFeature
from transformers.image_utils import ImageInput, is_valid_image
from transformers.processing_utils import AudioInput, MultiModalData, ProcessingKwargs, ProcessorMixin, Unpack, VideoInput
from transformers.tokenization_utils_base import PreTokenizedInput, TextInput
from transformers.utils import logging

from transformers.models.qwen2_vl.image_processing_qwen2_vl import smart_resize

logger = logging.get_logger(__name__)

try:
    from fast_plaid import search
except ImportError:
    logger.info(
        "FastPlaid is not installed.If you want to use it:Instal with `pip install --no-deps fast-plaid fastkmeans`"
    )


def get_torch_device(device: str = "auto") -> str:
    """Resolve a torch device string with a simple auto mode."""
    if device == "auto":
        if torch.cuda.is_available():
            device = "cuda:0"
        elif torch.backends.mps.is_available():  # for Apple Silicon
            device = "mps"
        else:
            device = "cpu"
    return device


class ColQwen3ProcessorKwargs(ProcessingKwargs, total=False):
    _defaults = {
        "text_kwargs": {
            "padding": "longest",
        },
        "images_kwargs": {
            "data_format": "channels_first",
            "do_convert_rgb": True,
        },
        "videos_kwargs": {
            "return_metadata": True,
            "data_format": "channels_first",
            "do_convert_rgb": True,
        },
        "common_kwargs": {"return_tensors": "pt"},
    }


class ColQwen3Processor(ProcessorMixin):
    """
    Constructs a ColQwen3 processor which wraps a Qwen3VLProcessor with retrieval-specific helpers.
    """

    attributes = ["image_processor", "tokenizer", "video_processor"]
    image_processor_class = "AutoImageProcessor"
    video_processor_class = "AutoVideoProcessor"
    tokenizer_class = ("Qwen2Tokenizer", "Qwen2TokenizerFast")

    def __init__(
        self,
        image_processor=None,
        tokenizer=None,
        video_processor=None,
        chat_template=None,
        visual_prompt_prefix: Optional[str] = None,
        visual_prompt_suffix: Optional[str] = None,
        video_prompt_prefix: Optional[str] = None,
        video_prompt_suffix: Optional[str] = None,
        query_prefix: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(image_processor, tokenizer, video_processor, chat_template=chat_template, **kwargs)
        self.image_token = "<|image_pad|>" if not hasattr(tokenizer, "image_token") else tokenizer.image_token
        self.image_token_id = (
            tokenizer.image_token_id
            if getattr(tokenizer, "image_token_id", None)
            else tokenizer.convert_tokens_to_ids(self.image_token)
        )
        self.video_token = "<|video_pad|>" if not hasattr(tokenizer, "video_token") else tokenizer.video_token
        self.video_token_id = (
            tokenizer.video_token_id
            if getattr(tokenizer, "video_token_id", None)
            else tokenizer.convert_tokens_to_ids(self.video_token)
        )
        self.vision_start_token = (
            "<|vision_start|>" if not hasattr(tokenizer, "vision_start_token") else tokenizer.vision_start_token
        )
        self.vision_end_token = (
            "<|vision_end|>" if not hasattr(tokenizer, "vision_end_token") else tokenizer.vision_end_token
        )
        self.vision_start_token_id = (
            tokenizer.vision_start_token_id
            if getattr(tokenizer, "vision_start_token_id", None)
            else tokenizer.convert_tokens_to_ids(self.vision_start_token)
        )
        self.vision_end_token_id = (
            tokenizer.vision_end_token_id
            if getattr(tokenizer, "vision_end_token_id", None)
            else tokenizer.convert_tokens_to_ids(self.vision_end_token)
        )

        if visual_prompt_prefix is None:
            visual_prompt_prefix = (
                "<|im_start|>user\n<|vision_start|><|image_pad|><|vision_end|>Describe the image."
            )
        self.visual_prompt_prefix = visual_prompt_prefix
        if visual_prompt_suffix is None:
            visual_prompt_suffix = "<|im_end|><|endoftext|>"
        self.visual_prompt_suffix = visual_prompt_suffix

        if video_prompt_prefix is None:
            video_prompt_prefix = (
                "<|im_start|>user\n<|vision_start|><|video_pad|><|vision_end|>Describe the video."
            )
        self.video_prompt_prefix = video_prompt_prefix
        if video_prompt_suffix is None:
            video_prompt_suffix = "<|im_end|><|endoftext|>"
        self.video_prompt_suffix = video_prompt_suffix

        if query_prefix is None:
            query_prefix = ""
        self.query_prefix = query_prefix
        self.tokenizer.padding_side = "left"

    @classmethod
    def from_pretrained(  # type: ignore[override]
        cls,
        *args: Any,
        max_num_visual_tokens: int = 1280,
        **kwargs: Any,
    ) -> "ColQwen3Processor":
        instance = super().from_pretrained(
            *args,
            **kwargs,
        )

        patch_size = getattr(instance.image_processor, "patch_size", None)
        merge_size = getattr(instance.image_processor, "merge_size", None) or getattr(
            instance.image_processor, "spatial_merge_size", None
        )
        if patch_size is None or merge_size is None:
            raise ValueError("Qwen3VL image processor is missing `patch_size` or `merge_size`/`spatial_merge_size`.")
        tile = patch_size * merge_size
        instance.image_processor.max_pixels = max_num_visual_tokens * tile * tile
        instance.image_processor.size["longest_edge"] = instance.image_processor.max_pixels

        video_patch_size = getattr(instance.video_processor, "patch_size", None)
        video_merge_size = getattr(instance.video_processor, "merge_size", None) or getattr(
            instance.video_processor, "spatial_merge_size", None
        )
        video_temporal_patch_size = getattr(instance.video_processor, "temporal_patch_size", None)
        if video_patch_size is None or video_merge_size is None or video_temporal_patch_size is None:
            raise ValueError(
                "Qwen3VL video processor is missing `patch_size`, `merge_size`/`spatial_merge_size`, or `temporal_patch_size`."
            )
        video_tile = video_patch_size * video_merge_size
        # Include temporal patching so the visual token cap applies across space and time.
        instance.video_processor.max_pixels = max_num_visual_tokens * video_tile * video_tile * video_temporal_patch_size
        instance.video_processor.size["longest_edge"] = instance.video_processor.max_pixels

        return instance

    def __call__(
        self,
        images: Optional[ImageInput] = None,
        text: Union[TextInput, PreTokenizedInput, list[TextInput], list[PreTokenizedInput]] = None,
        audio: Optional[AudioInput] = None,
        videos: Optional[VideoInput] = None,
        **kwargs: Unpack[ColQwen3ProcessorKwargs],
    ) -> BatchFeature:
        output_kwargs = self._merge_kwargs(
            ColQwen3ProcessorKwargs,
            tokenizer_init_kwargs=self.tokenizer.init_kwargs,
            **kwargs,
        )
        suffix = output_kwargs["text_kwargs"].pop("suffix", None)
        return_tensors = output_kwargs["text_kwargs"].pop("return_tensors", None)
        return_mm_token_type_ids = output_kwargs["text_kwargs"].pop("return_mm_token_type_ids", True)

        if images is not None and videos is not None:
            raise ValueError("Provide only one of `images` or `videos`, not both.")

        # Normalize text inputs
        text_list: list[str] = []
        if text is not None:
            if isinstance(text, str):
                text_list = [text]
            elif isinstance(text, list):
                if len(text) == 0 or not all(isinstance(t, (str, type(None))) for t in text):
                    raise ValueError("Text must be a string or a list of strings.")
                text_list = [t or "" for t in text]
            else:
                raise ValueError("Text must be a string or a list of strings")

        # Normalize image inputs
        image_list: Optional[list[Any]] = None
        if images is not None:
            raw_images = images if isinstance(images, list) else [images]
            image_list = []
            for idx, img_item in enumerate(raw_images):
                if img_item is None:
                    image_list.append([])
                elif is_valid_image(img_item):
                    image_list.append([img_item])
                elif isinstance(img_item, list):
                    if not img_item:
                        image_list.append([])
                        continue
                    for sub_idx, sub_img in enumerate(img_item):
                        if not is_valid_image(sub_img):
                            raise ValueError(f"Image at position {idx}[{sub_idx}] is not a valid image.")
                    image_list.append(list(img_item))
                else:
                    raise ValueError("images must be an image, list of images or list of list of images")

        # Normalize video inputs
        video_list: Optional[list[Any]] = None
        if videos is not None:
            raw_videos = list(videos) if isinstance(videos, (list, tuple)) else [videos]
            video_list = []
            for idx, vid_item in enumerate(raw_videos):
                if vid_item is None:
                    video_list.append([])
                elif isinstance(vid_item, list):
                    video_list.append(list(vid_item))
                else:
                    video_list.append([vid_item])

        if image_list is None and video_list is None and not text_list:
            raise ValueError("Either text, images or videos must be provided")

        # Align text length with provided vision inputs when needed
        if image_list is not None:
            if not text_list:
                text_list = [""] * len(image_list)
            elif len(text_list) == 1 and len(image_list) > 1:
                text_list = text_list * len(image_list)
            elif len(text_list) != len(image_list):
                raise ValueError("When providing both images and text, their lengths must match.")
            num_items = len(image_list)
        elif video_list is not None:
            if not text_list:
                text_list = [""] * len(video_list)
            elif len(text_list) == 1 and len(video_list) > 1:
                text_list = text_list * len(video_list)
            elif len(text_list) != len(video_list):
                raise ValueError("When providing both videos and text, their lengths must match.")
            num_items = len(video_list)
        else:
            num_items = len(text_list)

        if num_items == 0:
            raise ValueError("Either text, images or videos must be provided")

        prompts: list[str] = []
        query_suffix = suffix if suffix is not None else self.query_augmentation_token * 10

        for idx in range(num_items):
            extra_text = (text_list[idx] if idx < len(text_list) else "") or ""
            extra_text = extra_text.strip()
            has_image = image_list is not None and len(image_list[idx]) > 0
            has_video = video_list is not None and len(video_list[idx]) > 0
            if has_image and has_video:
                raise ValueError("Provide only one of `images` or `videos` per item.")

            if has_image:
                prompt = (
                    f"{self.visual_prompt_prefix} {extra_text}{self.visual_prompt_suffix}"
                    if extra_text
                    else f"{self.visual_prompt_prefix}{self.visual_prompt_suffix}"
                )
                prompts.append(prompt)
            elif has_video:
                prompt = (
                    f"{self.video_prompt_prefix} {extra_text}{self.video_prompt_suffix}"
                    if extra_text
                    else f"{self.video_prompt_prefix}{self.video_prompt_suffix}"
                )
                prompts.append(prompt)
            else:
                prompt = self.query_prefix + extra_text + query_suffix
                prompts.append(prompt)

        # Process images (excluding empty placeholders)
        image_inputs: dict[str, Any] = {}
        image_grid_thw = None
        if image_list is not None:
            normalized_images: list[list[Image.Image]] = []
            for idx, img_group in enumerate(image_list):
                converted_list: list[Image.Image] = []
                for sub_idx, sub_img in enumerate(img_group):
                    if not is_valid_image(sub_img):
                        raise ValueError(f"Image at position {idx}[{sub_idx}] is not a valid image.")
                    converted_list.append(sub_img.convert("RGB") if hasattr(sub_img, "convert") else sub_img)
                normalized_images.append(converted_list)

            image_inputs = self.image_processor(images=normalized_images, **output_kwargs["images_kwargs"])
            image_grid_thw = image_inputs["image_grid_thw"]

        # Process videos (excluding empty placeholders)
        videos_inputs: dict[str, Any] = {}
        video_grid_thw = None
        video_metadata = None
        if video_list is not None:
            videos_inputs = self.video_processor(videos=video_list, **output_kwargs["videos_kwargs"])
            video_grid_thw = videos_inputs["video_grid_thw"]
            if "return_metadata" not in output_kwargs["videos_kwargs"]:
                video_metadata = videos_inputs.pop("video_metadata")
            else:
                video_metadata = videos_inputs["video_metadata"]

        # Expand prompts to match the number of visual tokens
        text_prompts = prompts.copy()
        if image_grid_thw is not None:
            merge_size = getattr(self.image_processor, "merge_size", None) or getattr(
                self.image_processor, "spatial_merge_size", None
            )
            if merge_size is None:
                raise ValueError("Qwen3VL image processor is missing `merge_size`/`spatial_merge_size`.")
            merge_length = merge_size**2
            index = 0
            for i in range(len(text_prompts)):
                while self.image_token in text_prompts[i]:
                    if index >= len(image_grid_thw):
                        raise ValueError("Number of image tokens does not match provided images.")
                    num_image_tokens = image_grid_thw[index].prod() // merge_length
                    text_prompts[i] = text_prompts[i].replace(
                        self.image_token, "<|placeholder|>" * num_image_tokens, 1
                    )
                    index += 1
                text_prompts[i] = text_prompts[i].replace("<|placeholder|>", self.image_token)

        if video_grid_thw is not None:
            merge_size = getattr(self.video_processor, "merge_size", None)
            if merge_size is None:
                raise ValueError("Qwen3VL video processor is missing `merge_size`.")
            merge_length = merge_size**2
            index = 0
            for i in range(len(text_prompts)):
                while self.video_token in text_prompts[i]:
                    if video_metadata is None or index >= len(video_metadata):
                        raise ValueError("Video metadata is required to build video prompts.")
                    metadata = video_metadata[index]
                    if metadata.fps is None:
                        logger.warning_once(
                            "Qwen3VL requires frame timestamps to construct prompts, but the `fps` of the input video could "
                            "not be inferred. Defaulting to `fps=24`. Please provide `video_metadata` for more accurate results."
                        )
                        metadata.fps = 24 if metadata.fps is None else metadata.fps

                    curr_timestamp = self._calculate_timestamps(
                        metadata.frames_indices, metadata.fps, self.video_processor.merge_size
                    )
                    frame_seqlen = int(video_grid_thw[index][1:].prod().item() // merge_length)
                    video_placeholder = ""
                    for frame_idx in range(int(video_grid_thw[index][0])):
                        curr_time = curr_timestamp[frame_idx]
                        video_placeholder += f"<{curr_time:.1f} seconds>"
                        video_placeholder += (
                            self.vision_start_token + "<|placeholder|>" * frame_seqlen + self.vision_end_token
                        )

                    if f"{self.vision_start_token}{self.video_token}{self.vision_end_token}" in text_prompts[i]:
                        text_prompts[i] = text_prompts[i].replace(
                            f"{self.vision_start_token}{self.video_token}{self.vision_end_token}",
                            video_placeholder,
                            1,
                        )
                    else:
                        text_prompts[i] = text_prompts[i].replace(self.video_token, video_placeholder, 1)
                    index += 1

                text_prompts[i] = text_prompts[i].replace("<|placeholder|>", self.video_token)

        text_inputs = self.tokenizer(text_prompts, **output_kwargs["text_kwargs"])
        self._check_special_mm_tokens(text_prompts, text_inputs, modalities=["image", "video"])

        if return_mm_token_type_ids:
            array_ids = np.array(text_inputs["input_ids"])
            mm_token_type_ids = np.zeros_like(text_inputs["input_ids"])
            mm_token_type_ids[array_ids == self.image_token_id] = 1
            text_inputs["mm_token_type_ids"] = mm_token_type_ids.tolist()

        return BatchFeature(data={**text_inputs, **image_inputs, **videos_inputs}, tensor_type=return_tensors)

    def process_images(
        self,
        images: List[Image.Image],
    ) -> Union[BatchFeature, BatchEncoding]:
        images = [image.convert("RGB") for image in images]
        return self(images=images, padding="longest", return_tensors="pt")

    def process_texts(self, texts: List[str]) -> Union[BatchFeature, BatchEncoding]:
        return self(text=texts, return_tensors="pt", padding="longest")


    @staticmethod
    def _split_batch_feature(batch_feature: BatchFeature) -> list[BatchFeature]:
        # Split a batched BatchFeature into a list of per-item BatchFeatures.
        length: Optional[int] = None
        for value in batch_feature.values():
            if hasattr(value, "__len__"):
                try:
                    length = len(value)
                except Exception:
                    continue
            if length is not None:
                break

        if length is None:
            return [batch_feature]

        items: list[BatchFeature] = []
        for idx in range(length):
            data = {}
            for key, value in batch_feature.items():
                try:
                    data[key] = value[idx]
                except Exception:
                    data[key] = value
            items.append(BatchFeature(data=data))
        return items

    @staticmethod
    def _merge_batch_features(features: list[BatchFeature]) -> BatchFeature:
        if not features:
            return BatchFeature()

        all_keys = set()
        for feat in features:
            all_keys.update(feat.keys())

        merged: dict[str, list[Any]] = {key: [] for key in all_keys}
        for feat in features:
            for key in all_keys:
                merged[key].append(feat.get(key))

        combined: dict[str, Any] = {}
        for key, values in merged.items():
            # Prefer stacking tensors so callers get batched tensors instead of lists
            if all(isinstance(v, torch.Tensor) for v in values):
                try:
                    combined[key] = torch.stack(values)
                    continue
                except Exception:
                    # Fallback to list if shapes are incompatible for stacking
                    pass
            combined[key] = values

        return BatchFeature(data=combined)

    def score_retrieval(
        self,
        qs: List[torch.Tensor],
        ps: List[torch.Tensor],
        score_batch_size: int = 128,
        device: Optional[Union[str, torch.device]] = None,
        **kwargs,
    ) -> torch.Tensor:
        return self.score_multi_vector(qs, ps, batch_size=score_batch_size, device=device, **kwargs)

    @staticmethod
    def score_single_vector(
        qs: Union[torch.Tensor, List[torch.Tensor]],
        ps: Union[torch.Tensor, List[torch.Tensor]],
        device: Optional[Union[str, torch.device]] = None,
    ) -> torch.Tensor:
        """
        Compute the dot product score for the given single-vector query and passage embeddings.
        """
        device = device or get_torch_device("auto")

        if isinstance(qs, list) and isinstance(ps, list):
            if len(qs) == 0:
                raise ValueError("No queries provided")
            if len(ps) == 0:
                raise ValueError("No passages provided")

            qs = torch.stack(qs).to(device)
            ps = torch.stack(ps).to(device)
        else:
            qs = qs.to(device)
            ps = ps.to(device)

        scores = torch.einsum("bd,cd->bc", qs, ps)
        assert scores.shape[0] == len(qs), f"Expected {len(qs)} scores, got {scores.shape[0]}"

        scores = scores.to(torch.float32)
        return scores

    @staticmethod
    def score_multi_vector(
        qs: Union[torch.Tensor, List[torch.Tensor]],
        ps: Union[torch.Tensor, List[torch.Tensor]],
        batch_size: int = 128,
        device: Optional[Union[str, torch.device]] = None,
    ) -> torch.Tensor:
        """
        Compute the late-interaction/MaxSim score (ColBERT-like) for the given multi-vector
        query embeddings (`qs`) and passage embeddings (`ps`). For ColPali, a passage is the
        image of a document page.

        Because the embedding tensors are multi-vector and can thus have different shapes, they
        should be fed as:
        (1) a list of tensors, where the i-th tensor is of shape (sequence_length_i, embedding_dim)
        (2) a single tensor of shape (n_passages, max_sequence_length, embedding_dim) -> usually
            obtained by padding the list of tensors.

        Args:
            qs (`Union[torch.Tensor, List[torch.Tensor]`): Query embeddings.
            ps (`Union[torch.Tensor, List[torch.Tensor]`): Passage embeddings.
            batch_size (`int`, *optional*): Batch size for computing scores.
            device (`Union[str, torch.device]`, *optional*): Device to use for computation. If not
                provided, uses `get_torch_device("auto")`.

        Returns:
            `torch.Tensor`: A tensor of shape `(n_queries, n_passages)` containing the scores. The score
            tensor is saved on the "cpu" device.
        """
        device = device or get_torch_device("auto")

        if len(qs) == 0:
            raise ValueError("No queries provided")
        if len(ps) == 0:
            raise ValueError("No passages provided")

        scores_list: List[torch.Tensor] = []

        for i in range(0, len(qs), batch_size):
            scores_batch = []
            qs_batch = torch.nn.utils.rnn.pad_sequence(qs[i : i + batch_size], batch_first=True, padding_value=0).to(
                device
            )
            for j in range(0, len(ps), batch_size):
                ps_batch = torch.nn.utils.rnn.pad_sequence(
                    ps[j : j + batch_size], batch_first=True, padding_value=0
                ).to(device)
                scores_batch.append(torch.einsum("bnd,csd->bcns", qs_batch, ps_batch).max(dim=3)[0].sum(dim=2))
            scores_batch = torch.cat(scores_batch, dim=1).cpu()
            scores_list.append(scores_batch)

        scores = torch.cat(scores_list, dim=0)
        assert scores.shape[0] == len(qs), f"Expected {len(qs)} scores, got {scores.shape[0]}"

        scores = scores.to(torch.float32)
        return scores

    @staticmethod
    def get_topk_plaid(
        qs: Union[torch.Tensor, List[torch.Tensor]],
        plaid_index: "search.FastPlaid",
        k: int = 10,
        batch_size: int = 128,
        device: Optional[Union[str, torch.device]] = None,
    ) -> torch.Tensor:
        """
        Experimental: Compute the late-interaction/MaxSim score (ColBERT-like) for the given multi-vector
        query embeddings (`qs`) and passage embeddings endoded in a plaid index. For ColPali, a passage is the
        image of a document page.
        """
        device = device or get_torch_device("auto")

        if len(qs) == 0:
            raise ValueError("No queries provided")

        scores_list: List[torch.Tensor] = []

        for i in range(0, len(qs), batch_size):
            scores_batch = []
            qs_batch = torch.nn.utils.rnn.pad_sequence(qs[i : i + batch_size], batch_first=True, padding_value=0).to(
                device
            )
            scores_batch = plaid_index.search(
                queries_embeddings=qs_batch.to(torch.float32),
                top_k=k,
            )
            scores_list.append(scores_batch)

        return scores_list

    @staticmethod
    def create_plaid_index(
        ps: Union[torch.Tensor, List[torch.Tensor]],
        device: Optional[Union[str, torch.device]] = None,
    ) -> torch.Tensor:
        """
        Experimental: Create a FastPlaid index from the given passage embeddings.
        Args:
            ps (`Union[torch.Tensor, List[torch.Tensor]]`): Passage embeddings. Should be a list of tensors,
                where each tensor is of shape (sequence_length_i, embedding_dim).
            device (`Optional[Union[str, torch.device]]`, *optional*): Device to use for computation. If not
                provided, uses `get_torch_device("auto")`.
        """
        if not importlib.util.find_spec("fast_plaid"):
            raise ImportError("FastPlaid is not installed. Please install it with `pip install fast-plaid`.")

        fast_plaid_index = search.FastPlaid(index="index")
        device = device or get_torch_device("auto")
        fast_plaid_index.create(documents_embeddings=[d.to(device).to(torch.float32) for d in ps])
        return fast_plaid_index

    def get_n_patches(
        self,
        image_size: Tuple[int, int],
        spatial_merge_size: int,
    ) -> Tuple[int, int]:
        """
        Get the number of patches (n_patches_x, n_patches_y) that will be used to process an image of
        size (height, width) with the given patch size.

        The `spatial_merge_size` is the number of patches that will be merged spatially. It is stored in
        as a `Qwen2VLForConditionalGeneration` attribute under `model.spatial_merge_size`.
        """
        patch_size = self.image_processor.patch_size

        height_new, width_new = smart_resize(
            width=image_size[0],
            height=image_size[1],
            factor=patch_size * self.image_processor.merge_size,
            min_pixels=self.image_processor.size["shortest_edge"],
            max_pixels=self.image_processor.size["longest_edge"],
        )

        n_patches_x = width_new // patch_size // spatial_merge_size
        n_patches_y = height_new // patch_size // spatial_merge_size

        return n_patches_x, n_patches_y

    def get_image_mask(self, batch_images: BatchFeature) -> torch.Tensor:
        return batch_images.input_ids == self.image_token_id

    def _get_num_multimodal_tokens(self, image_sizes=None, **kwargs):
        vision_data = {}
        if image_sizes is not None:
            images_kwargs = ColQwen3ProcessorKwargs._defaults.get("images_kwargs", {})
            images_kwargs.update(kwargs)
            merge_size = images_kwargs.get("merge_size", None) or getattr(
                self.image_processor, "merge_size", None
            ) or getattr(self.image_processor, "spatial_merge_size", None)
            if merge_size is None:
                raise ValueError("Qwen3VL image processor is missing `merge_size`/`spatial_merge_size`.")

            num_image_patches = [
                self.image_processor.get_number_of_image_patches(*image_size, images_kwargs)
                for image_size in image_sizes
            ]
            num_image_tokens = [(num_patches // merge_size**2) for num_patches in num_image_patches]
            vision_data.update({"num_image_tokens": num_image_tokens, "num_image_patches": num_image_patches})

        video_sizes = kwargs.pop("video_sizes", None)
        if video_sizes is not None:
            videos_kwargs = ColQwen3ProcessorKwargs._defaults.get("videos_kwargs", {})
            videos_kwargs.update(kwargs)
            merge_size = videos_kwargs.get("merge_size", None) or getattr(self.video_processor, "merge_size", None)
            if merge_size is None:
                raise ValueError("Qwen3VL video processor is missing `merge_size`.")

            num_video_patches = [
                self.video_processor.get_number_of_video_patches(*video_size, videos_kwargs) for video_size in video_sizes
            ]
            num_video_tokens = [(num_patches // merge_size**2) for num_patches in num_video_patches]
            vision_data.update({"num_video_tokens": num_video_tokens, "num_video_patches": num_video_patches})

        return MultiModalData(**vision_data)

    @property
    def model_input_names(self) -> list[str]:
        return [
            "input_ids",
            "attention_mask",
            "pixel_values",
            "image_grid_thw",
            "pixel_values_videos",
            "video_grid_thw",
        ]

    @property
    def query_augmentation_token(self) -> str:
        return self.tokenizer.pad_token

    def get_video_mask(self, batch_videos: BatchFeature) -> torch.Tensor:
        return batch_videos.input_ids == self.video_token_id

    def _calculate_timestamps(
        self, indices: Union[list[int], np.ndarray], video_fps: float, merge_size: int = 2
    ) -> list[float]:
        if not isinstance(indices, list):
            indices = indices.tolist()
        if len(indices) % merge_size != 0:
            indices.extend(indices[-1] for _ in range(merge_size - len(indices) % merge_size))
        timestamps = [idx / video_fps for idx in indices]
        timestamps = [
            (timestamps[i] + timestamps[i + merge_size - 1]) / 2 for i in range(0, len(timestamps), merge_size)
        ]
        return timestamps


__all__ = ["ColQwen3Processor", "ColQwen3ProcessorKwargs"]
