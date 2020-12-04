from typing import Optional, Iterable, Dict

import nnabla as nn
import nnabla.functions as NF

from dataclasses import dataclass

from nnabla_rl.model_trainers.model_trainer import TrainerParam, Training, TrainingVariables, ModelTrainer
from nnabla_rl.models import Model, StochasticPolicy


@dataclass
class SPGPolicyTrainerParam(TrainerParam):
    pi_loss_scalar: float = 1.0
    grad_clip_norm: Optional[float] = None


class SPGPolicyTrainer(ModelTrainer):
    '''Stochastic Policy Gradient (SPG) style Policy Trainer
    Stochastic Policy Gradient is widely known as 'Policy Gradient algorithm'
    '''

    def __init__(self, env_info, params=SPGPolicyTrainerParam()):
        super(SPGPolicyTrainer, self).__init__(env_info, params)
        self._pi_loss = None

    def _update_model(self,
                      models: Iterable[Model],
                      solvers: Dict[str, nn.solver.Solver],
                      experience,
                      training_variables: TrainingVariables,
                      **kwargs):
        (s, a, *_) = experience

        training_variables.s_current.d = s
        training_variables.a_current.d = a

        # update model
        for solver in solvers.values():
            solver.zero_grad()
        self._pi_loss.forward(clear_no_need_grad=True)
        self._pi_loss.backward(clear_buffer=True)
        for solver in solvers.values():
            if self._params.grad_clip_norm is not None:
                solver.clip_grad_by_norm(self._params.grad_clip_norm)
            solver.update()

        errors = {}
        return errors

    def _build_training_graph(self, models: Iterable[Model],
                              training: Training,
                              training_variables: TrainingVariables):
        if not isinstance(models[0], StochasticPolicy):
            raise ValueError

        # Actor optimization graph
        target_value = training.compute_target(training_variables)

        self._pi_loss = 0
        for policy in models:
            distribution = policy.pi(training_variables.s_current)
            log_prob = distribution.log_prob(training_variables.a_current)
            self._pi_loss += NF.sum(-log_prob * target_value) * self._params.pi_loss_scalar

    def _setup_training_variables(self, batch_size) -> TrainingVariables:
        # Training input variables
        s_current_var = nn.Variable((batch_size, *self._env_info.state_shape))
        if self._env_info.is_discrete_action_env():
            action_shape = (batch_size, 1)
        else:
            action_shape = (batch_size, self._env_info.action_dim)
        a_current_var = nn.Variable(action_shape)
        return TrainingVariables(s_current_var, a_current_var)
