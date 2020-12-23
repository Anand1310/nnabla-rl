from typing import Iterable, Dict

import nnabla as nn
import nnabla.functions as NF

from dataclasses import dataclass

from nnabla_rl.model_trainers.model_trainer import \
    TrainerParam, Training, TrainingBatch, TrainingVariables, ModelTrainer
from nnabla_rl.models import ValueDistributionFunction, Model


@dataclass
class C51ValueDistributionFunctionTrainerParam(TrainerParam):
    v_min: float = -10.0
    v_max: float = 10.0
    num_atoms: int = 51


class C51ValueDistributionFunctionTrainer(ModelTrainer):
    def __init__(self,
                 env_info,
                 params: C51ValueDistributionFunctionTrainerParam):
        super(C51ValueDistributionFunctionTrainer, self).__init__(env_info, params)
        self._model: ValueDistributionFunction = None
        self._env_info = env_info

        # Training loss/output
        self._kl_loss = None
        self._cross_entropy_loss = None

    def _update_model(self,
                      models: Iterable[Model],
                      solvers: Dict[str, nn.solver.Solver],
                      batch: TrainingBatch,
                      training_variables: TrainingVariables,
                      **kwargs) -> Dict:
        training_variables.s_current.d = batch.s_current
        training_variables.a_current.d = batch.a_current
        training_variables.reward.d = batch.reward
        training_variables.non_terminal.d = batch.non_terminal
        training_variables.gamma.d = batch.gamma
        training_variables.s_next.d = batch.s_next
        training_variables.weight.d = batch.weight

        for solver in solvers.values():
            solver.zero_grad()
        self._cross_entropy_loss.forward()
        self._cross_entropy_loss.backward()
        for solver in solvers.values():
            solver.update()
        errors = {}
        # Kullbuck Leibler divergence is not actually the td_error itself
        # but is used for prioritizing the replay buffer and we save it as 'td_error' for convenience
        # See: https://arxiv.org/pdf/1710.02298.pdf
        errors['td_error'] = self._kl_loss.d.copy()
        return errors

    def _build_training_graph(self,
                              models: Iterable[Model],
                              training: 'Training',
                              training_variables: TrainingVariables):
        for model in models:
            assert isinstance(model, ValueDistributionFunction)
        # Computing the target probabilities
        mi = self._training.compute_target(training_variables)
        mi.need_grad = False

        batch_size = training_variables.batch_size
        cross_entropy_loss = 0
        for model in models:
            atom_probabilities = model.probabilities(self._training_variables.s_current)
            atom_probabilities = model._probabilities_of(atom_probabilities, self._training_variables.a_current)
            atom_probabilities = NF.clip_by_value(atom_probabilities, 1e-10, 1.0)
            cross_entropy = mi * NF.log(atom_probabilities)
            assert cross_entropy.shape == (batch_size, self._params.num_atoms)
            # This kl_Loss value is same as the cross entropy but we name it as kl_loss for convenience to use for
            # prioritized experience replay
            # See: https://arxiv.org/pdf/1710.02298.pdf
            # keep kl_loss only for the last model for prioritized replay
            kl_loss = -NF.sum(cross_entropy, axis=1, keepdims=True)

            # Sum over models
            cross_entropy_loss += NF.mean(kl_loss * training_variables.weight)
        self._kl_loss = kl_loss
        self._kl_loss.persistent = True
        self._cross_entropy_loss = cross_entropy_loss

    def _setup_training_variables(self, batch_size) -> TrainingVariables:
        # Training input variables
        s_current_var = nn.Variable((batch_size, *self._env_info.state_shape))
        a_current_var = nn.Variable((batch_size, 1))
        s_next_var = nn.Variable((batch_size, *self._env_info.state_shape))
        reward_var = nn.Variable((batch_size, 1))
        gamma_var = nn.Variable((1, 1))
        non_terminal_var = nn.Variable((batch_size, 1))
        s_next_var = nn.Variable((batch_size, *self._env_info.state_shape))
        weight_var = nn.Variable((batch_size, 1))

        training_variables = TrainingVariables(batch_size=batch_size,
                                               s_current=s_current_var,
                                               a_current=a_current_var,
                                               reward=reward_var,
                                               gamma=gamma_var,
                                               non_terminal=non_terminal_var,
                                               s_next=s_next_var,
                                               weight=weight_var)
        return training_variables