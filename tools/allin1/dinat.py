"""This is a modification of:
  https://github.com/huggingface/transformers/blob/main/src/transformers/models/dinat/modeling_dinat.py
  so that it can provide both 1D and 2D attention.
"""

import math
import torch
from abc import ABC,  abstractmethod
from typing import Optional, Tuple, Callable
# NATTEN 0.20+ removed the old natten1dav/natten1dqkrpb/natten2dav/natten2dqkrpb
# functional API in favour of a fused na1d/na2d, whose only CPU backend is torch
# Flex Attention. In eager mode that backend materializes full attention
# matrices: ~11 GB peak RSS for 30 s of audio, OOM-killing real tracks on a
# 16 GB machine. Neighborhood attention with kernel_size=5 doesn't need any of
# that — each query attends to exactly 5 neighbors — so the implementation
# below gathers the k-wide windows directly: memory is O(T·k), not O(T²).
# Bonus over the fused path: the checkpoint's trained relative positional bias
# (rpb) is applied again (the 0.20+ functional API had dropped it).


def _na_windows_1d(length, kernel, device):
    """Per-query window starts + rpb indices for a contiguous 1D sequence.

    NATTEN semantics: a window of exactly `kernel` keys centered on the query,
    shifted (not shrunk) at the sequence edges. For length < kernel the window
    is the whole sequence."""
    kk = min(kernel, length)
    j = torch.arange(length, device=device)
    start = (j - kernel // 2).clamp(0, max(length - kk, 0))
    idx = start[:, None] + torch.arange(kk, device=device)[None, :]   # [T, kk]
    rel = idx - j[:, None] + (kernel - 1)                             # rpb index [T, kk]
    return idx, rel


def _na1d_contiguous(q, k, v, kernel, rpb):
    """Neighborhood attention over [B, T, heads, dim], dilation 1."""
    idx, rel = _na_windows_1d(q.shape[1], kernel, q.device)
    kw, vw = k[:, idx], v[:, idx]                       # [B, T, kk, heads, dim]
    scores = torch.einsum('bthd,btkhd->bthk', q, kw)
    if rpb is not None:
        scores = scores + rpb[:, rel].permute(1, 0, 2)  # [heads,T,kk] → [T,heads,kk]
    attn = scores.softmax(-1)
    return torch.einsum('bthk,btkhd->bthd', attn, vw)


def _regroup_dilated(x, dilation):
    """Split [B, T, ...] into dilation groups as extra batch — two exact batches
    (group lengths differ by at most one), no padding, no masking."""
    t = x.shape[1]
    groups = [torch.arange(r, t, dilation, device=x.device) for r in range(dilation)]
    long_g = [g for g in groups if g.numel() == groups[0].numel()]
    short_g = [g for g in groups if g.numel() != groups[0].numel()]
    batches = []
    for gs in (long_g, short_g):
        if gs:
            idx = torch.stack(gs)                                    # [G, m]
            batches.append((idx, x[:, idx].flatten(0, 1)))           # [B*G, m, ...]
    return batches


def _na1d(q, k, v, kernel, dilation, rpb):
    if dilation <= 1:
        return _na1d_contiguous(q, k, v, kernel, rpb)
    out = torch.empty_like(q)
    b = q.shape[0]
    for idx, qg in _regroup_dilated(q, dilation):
        kg = k[:, idx].flatten(0, 1)
        vg = v[:, idx].flatten(0, 1)
        og = _na1d_contiguous(qg, kg, vg, kernel, rpb)
        out[:, idx] = og.unflatten(0, (b, idx.shape[0]))
    return out


def _na2d(q, k, v, kernel, dilation, rpb):
    """Neighborhood attention over [B, H, W, heads, dim] (dilation 1 in allin1)."""
    if dilation > 1:        # not exercised by the allin1 checkpoint; keep exact
        raise NotImplementedError("2D dilation > 1 not supported by the CPU path")
    bb, hh, ww, nh, dd = q.shape
    idx_h, rel_h = _na_windows_1d(hh, kernel, q.device)               # [hh, kh]
    idx_w, rel_w = _na_windows_1d(ww, kernel, q.device)               # [ww, kw]
    kw_ = k[:, idx_h][:, :, :, idx_w]                # [B, hh, kh, ww, kw, heads, dim]
    vw_ = v[:, idx_h][:, :, :, idx_w]
    kw_ = kw_.permute(0, 1, 3, 2, 4, 5, 6)           # [B, hh, ww, kh, kw, heads, dim]
    vw_ = vw_.permute(0, 1, 3, 2, 4, 5, 6)
    scores = torch.einsum('bxyhd,bxyklhd->bxyhkl', q, kw_)
    if rpb is not None:
        bias = rpb[:, rel_h][..., rel_w]             # [heads, hh, kh, ww, kw]
        scores = scores + bias.permute(1, 3, 0, 2, 4)
    attn = scores.flatten(-2).softmax(-1).view_as(scores)
    return torch.einsum('bxyhkl,bxyklhd->bxyhd', attn, vw_)
from ..config import Config
from .utils import *


# Copied from transformers.models.beit.modeling_beit.drop_path
def drop_path(input, drop_prob=0.0, training=False, scale_by_keep=True):
  """
  Drop paths (Stochastic Depth) per sample (when applied in main path of residual blocks).

  Comment by Ross Wightman: This is the same as the DropConnect impl I created for EfficientNet, etc networks,
  however, the original name is misleading as 'Drop Connect' is a different form of dropout in a separate paper...
  See discussion: https://github.com/tensorflow/tpu/issues/494#issuecomment-532968956 ... I've opted for changing the
  layer and argument names to 'drop path' rather than mix DropConnect as a layer name and use 'survival rate' as the
  argument.
  """
  if drop_prob == 0.0 or not training:
    return input
  keep_prob = 1 - drop_prob
  shape = (input.shape[0],) + (1,) * (input.ndim - 1)  # work with diff dim tensors, not just 2D ConvNets
  random_tensor = keep_prob + torch.rand(shape, dtype=input.dtype, device=input.device)
  random_tensor.floor_()  # binarize
  output = input.div(keep_prob) * random_tensor
  return output


# Copied from transformers.models.beit.modeling_beit.BeitDropPath with Beit->Dinat
class DinatDropPath(nn.Module):
  """Drop paths (Stochastic Depth) per sample (when applied in main path of residual blocks)."""
  
  def __init__(self, drop_prob: Optional[float] = None) -> None:
    super().__init__()
    self.drop_prob = drop_prob
  
  def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
    return drop_path(hidden_states, self.drop_prob, self.training)
  
  def extra_repr(self) -> str:
    return "p={}".format(self.drop_prob)


class _NeighborhoodAttentionNd(ABC, nn.Module):
  # rpb is a trained parameter, loaded from the checkpoint and applied by the
  # windowed attention implementation above (as in NATTEN ≤0.17, which the
  # model was trained with).
  rpb: nn.Parameter
  natten_attn: Callable  # _na1d or _na2d

  def __init__(
    self,
    cfg: Config,
    dim: int,
    num_heads: int,
    kernel_size: int,
    dilation: int
  ):
    super().__init__()
    if dim % num_heads != 0:
      raise ValueError(
        f"The hidden size ({dim}) is not a multiple of the number of attention heads ({num_heads})"
      )

    self.num_attention_heads = num_heads
    self.attention_head_size = int(dim / num_heads)
    self.all_head_size = self.num_attention_heads * self.attention_head_size
    self.kernel_size = kernel_size
    self.dilation = dilation

    self.query = nn.Linear(self.all_head_size, self.all_head_size, bias=cfg.qkv_bias)
    self.key = nn.Linear(self.all_head_size, self.all_head_size, bias=cfg.qkv_bias)
    self.value = nn.Linear(self.all_head_size, self.all_head_size, bias=cfg.qkv_bias)

    self.dropout = nn.Dropout(cfg.drop_attention)

  def forward(
    self,
    hidden_states: torch.Tensor,
    output_attentions: Optional[bool] = False,
  ) -> Tuple[torch.Tensor]:
    query_layer = self.transpose_for_scores(self.query(hidden_states))
    key_layer = self.transpose_for_scores(self.key(hidden_states))
    value_layer = self.transpose_for_scores(self.value(hidden_states))

    # Pre-apply scale; pass scale=1.0 to na1d/na2d to avoid double-scaling.
    query_layer = query_layer / math.sqrt(self.attention_head_size)

    # NATTEN 0.20+ uses heads-last layout: (B, ..., heads, head_dim).
    # transpose_for_scores returns heads-second; permute before the call and
    # the output comes back in heads-last form ready to reshape.
    is_2d = len(query_layer.shape) > 4
    if is_2d:  # (B, heads, H, W, head_dim) → (B, H, W, heads, head_dim)
      q = query_layer.permute(0, 2, 3, 1, 4)
      k = key_layer.permute(0, 2, 3, 1, 4)
      v = value_layer.permute(0, 2, 3, 1, 4)
    else:      # (B, heads, T, head_dim) → (B, T, heads, head_dim)
      q = query_layer.permute(0, 2, 1, 3)
      k = key_layer.permute(0, 2, 1, 3)
      v = value_layer.permute(0, 2, 1, 3)

    context_layer = self.natten_attn(q, k, v, self.kernel_size, self.dilation, self.rpb)

    new_context_layer_shape = context_layer.size()[:-2] + (self.all_head_size,)
    context_layer = context_layer.reshape(new_context_layer_shape)

    return (context_layer,)
  
  def transpose_for_scores(self, x):
    new_x_shape = x.size()[:-1] + (self.num_attention_heads, self.attention_head_size)
    x = x.view(new_x_shape)
    if len(x.shape) > 4:  # 2D
      return x.permute(0, 3, 1, 2, 4)
    else:  # 1D
      return x.permute(0, 2, 1, 3)


class NeighborhoodAttention1d(_NeighborhoodAttentionNd):
  def __init__(
    self,
    cfg: Config,
    dim: int,
    num_heads: int,
    kernel_size: int,
    dilation: int
  ):
    super().__init__(cfg, dim, num_heads, kernel_size, dilation)
    self.rpb = nn.Parameter(
      torch.zeros(num_heads, (2 * self.kernel_size - 1)),
      requires_grad=True,
    )
    self.natten_attn = _na1d


class NeighborhoodAttention2d(_NeighborhoodAttentionNd):
  def __init__(
    self,
    cfg: Config,
    dim: int,
    num_heads: int,
    kernel_size: int,
    dilation: int
  ):
    super().__init__(cfg, dim, num_heads, kernel_size, dilation)
    self.rpb = nn.Parameter(
      torch.zeros(num_heads, (2 * self.kernel_size - 1), (2 * self.kernel_size - 1)),
      requires_grad=True,
    )
    self.natten_attn = _na2d


# Copied from transformers.models.nat.modeling_nat.NeighborhoodAttentionOutput
class NeighborhoodAttentionOutput(nn.Module):
  def __init__(self, config: Config, dim: int):
    super().__init__()
    self.dense = nn.Linear(dim, dim)
    self.dropout = nn.Dropout(config.drop_attention)
  
  def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
    hidden_states = self.dense(hidden_states)
    hidden_states = self.dropout(hidden_states)
    
    return hidden_states


class _NeighborhoodAttentionModuleNd(ABC, nn.Module):
  self: _NeighborhoodAttentionNd
  
  def __init__(self, cfg: Config, dim: int):
    super().__init__()
    # self.self = _NeighborhoodAttentionNd(config, dim, num_heads, kernel_size, dilation)
    self.output = NeighborhoodAttentionOutput(cfg, dim)
  
  def forward(
    self,
    hidden_states: torch.Tensor,
    output_attentions: Optional[bool] = False,
  ) -> Tuple[torch.Tensor]:
    self_outputs = self.self(hidden_states, output_attentions)
    attention_output = self.output(self_outputs[0])
    outputs = (attention_output,) + self_outputs[1:]  # add attentions if we output them
    return outputs


class NeighborhoodAttentionModule1d(_NeighborhoodAttentionModuleNd):
  def __init__(self, cfg: Config, dim: int, num_heads: int, kernel_size: int, dilation: int):
    super().__init__(cfg, dim)
    self.self = NeighborhoodAttention1d(cfg, dim, num_heads, kernel_size, dilation)


class NeighborhoodAttentionModule2d(_NeighborhoodAttentionModuleNd):
  def __init__(self, cfg: Config, dim: int, num_heads: int, kernel_size: int, dilation: int):
    super().__init__(cfg, dim)
    self.self = NeighborhoodAttention2d(cfg, dim, num_heads, kernel_size, dilation)


# Copied from transformers.models.nat.modeling_nat.NatIntermediate with Nat->Dinat
class DinatIntermediate(nn.Module):
  def __init__(self, config: Config, dim_in: int, dim_out: int):
    super().__init__()
    self.dense = nn.Linear(dim_in, dim_out)
    if isinstance(config.act_transformer, str):
      self.intermediate_act_fn = get_activation_function(config.act_transformer)
    else:
      self.intermediate_act_fn = config.act_transformer
  
  def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
    hidden_states = self.dense(hidden_states)
    hidden_states = self.intermediate_act_fn(hidden_states)
    return hidden_states


# Copied from transformers.models.nat.modeling_nat.NatOutput with Nat->Dinat
class DinatOutput(nn.Module):
  def __init__(self, config: Config, dim_in: int, dim_out: int):
    super().__init__()
    self.dense = nn.Linear(dim_in, dim_out)
    self.dropout = nn.Dropout(config.drop_hidden)
  
  def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
    hidden_states = self.dense(hidden_states)
    hidden_states = self.dropout(hidden_states)
    return hidden_states


class _DinatLayerNd(ABC, nn.Module):
  attention: _NeighborhoodAttentionModuleNd
  attention2: Optional[_NeighborhoodAttentionModuleNd]
  
  def __init__(
    self,
    cfg: Config,
    dim: int,
    kernel_size: int,
    dilation: int,
    drop_path_rate: float,
    double_attention: bool,
  ):
    super().__init__()
    self.double_attention = double_attention
    self.kernel_size = kernel_size
    self.dilation = dilation
    self.window_size = self.kernel_size * self.dilation
    if double_attention:
      self.window_size *= 2
    self.layernorm_before = nn.LayerNorm(dim, eps=cfg.layer_norm_eps)
    self.drop_path = DinatDropPath(drop_path_rate) if drop_path_rate > 0.0 else nn.Identity()
    dim_after = dim * 2 if double_attention else dim
    self.layernorm_after = nn.LayerNorm(dim_after, eps=cfg.layer_norm_eps)
    self.intermediate = DinatIntermediate(cfg, dim_after, int(dim_after * cfg.mlp_ratio))
    self.output = DinatOutput(cfg, int(dim_after * cfg.mlp_ratio), dim)
  
  @abstractmethod
  def maybe_pad(self, *args, **kwargs):
    raise NotImplementedError
  
  def forward(
    self,
    hidden_states: torch.Tensor,
    output_attentions: Optional[bool] = False,
  ) -> Tuple[torch.Tensor, torch.Tensor]:
    if len(hidden_states.shape) > 3:
      is_2d = True
      N, K, T, C = hidden_states.size()
    else:
      is_2d = False
      N, T, C = hidden_states.shape
    shortcut = hidden_states
    
    hidden_states = self.layernorm_before(hidden_states)
    # pad hidden_states if they are smaller than kernel size x dilation
    if is_2d:
      hidden_states, pad_values = self.maybe_pad(hidden_states, K, T)
      _, height_pad, width_pad, _ = hidden_states.shape
    else:
      hidden_states, pad_values = self.maybe_pad(hidden_states, T)
    
    attention_inputs = hidden_states
    hidden_states_list = []
    for attention in [self.attention, self.attention2]:
      if attention is None:
        continue
      
      attention_output = attention(attention_inputs, output_attentions=output_attentions)
      attention_output = attention_output[0]
      
      if is_2d:
        was_padded = pad_values[3] > 0 or pad_values[5] > 0
        if was_padded:
          attention_output = attention_output[:, :K, :T, :].contiguous()
      else:
        was_padded = pad_values[3] > 0
        if was_padded:
          attention_output = attention_output[:, :T, :].contiguous()
      
      hidden_states = shortcut + self.drop_path(attention_output)
      hidden_states_list.append(hidden_states)
    
    if self.double_attention:
      hidden_states = torch.cat(hidden_states_list, dim=-1)
      shortcut = torch.stack(hidden_states_list).sum(dim=0) / 2.
    else:
      shortcut = hidden_states
    layer_output = self.layernorm_after(hidden_states)
    layer_output = self.output(self.intermediate(layer_output))
    
    layer_output = shortcut + self.drop_path(layer_output)
    
    # layer_outputs = (layer_output, attention_outputs[1]) if output_attentions else (layer_output,)
    layer_outputs = (layer_output,)
    return layer_outputs


class DinatLayer1d(_DinatLayerNd):
  def __init__(
    self,
    cfg: Config,
    dim: int,
    num_heads: int,
    kernel_size: int,
    dilation: int,
    drop_path_rate: float,
    double_attention: bool,
  ):
    super().__init__(cfg, dim, kernel_size, dilation, drop_path_rate, double_attention)
    self.attention = NeighborhoodAttentionModule1d(cfg, dim, num_heads, kernel_size, dilation)
    if double_attention:
      self.attention2 = NeighborhoodAttentionModule1d(cfg, dim, num_heads, kernel_size, dilation * 2)
    else:
      self.attention2 = None
  
  def maybe_pad(self, hidden_states, frames):
    window_size = self.window_size
    pad_values = (0, 0, 0, 0)
    if frames < window_size:
      pad_l = 0
      pad_r = max(0, window_size - frames)
      pad_values = (0, 0, pad_l, pad_r)
      hidden_states = nn.functional.pad(hidden_states, pad_values)
    return hidden_states, pad_values


class DinatLayer2d(_DinatLayerNd):
  def __init__(
    self,
    cfg: Config,
    dim: int,
    num_heads: int,
    kernel_size: int,
    dilation: int,
    drop_path_rate: float
  ):
    super().__init__(cfg, dim, kernel_size, dilation, drop_path_rate, double_attention=False)
    self.attention = NeighborhoodAttentionModule2d(cfg, dim, num_heads, kernel_size, dilation)
    self.attention2 = None
  
  def maybe_pad(self, hidden_states, height, width):
    window_size = self.window_size
    pad_values = (0, 0, 0, 0, 0, 0)
    if height < window_size or width < window_size:
      pad_l = pad_t = 0
      pad_r = max(0, window_size - width)
      pad_b = max(0, window_size - height)
      pad_values = (0, 0, pad_l, pad_r, pad_t, pad_b)
      hidden_states = nn.functional.pad(hidden_states, pad_values)
    return hidden_states, pad_values
