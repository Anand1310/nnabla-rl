# Copyright 2021 Sony Group Corporation.
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

from typing import Callable, Optional, Tuple

import numpy as np

import nnabla as nn
import nnabla.functions as NF
import nnabla_rl.functions as RF
from nnabla.initializer import ConstantInitializer
from nnabla.parameter import get_parameter_or_create
from nnabla_rl.initializers import HeUniform


def noisy_net(inp: nn.Variable,
              n_outmap: int,
              base_axis: int = 1,
              w_init: Optional[Callable[[Tuple[int, ...]], np.ndarray]] = None,
              b_init: Optional[Callable[[Tuple[int, ...]], np.ndarray]] = None,
              noisy_w_init: Optional[Callable[[Tuple[int, ...]], np.ndarray]] = None,
              noisy_b_init: Optional[Callable[[Tuple[int, ...]], np.ndarray]] = None,
              fix_parameters: bool = False,
              rng: Optional[np.random.RandomState] = None,
              with_bias: bool = True,
              with_noisy_bias: bool = True,
              apply_w: Optional[Callable[[nn.Variable], nn.Variable]] = None,
              apply_b: Optional[Callable[[nn.Variable], nn.Variable]] = None,
              apply_noisy_w: Optional[Callable[[nn.Variable], nn.Variable]] = None,
              apply_noisy_b: Optional[Callable[[nn.Variable], nn.Variable]] = None,
              seed: int = -1) -> nn.Variable:
    '''
    Noisy linear layer with factorized gaussian noise  proposed by Fortunato et al. in the paper
    "Noisy networks for exploration". See: https://arxiv.org/abs/1706.10295 for details.

    Args:
        inp (nn.Variable): Input of the layer n_outmaps (int): output dimension of the layer.
        n_outmap (int): Output dimension of the layer.
        base_axis (int): Axis of the input to treat as sample dimensions. Dimensions up to base_axis will be treated
            as sample dimensions. Defaults to 1.
        w_init (None or Callable[[Tuple[int, ...]], np.ndarray]): Initializer of weights used in deterministic stream.
            Defaults to None. If None, will be initialized with Uniform distribution
            :math:`(-\\frac{1}{\\sqrt{fanin}},\\frac{1}{\\sqrt{fanin}})`.
        b_init (None or Callable[[Tuple[int, ...]], np.ndarray]): Initializer of bias used in deterministic stream.
            Defaults to None. If None, will be initialized with Uniform distribution
            :math:`(-\\frac{1}{\\sqrt{fanin}},\\frac{1}{\\sqrt{fanin}})`.
        noisy_w_init (None or Callable[[Tuple[int, ...]], np.ndarray]): Initializer of weights used in noisy stream.
            Defaults to None. If None, will be initialized to a constant value of :math:`\\frac{0.5}{\\sqrt{fanin}}`.
        noisy_b_init (None or Callable[[Tuple[int, ...]], np.ndarray]): Initializer of bias used in noisy stream.
            Defaults to None. If None, will be initialized to a constant value of :math:`\\frac{0.5}{\\sqrt{fanin}}`.
        fix_parameters (bool): If True, underlying weight and bias parameters will Not be updated during training.
            Default to False.
        rng (None or np.random.RandomState): Random number generator for parameter initializer. Defaults to None.
        with_bias (bool): If True, deterministic bias term is included in the computation. Defaults to True.
        with_noisy_bias (bool): If True, noisy bias term is included in the computation. Defaults to True.
        apply_w (None or Callable[[nn.Variable], nn.Variable]): Callable object to apply to the weights on
            initialization. Defaults to None.
        apply_b (None or Callable[[nn.Variable], nn.Variable]): Callable object to apply to the bias on
            initialization. Defaults to None.
        apply_noisy_w (None or Callable[[nn.Variable], nn.Variable]):  Callable object to apply to the noisy weight on
            initialization. Defaults to None.
        apply_noisy_b (None or Callable[[nn.Variable], nn.Variable]):  Callable object to apply to the noisy bias on
            initialization. Defaults to None.
        seed (int): Random seed. If -1, seed will be sampled from global random number generator. Defaults to -1.

    Returns:
        nn.Variable: Linearly transformed input with noisy weights
    '''

    inmaps = int(np.prod(inp.shape[base_axis:]))
    if w_init is None:
        w_init = HeUniform(inmaps, n_outmap, factor=1.0/3.0, rng=rng)
    if noisy_w_init is None:
        noisy_w_init = ConstantInitializer(0.5 / np.sqrt(inmaps))
    w = get_parameter_or_create("W", (inmaps, n_outmap), w_init, True, not fix_parameters)
    if apply_w is not None:
        w = apply_w(w)

    noisy_w = get_parameter_or_create("noisy_W", (inmaps, n_outmap), noisy_w_init, True, not fix_parameters)
    if apply_noisy_w is not None:
        noisy_w = apply_noisy_w(noisy_w)

    b = None
    if with_bias:
        if b_init is None:
            b_init = HeUniform(inmaps, n_outmap, factor=1.0/3.0, rng=rng)
        b = get_parameter_or_create("b", (n_outmap, ), b_init, True, not fix_parameters)
        if apply_b is not None:
            b = apply_b(b)

    noisy_b = None
    if with_noisy_bias:
        if noisy_b_init is None:
            noisy_b_init = ConstantInitializer(0.5 / np.sqrt(inmaps))
        noisy_b = get_parameter_or_create("noisy_b", (n_outmap, ), noisy_b_init, True, not fix_parameters)
        if apply_noisy_b is not None:
            noisy_b = apply_noisy_b(noisy_b)

    def _f(x):
        return NF.sign(x) * RF.sqrt(NF.abs(x))

    e_i = _f(NF.randn(shape=(1, inmaps, 1), seed=seed))
    e_j = _f(NF.randn(shape=(1, 1, n_outmap), seed=seed))

    e_w = NF.reshape(NF.batch_matmul(e_i, e_j), shape=noisy_w.shape)
    e_w.need_grad = False
    noisy_w = noisy_w * e_w
    assert noisy_w.shape == w.shape

    if with_noisy_bias:
        assert isinstance(noisy_b, nn.Variable)
        e_b = NF.reshape(e_j, shape=noisy_b.shape)
        e_b.need_grad = False
        noisy_b = noisy_b * e_b
        assert noisy_b.shape == (n_outmap,)
    weight = w + noisy_w

    if with_bias and with_noisy_bias:
        assert isinstance(b, nn.Variable)
        assert isinstance(noisy_b, nn.Variable)
        bias = b + noisy_b
    elif with_bias:
        bias = b
    elif with_noisy_bias:
        bias = noisy_b
    else:
        bias = None
    return NF.affine(inp, weight, bias, base_axis)
