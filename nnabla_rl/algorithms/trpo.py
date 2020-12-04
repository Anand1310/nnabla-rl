import nnabla as nn

import nnabla.solvers as NS

import numpy as np

from dataclasses import dataclass

from nnabla_rl.algorithm import Algorithm, AlgorithmParam
from nnabla_rl.replay_buffer import ReplayBuffer
from nnabla_rl.utils.data import marshall_experiences
from nnabla_rl.algorithms.common_utils import compute_v_target_and_advantage
from nnabla_rl.models import TRPOPolicy, TRPOVFunction, StochasticPolicy, VFunction
import nnabla_rl.model_trainers as MT
import nnabla_rl.preprocessors as RP


@dataclass
class TRPOParam(AlgorithmParam):
    gamma: float = 0.995
    lmb: float = 0.97
    num_steps_per_iteration: int = 5000
    sigma_kl_divergence_constraint: float = 0.01
    maximum_backtrack_numbers: int = 10
    conjugate_gradient_damping: float = 0.1
    conjugate_gradient_iterations: int = 20
    vf_epochs: int = 5
    vf_batch_size: int = 64
    vf_learning_rate: float = 1e-3

    def __post_init__(self):
        '''__post_init__

        Check the values are in valid range.

        '''
        self._assert_between(self.gamma, 0.0, 1.0, 'gamma')
        self._assert_between(self.lmb, 0.0, 1.0, 'lmb')
        self._assert_positive(self.num_steps_per_iteration, 'num_steps_per_iteration')
        self._assert_positive(self.sigma_kl_divergence_constraint, 'sigma_kl_divergence_constraint')
        self._assert_positive(self.maximum_backtrack_numbers, 'maximum_backtrack_numbers')
        self._assert_positive(self.conjugate_gradient_damping, 'conjugate_gradient_damping')
        self._assert_positive(self.conjugate_gradient_iterations, 'conjugate_gradient_iterations')
        self._assert_positive(self.vf_epochs, 'vf_epochs')
        self._assert_positive(self.vf_batch_size, 'vf_batch_size')
        self._assert_positive(self.vf_learning_rate, 'vf_learning_rate')


def build_mujoco_state_preprocessor(scope_name, env_info, algorithm_params, **kwargs):
    return RP.RunningMeanNormalizer(scope_name, env_info.state_shape, value_clip=(-5.0, 5.0))


def build_state_preprocessor(preprocessor_builder, scope_name, env_info, algorithm_params, **kwargs):
    builder = build_mujoco_state_preprocessor if preprocessor_builder is None else preprocessor_builder
    return builder(scope_name, env_info, algorithm_params, **kwargs)


def build_default_policy(scope_name, env_info, algorithm_params, **kwargs):
    return TRPOPolicy(scope_name, env_info.state_dim, env_info.action_dim)


def build_default_v_function(scope_name, env_info, algorithm_params, **kwargs):
    return TRPOVFunction(scope_name, env_info.state_dim)


class TRPO(Algorithm):
    """ Trust Region Policy Optimiation method
        with Generalized Advantage Estimation (GAE)
        See: https://arxiv.org/pdf/1502.05477.pdf and
            https://arxiv.org/pdf/1506.02438.pdf
    """

    def __init__(self, env_or_env_info,
                 value_function_builder=build_default_v_function,
                 policy_builder=build_default_policy,
                 state_preprocessor_builder=None,
                 params=TRPOParam()):
        super(TRPO, self).__init__(env_or_env_info, params=params)

        if self._env_info.is_discrete_action_env():
            self._state_preprocessor = None
        else:
            self._state_preprocessor = build_state_preprocessor(state_preprocessor_builder,
                                                                scope_name="preprocessor",
                                                                env_info=self._env_info,
                                                                algorithm_params=self._params)

        if self._env_info.is_discrete_action_env():
            raise NotImplementedError
        else:
            self._policy = policy_builder("pi", self._env_info, self._params)
            self._policy.set_state_preprocessor(self._state_preprocessor)

            self._v_function = value_function_builder("v", self._env_info, self._params)
            self._v_function.set_state_preprocessor(self._state_preprocessor)

        assert isinstance(self._policy, StochasticPolicy)
        assert isinstance(self._v_function, VFunction)
        assert isinstance(self._state_preprocessor, RP.Preprocessor)

        def v_function_solver():
            return NS.Adam(alpha=self._params.vf_learning_rate)
        self._v_function_solver = {self._v_function.scope_name: v_function_solver()}

        self._state = None
        self._action = None
        self._next_state = None
        self._buffer = None

    def _before_training_start(self, env_or_buffer):
        self._v_function_trainer = self._setup_v_function_training(env_or_buffer)
        self._policy_trainer = self._setup_policy_training(env_or_buffer)

    def _setup_v_function_training(self, env_or_buffer):
        v_function_trainer_params = MT.v_value_trainers.SquaredTDVFunctionTrainerParam(
            reduction_method='mean',
            v_loss_scalar=1.0
        )
        v_function_trainer = MT.v_value_trainers.SquaredTDVFunctionTrainer(
            env_info=self._env_info,
            params=v_function_trainer_params)

        training = MT.v_value_trainings.MonteCarloVValueTraining()
        v_function_trainer.setup_training(self._v_function, self._v_function_solver, training)
        return v_function_trainer

    def _setup_policy_training(self, env_or_buffer):
        policy_trainer_params = MT.policy_trainers.TRPOPolicyTrainerParam(
            batch_size=self._params.num_steps_per_iteration,
            num_steps_per_iteration=self._params.num_steps_per_iteration,
            sigma_kl_divergence_constraint=self._params.sigma_kl_divergence_constraint,
            maximum_backtrack_numbers=self._params.maximum_backtrack_numbers,
            conjugate_gradient_damping=self._params.conjugate_gradient_damping,
            conjugate_gradient_iterations=self._params.conjugate_gradient_iterations)
        policy_trainer = MT.policy_trainers.TRPOPolicyTrainer(env_info=self._env_info,
                                                              params=policy_trainer_params)
        training = MT.model_trainer.Training()
        policy_trainer.setup_training(self._policy, {}, training)

        return policy_trainer

    def compute_eval_action(self, s):
        return self._compute_action(s)

    def _run_online_training_iteration(self, env):
        if self.iteration_num % self._params.num_steps_per_iteration != 0:
            return

        self._buffer = ReplayBuffer(capacity=self._params.num_steps_per_iteration)

        num_steps = 0
        while num_steps <= self._params.num_steps_per_iteration:
            experience = self._run_one_episode(env)
            self._buffer.append(experience)
            num_steps += len(experience)

        self._trpo_training(self._buffer)

    def _run_one_episode(self, env):
        self._state = env.reset()
        done = False
        experience = []

        while not done:
            self._action = self._compute_action(self._state)
            self._next_state, r, done, info = env.step(self._action)
            truncated = info.get('TimeLimit.truncated', False)
            if done and not truncated:
                non_terminal = 0.0
            else:
                non_terminal = 1.0

            experience.append((self._state, self._action,
                               r, non_terminal, self._next_state))
            self._state = self._next_state

        return experience

    def _run_offline_training_iteration(self, buffer):
        raise NotImplementedError

    def _trpo_training(self, buffer):
        # sample all experience in the buffer
        experiences, *_ = buffer.sample(len(buffer))
        s_batch, a_batch, v_target_batch, adv_batch = self._align_experiences(experiences)

        if self._state_preprocessor is not None:
            self._state_preprocessor.update(s_batch)

        # v function training
        self._v_function_training(s_batch, v_target_batch)

        # policy training
        self._policy_training(s_batch, a_batch, adv_batch)

    def _align_experiences(self, experiences):
        v_target_batch, adv_batch = self._compute_v_target_and_advantage(experiences)

        s_batch, a_batch = self._align_state_and_action(experiences)

        return s_batch[:self._params.num_steps_per_iteration], \
            a_batch[:self._params.num_steps_per_iteration], \
            v_target_batch[:self._params.num_steps_per_iteration], \
            adv_batch[:self._params.num_steps_per_iteration]

    def _compute_v_target_and_advantage(self, experiences):
        v_target_batch = []
        adv_batch = []
        for experience in experiences:
            v_target, adv = compute_v_target_and_advantage(
                self._v_function, experience, gamma=self._params.gamma, lmb=self._params.lmb)
            v_target_batch.append(v_target.reshape(-1, 1))
            adv_batch.append(adv.reshape(-1, 1))

        adv_batch = np.concatenate(adv_batch, axis=0)
        v_target_batch = np.concatenate(v_target_batch, axis=0)

        adv_mean = np.mean(adv_batch)
        adv_std = np.std(adv_batch)
        adv_batch = (adv_batch - adv_mean) / adv_std
        return v_target_batch, adv_batch

    def _align_state_and_action(self, experiences):
        s_batch = []
        a_batch = []

        for experience in experiences:
            s_seq, a_seq, _, _, _ = marshall_experiences(
                experience)
            s_batch.append(s_seq)
            a_batch.append(a_seq)

        s_batch = np.concatenate(s_batch, axis=0)
        a_batch = np.concatenate(a_batch, axis=0)
        return s_batch, a_batch

    def _v_function_training(self, s_batch, v_target_batch):
        data_size = len(s_batch)
        num_iterations_per_epoch = data_size // self._params.vf_batch_size
        for _ in range(self._params.vf_epochs * num_iterations_per_epoch):
            idx = np.random.randint(0, data_size, size=self._params.vf_batch_size)
            experience = (s_batch[idx], v_target_batch[idx])
            self._v_function_trainer.train(experience)

    def _policy_training(self, s_batch, a_batch, adv_batch):
        experience = (s_batch, a_batch, adv_batch)
        self._policy_trainer.train(experience)

    def _compute_action(self, s):
        s_eval_var = nn.Variable.from_numpy_array(np.expand_dims(s, axis=0))
        with nn.auto_forward():
            distribution = self._policy.pi(s_eval_var)
            eval_action = distribution.sample()
        return eval_action.d.flatten()

    def _models(self):
        models = {}
        models[self._policy.scope_name] = self._policy
        models[self._v_function.scope_name] = self._v_function
        models[self._state_preprocessor.scope_name] = self._state_preprocessor
        return models

    def _solvers(self):
        solvers = {}
        solvers.update(self._v_function_solver)
        return solvers

    @property
    def latest_iteration_state(self):
        latest_iteration_state = super(TRPO, self).latest_iteration_state
        return latest_iteration_state
