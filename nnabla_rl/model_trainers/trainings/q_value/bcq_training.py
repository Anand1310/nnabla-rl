# Copyright (c) 2021 Sony Corporation. All Rights Reserved.
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

from typing import Sequence, Union, cast

import nnabla as nn
import nnabla.functions as NF
import nnabla_rl.functions as RNF
from nnabla_rl.model_trainers.model_trainer import Training, TrainingVariables
from nnabla_rl.models import DeterministicPolicy, Model, QFunction
from nnabla_rl.utils.data import convert_to_list_if_not_list


class _QFunctionBCQTraining(Training):
    # type declarations to type check with mypy
    # NOTE: declared variables are instance variable and NOT class variable, unless it is marked with ClassVar
    # See https://mypy.readthedocs.io/en/stable/class_basics.html for details
    _target_functions: Sequence[QFunction]
    _target_policy: DeterministicPolicy
    _num_action_samples: int
    _lmb: float

    def __init__(self,
                 target_functions: Sequence[QFunction],
                 target_policy: DeterministicPolicy,
                 num_action_samples: int,
                 lmb: float):
        self._target_functions = target_functions
        self._target_policy = target_policy
        self._num_action_samples = num_action_samples
        self._lmb = lmb

    def compute_target(self, training_variables: TrainingVariables, **kwargs) -> nn.Variable:
        gamma = training_variables.gamma
        reward = training_variables.reward
        non_terminal = training_variables.non_terminal
        s_next = training_variables.s_next

        batch_size = training_variables.batch_size
        s_next_rep = RNF.repeat(x=s_next, repeats=self._num_action_samples, axis=0)
        a_next_rep = self._target_policy.pi(s_next_rep)
        q_values = NF.stack(*(q_target.q(s_next_rep, a_next_rep) for q_target in self._target_functions))
        num_q_ensembles = len(self._target_functions)
        assert q_values.shape == (num_q_ensembles, batch_size * self._num_action_samples, 1)
        weighted_q_minmax = self._lmb * NF.min(q_values, axis=0) + (1.0 - self._lmb) * NF.max(q_values, axis=0)
        assert weighted_q_minmax.shape == (batch_size * self._num_action_samples, 1)

        next_q_value = NF.max(NF.reshape(weighted_q_minmax, shape=(batch_size, -1)), axis=1, keepdims=True)
        assert next_q_value.shape == (batch_size, 1)
        return reward + gamma * non_terminal * next_q_value


class BCQTraining(Training):
    # type declarations to type check with mypy
    # NOTE: declared variables are instance variable and NOT class variable, unless it is marked with ClassVar
    # See https://mypy.readthedocs.io/en/stable/class_basics.html for details
    _delegate: Training

    def __init__(self,
                 train_functions: Union[Model, Sequence[Model]],
                 target_functions: Union[Model, Sequence[Model]],
                 target_policy: DeterministicPolicy,
                 num_action_samples: int,
                 lmb: float):
        train_functions = convert_to_list_if_not_list(train_functions)
        target_functions = convert_to_list_if_not_list(target_functions)
        train_function = train_functions[0]
        target_function = target_functions[0]
        if isinstance(train_function, QFunction) and isinstance(target_function, QFunction):
            target_functions = cast(Sequence[QFunction], target_functions)
            self._delegate = _QFunctionBCQTraining(target_functions, target_policy, num_action_samples, lmb)
        else:
            raise NotImplementedError(f'No training implementation for class: {target_function.__class__}')

    def compute_target(self, training_variables: TrainingVariables, **kwargs) -> nn.Variable:
        return self._delegate.compute_target(training_variables, **kwargs)
