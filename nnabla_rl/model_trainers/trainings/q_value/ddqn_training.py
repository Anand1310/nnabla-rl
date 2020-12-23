import numpy as np

import nnabla as nn
import nnabla.functions as NF

from nnabla_rl.models import QFunction, ValueDistributionFunction, Model
from nnabla_rl.model_trainers.model_trainer import Training, TrainingVariables


class _QFunctionDDQNTraining(Training):
    def __init__(self, train_function: QFunction, target_function: QFunction):
        self._train_function = train_function
        self._target_function = target_function

    def compute_target(self, training_variables: TrainingVariables, **kwargs) -> nn.Variable:
        gamma = training_variables.gamma
        reward = training_variables.reward
        non_terminal = training_variables.non_terminal
        s_next = training_variables.s_next
        a_next = self._train_function.argmax_q(s_next)
        double_q_target = self._target_function.q(s_next, a_next)
        return reward + gamma * non_terminal * double_q_target


class _ValueDistributionFunctionDDQNTraining(Training):
    def __init__(self,
                 train_function: ValueDistributionFunction,
                 target_function: ValueDistributionFunction):
        self._train_function = train_function
        self._target_function = target_function

    def compute_target(self, training_variables: TrainingVariables, **kwargs) -> nn.Variable:
        batch_size = training_variables.s_next.shape[0]

        gamma = training_variables.gamma
        reward = training_variables.reward
        non_terminal = training_variables.non_terminal
        s_next = training_variables.s_next

        N = self._target_function._num_atoms
        v_max = self._target_function._v_max
        v_min = self._target_function._v_min

        train_atom_probabilities = self._train_function.probabilities(s_next)
        a_star = self._train_function.argmax_q_from_probabilities(train_atom_probabilities)
        target_atom_probabilities = self._target_function.probabilities(s_next)
        pj = self._target_function.probabilities_of(target_atom_probabilities, a_star)

        delta_z = (v_max - v_min) / (N - 1)
        z = np.asarray([v_min + i * delta_z for i in range(N)])
        z = np.broadcast_to(array=z, shape=(batch_size, N))
        z = nn.Variable.from_numpy_array(z)
        target = reward + non_terminal * gamma * z
        Tz = NF.clip_by_value(target, v_min, v_max)
        assert Tz.shape == (batch_size, N)

        mi = self._compute_projection(Tz, pj, N, v_max, v_min)
        return mi

    def _compute_projection(self, Tz, pj, N, v_max, v_min):
        batch_size = Tz.shape[0]
        delta_z = (v_max - v_min) / (N - 1)

        bj = (Tz - v_min) / delta_z
        bj = NF.clip_by_value(bj, 0, N - 1)

        lower = NF.floor(bj)
        upper = NF.ceil(bj)

        ml_indices = lower
        mu_indices = upper

        mi = nn.Variable.from_numpy_array(np.zeros(shape=(batch_size, N), dtype=np.float32))
        # Fix upper - bj = bj - lower = 0 (Prevent not getting both 0. upper - l must always be 1)
        # upper - bj = (1 + lower) - bj
        upper = 1 + lower

        result_upper = NF.scatter_add(mi, ml_indices, pj * (upper - bj), axis=-1)
        result_lower = NF.scatter_add(mi, mu_indices, pj * (bj - lower), axis=-1)

        return (result_upper + result_lower)


class DDQNTraining(Training):
    def __init__(self,
                 train_function: Model,
                 target_function: Model):
        if type(train_function) is not type(target_function):
            raise ValueError

        if isinstance(train_function, ValueDistributionFunction) and \
                isinstance(target_function, ValueDistributionFunction):
            self._delegate = _ValueDistributionFunctionDDQNTraining(train_function, target_function)
        elif isinstance(train_function, QFunction) and isinstance(target_function, QFunction):
            self._delegate = _QFunctionDDQNTraining(train_function, target_function)
        else:
            raise NotImplementedError(f'No training implementation for class: {train_function.__class__}')

    def compute_target(self, training_variables: TrainingVariables, **kwargs) -> nn.Variable:
        return self._delegate.compute_target(training_variables, **kwargs)