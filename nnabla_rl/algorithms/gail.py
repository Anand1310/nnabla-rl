import nnabla as nn

import nnabla.solvers as NS
import nnabla.functions as NF

import numpy as np

from dataclasses import dataclass
import random

import gym
from typing import Union, Optional

from nnabla_rl.environment_explorer import EnvironmentExplorer
from nnabla_rl.environments.environment_info import EnvironmentInfo
from nnabla_rl.algorithm import Algorithm, AlgorithmParam, eval_api
from nnabla_rl.builders import VFunctionBuilder, RewardFunctionBuilder, StochasticPolicyBuilder, \
    SolverBuilder, PreprocessorBuilder
from nnabla_rl.preprocessors import Preprocessor
from nnabla_rl.replay_buffer import ReplayBuffer
from nnabla_rl.replay_buffers.buffer_iterator import BufferIterator
from nnabla_rl.utils.data import marshall_experiences
from nnabla_rl.algorithms.common_utils import compute_v_target_and_advantage, _StatePreprocessedPolicy,\
    _StatePreprocessedVFunction, _StatePreprocessedRewardFunction
from nnabla_rl.models \
    import GAILPolicy, GAILVFunction, GAILDiscriminator, StochasticPolicy, VFunction, RewardFunction, Model
from nnabla_rl.model_trainers.model_trainer import ModelTrainer, TrainingBatch
import nnabla_rl.environment_explorers as EE
import nnabla_rl.model_trainers as MT
import nnabla_rl.preprocessors as RP


@dataclass
class GAILParam(AlgorithmParam):
    preprocess_state: bool = True
    act_deterministic_in_eval: bool = True
    discriminator_batch_size: int = 1024
    discriminator_learning_rate: float = 3e-4
    policy_update_interval: int = 1
    discriminator_update_interval: int = 3
    adversary_entropy_coef: float = 0.001
    gamma: float = 0.995
    lmb: float = 0.97
    pi_batch_size: int = 1024
    num_steps_per_iteration: int = 1024
    sigma_kl_divergence_constraint: float = 0.01
    maximum_backtrack_numbers: int = 10
    conjugate_gradient_damping: float = 0.1
    conjugate_gradient_iterations: int = 10
    vf_epochs: int = 5
    vf_batch_size: int = 128
    vf_learning_rate: float = 1e-3

    def __post_init__(self):
        '''__post_init__

        Check the values are in valid range.

        '''
        self._assert_between(self.pi_batch_size, 0, self.num_steps_per_iteration, 'pi_batch_size')
        self._assert_positive(self.discriminator_learning_rate, "discriminator_learning_rate")
        self._assert_positive(self.discriminator_batch_size, "discriminator_batch_size")
        self._assert_positive(self.policy_update_interval, "policy_update_interval")
        self._assert_positive(self.discriminator_update_interval, "discriminator_update_interval")
        self._assert_positive(self.adversary_entropy_coef, "adversarial_entropy_coef")
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


class DefaultPreprocessorBuilder(PreprocessorBuilder):
    def build_preprocessor(self,  # type: ignore[override]
                           scope_name: str,
                           env_info: EnvironmentInfo,
                           algorithm_params: GAILParam,
                           **kwargs) -> Preprocessor:
        return RP.RunningMeanNormalizer(scope_name, env_info.state_shape, value_clip=(-5.0, 5.0))


class DefaultPolicyBuilder(StochasticPolicyBuilder):
    def build_model(self,  # type: ignore[override]
                    scope_name: str,
                    env_info: EnvironmentInfo,
                    algorithm_params: GAILParam,
                    **kwargs) -> StochasticPolicy:
        return GAILPolicy(scope_name, env_info.action_dim)


class DefaultVFunctionBuilder(VFunctionBuilder):
    def build_model(self,  # type: ignore[override]
                    scope_name: str,
                    env_info: EnvironmentInfo,
                    algorithm_params: GAILParam,
                    **kwargs) -> VFunction:
        return GAILVFunction(scope_name)


class DefaultRewardFunctionBuilder(RewardFunctionBuilder):
    def build_model(self,  # type: ignore[override]
                    scope_name: str,
                    env_info: EnvironmentInfo,
                    algorithm_params: GAILParam,
                    **kwargs) -> RewardFunction:
        return GAILDiscriminator(scope_name)


class DefaultVFunctionSolverBuilder(SolverBuilder):
    def build_solver(self,  # type: ignore[override]
                     env_info: EnvironmentInfo,
                     algorithm_params: GAILParam,
                     **kwargs) -> nn.solver.Solver:
        return NS.Adam(alpha=algorithm_params.vf_learning_rate)


class DefaultRewardFunctionSolverBuilder(SolverBuilder):
    def build_solver(self,  # type: ignore[override]
                     env_info: EnvironmentInfo,
                     algorithm_params: GAILParam,
                     **kwargs) -> nn.solver.Solver:
        assert isinstance(algorithm_params, GAILParam)
        return NS.Adam(alpha=algorithm_params.discriminator_learning_rate)


class GAIL(Algorithm):
    ''' Generative Adversarial Imitation Learning
        See: https://arxiv.org/abs/1606.03476.pdf
    '''

    _params: GAILParam
    _v_function: VFunction
    _v_function_solver: nn.solver.Solver
    _policy: StochasticPolicy
    _discriminator: RewardFunction
    _discriminator_solver: nn.solver.Solver
    _environment_explorer: EnvironmentExplorer
    _v_function_trainer: ModelTrainer
    _policy_trainer: ModelTrainer
    _discriminator_trainer: ModelTrainer

    _s_var_label: nn.Variable
    _s_next_var_label: nn.Variable
    _a_var_label: nn.Variable
    _reward: nn.Variable

    def __init__(self, env_or_env_info: Union[gym.Env, EnvironmentInfo],
                 expert_buffer: ReplayBuffer,
                 params=GAILParam(),
                 v_function_builder: VFunctionBuilder = DefaultVFunctionBuilder(),
                 v_solver_builder: SolverBuilder = DefaultVFunctionSolverBuilder(),
                 policy_builder: StochasticPolicyBuilder = DefaultPolicyBuilder(),
                 reward_function_builder: RewardFunctionBuilder = DefaultRewardFunctionBuilder(),
                 reward_solver_builder: SolverBuilder = DefaultRewardFunctionSolverBuilder(),
                 state_preprocessor_builder: Optional[PreprocessorBuilder] = DefaultPreprocessorBuilder()):
        super(GAIL, self).__init__(env_or_env_info, params=params)
        if self._env_info.is_discrete_action_env():
            raise NotImplementedError

        self._expert_buffer = expert_buffer

        policy = policy_builder("pi", self._env_info, self._params)
        v_function = v_function_builder("v", self._env_info, self._params)
        discriminator = reward_function_builder("discriminator", self._env_info, self._params)

        if self._params.preprocess_state:
            if state_preprocessor_builder is None:
                raise ValueError('State preprocessing is enabled but no preprocessor builder is given')
            pi_v_preprocessor = state_preprocessor_builder('pi_v_preprocessor', self._env_info, self._params)
            v_function = _StatePreprocessedVFunction(v_function=v_function, preprocessor=pi_v_preprocessor)
            policy = _StatePreprocessedPolicy(policy=policy, preprocessor=pi_v_preprocessor)
            r_preprocessor = state_preprocessor_builder('r_preprocessor', self._env_info, self._params)
            discriminator = _StatePreprocessedRewardFunction(reward_function=discriminator, preprocessor=r_preprocessor)
            self._pi_v_state_preprocessor = pi_v_preprocessor
            self._r_state_preprocessor = r_preprocessor
        self._v_function = v_function
        self._policy = policy
        self._discriminator = discriminator

        self._v_function_solver = v_solver_builder(self._env_info, self._params)
        self._discriminator_solver = reward_solver_builder(self._env_info, self._params)

    def _before_training_start(self, env_or_buffer):
        self._environment_explorer = self._setup_environment_explorer(env_or_buffer)
        self._v_function_trainer = self._setup_v_function_training(env_or_buffer)
        self._policy_trainer = self._setup_policy_training(env_or_buffer)
        self._discriminator_trainer = self._setup_reward_function_training(env_or_buffer)

    def _setup_environment_explorer(self, env_or_buffer):
        if self._is_buffer(env_or_buffer):
            return None
        explorer_params = EE.RawPolicyExplorerParam(
            initial_step_num=self.iteration_num,
            timelimit_as_terminal=False
        )
        explorer = EE.RawPolicyExplorer(policy_action_selector=self._compute_action,
                                        env_info=self._env_info,
                                        params=explorer_params)
        return explorer

    def _setup_v_function_training(self, env_or_buffer):
        v_function_trainer_params = MT.v_value_trainers.SquaredTDVFunctionTrainerParam(
            reduction_method='mean',
            v_loss_scalar=1.0
        )
        v_function_trainer = MT.v_value_trainers.SquaredTDVFunctionTrainer(
            env_info=self._env_info,
            params=v_function_trainer_params)

        training = MT.v_value_trainings.MonteCarloVValueTraining()
        v_function_trainer.setup_training(
            self._v_function, {self._v_function.scope_name: self._v_function_solver}, training)
        return v_function_trainer

    def _setup_policy_training(self, env_or_buffer):
        policy_trainer_params = MT.policy_trainers.TRPOPolicyTrainerParam(
            sigma_kl_divergence_constraint=self._params.sigma_kl_divergence_constraint,
            maximum_backtrack_numbers=self._params.maximum_backtrack_numbers,
            conjugate_gradient_damping=self._params.conjugate_gradient_damping,
            conjugate_gradient_iterations=self._params.conjugate_gradient_iterations)
        policy_trainer = MT.policy_trainers.TRPOPolicyTrainer(env_info=self._env_info,
                                                              params=policy_trainer_params)
        training = MT.model_trainer.Training()
        policy_trainer.setup_training(self._policy, {}, training)

        return policy_trainer

    def _setup_reward_function_training(self, env_or_buffer):
        reward_function_trainer_params = MT.reward_trainiers.GAILRewardFunctionTrainerParam(
            batch_size=self._params.discriminator_batch_size,
            learning_rate=self._params.discriminator_learning_rate,
            entropy_coef=self._params.adversary_entropy_coef
        )
        reward_function_trainer = MT.reward_trainiers.GAILRewardFunctionTrainer(env_info=self._env_info,
                                                                                params=reward_function_trainer_params)
        training = MT.model_trainer.Training()
        reward_function_trainer.setup_training(
            self._discriminator, {self._discriminator.scope_name: self._discriminator_solver}, training)

        return reward_function_trainer

    @eval_api
    def compute_eval_action(self, s):
        action, _ = self._compute_action(s, act_deterministic=self._params.act_deterministic_in_eval)
        return action

    def _run_online_training_iteration(self, env):
        if self.iteration_num % self._params.num_steps_per_iteration != 0:
            return

        buffer = ReplayBuffer(capacity=self._params.num_steps_per_iteration)

        num_steps = 0
        while num_steps <= self._params.num_steps_per_iteration:
            experience = self._environment_explorer.rollout(env)
            experience = self._label_experience(experience)
            buffer.append(experience)
            num_steps += len(experience)

        self._gail_training(buffer)

    def _label_experience(self, experience):
        labeled_experience = []
        if not hasattr(self, '_s_var_label'):
            # build graph
            self._s_var_label = nn.Variable((1, *self._env_info.state_shape))
            self._s_next_var_label = nn.Variable((1, *self._env_info.state_shape))
            if self._env_info.is_discrete_action_env():
                self._a_var_label = nn.Variable((1, 1))
            else:
                self._a_var_label = nn.Variable((1, self._env_info.action_dim))
            logits_fake = self._discriminator.r(self._s_var_label, self._a_var_label, self._s_next_var_label)
            self._reward = -NF.log(1. - NF.sigmoid(logits_fake) + 1e-8)

        for s, a, _, non_terminal, n_s, info in experience:
            # forward and get reward
            self._s_var_label.d = s.reshape((1, -1))
            self._a_var_label.d = a.reshape((1, -1))
            self._s_next_var_label.d = n_s.reshape((1, -1))
            self._reward.forward()
            transition = (s, a, self._reward.d, non_terminal, n_s, info)
            labeled_experience.append(transition)

        return labeled_experience

    def _run_offline_training_iteration(self, buffer):
        raise NotImplementedError

    def _gail_training(self, buffer):
        buffer_iterator = BufferIterator(buffer, 1, shuffle=False, repeat=False)

        # policy learning
        if self._iteration_num % self._params.policy_update_interval == 0:
            s, a, v_target, advantage = self._align_policy_experiences(buffer_iterator)

            if self._params.preprocess_state:
                self._pi_v_state_preprocessor.update(s)

            self._policy_training(s, a, v_target, advantage)
            self._v_function_training(s, v_target)

        # discriminator learning
        if self._iteration_num % self._params.discriminator_update_interval == 0:
            s_curr_expert, a_curr_expert, s_next_expert, s_curr_agent, a_curr_agent, s_next_agent = \
                self._align_discriminator_experiences(buffer_iterator)

            if self._params.preprocess_state:
                self._r_state_preprocessor.update(np.concatenate([s_curr_agent, s_curr_expert], axis=0))

            self._discriminator_training(s_curr_expert, a_curr_expert, s_next_expert,
                                         s_curr_agent, a_curr_agent, s_next_agent)

    def _align_policy_experiences(self, buffer_iterator):
        v_target_batch, adv_batch = self._compute_v_target_and_advantage(buffer_iterator)

        s_batch, a_batch, _ = self._align_state_and_action(buffer_iterator)

        return s_batch[:self._params.num_steps_per_iteration], \
            a_batch[:self._params.num_steps_per_iteration], \
            v_target_batch[:self._params.num_steps_per_iteration], \
            adv_batch[:self._params.num_steps_per_iteration]

    def _compute_v_target_and_advantage(self, buffer_iterator):
        v_target_batch = []
        adv_batch = []

        buffer_iterator.reset()
        for experiences, *_ in buffer_iterator:
            # length of experiences is 1
            v_target, adv = compute_v_target_and_advantage(
                self._v_function, experiences[0], gamma=self._params.gamma, lmb=self._params.lmb)
            v_target_batch.append(v_target.reshape(-1, 1))
            adv_batch.append(adv.reshape(-1, 1))

        adv_batch = np.concatenate(adv_batch, axis=0)
        v_target_batch = np.concatenate(v_target_batch, axis=0)

        adv_mean = np.mean(adv_batch)
        adv_std = np.std(adv_batch)
        adv_batch = (adv_batch - adv_mean) / adv_std
        return v_target_batch, adv_batch

    def _align_state_and_action(self, buffer_iterator, batch_size=None):
        s_batch = []
        a_batch = []
        s_next_batch = []

        buffer_iterator.reset()
        for experiences, _ in buffer_iterator:
            # length of experiences is 1
            s_seq, a_seq, _, _, s_next_seq, *_ = marshall_experiences(experiences[0])
            s_batch.append(s_seq)
            a_batch.append(a_seq)
            s_next_batch.append(s_next_seq)

        s_batch = np.concatenate(s_batch, axis=0)
        a_batch = np.concatenate(a_batch, axis=0)
        s_next_batch = np.concatenate(s_next_batch, axis=0)

        if batch_size is None:
            return s_batch, a_batch, s_next_batch

        idx = random.sample(list(range(s_batch.shape[0])), batch_size)
        return s_batch[idx], a_batch[idx], s_next_batch[idx]

    def _align_discriminator_experiences(self, buffer_iterator):
        # sample expert data
        expert_experience, _ = self._expert_buffer.sample(self._params.discriminator_batch_size)
        s_expert_batch, a_expert_batch, _, _, s_next_expert_batch, *_ = marshall_experiences(expert_experience)
        # sample agent data
        s_batch, a_batch, s_next_batch = self._align_state_and_action(
            buffer_iterator, batch_size=self._params.discriminator_batch_size)

        return s_expert_batch, a_expert_batch, s_next_expert_batch, s_batch, a_batch, s_next_batch

    def _v_function_training(self, s, v_target):
        num_iterations_per_epoch = self._params.num_steps_per_iteration // self._params.vf_batch_size

        for _ in range(self._params.vf_epochs * num_iterations_per_epoch):
            indices = np.random.randint(0, self._params.num_steps_per_iteration, size=self._params.vf_batch_size)
            batch = TrainingBatch(batch_size=self._params.vf_batch_size,
                                  s_current=s[indices],
                                  extra={'v_target': v_target[indices]})
            self._v_function_trainer.train(batch)

    def _policy_training(self, s, a, v_target, advantage):
        extra = {}
        extra['v_target'] = v_target[:self._params.pi_batch_size]
        extra['advantage'] = advantage[:self._params.pi_batch_size]
        batch = TrainingBatch(batch_size=self._params.pi_batch_size,
                              s_current=s[:self._params.pi_batch_size],
                              a_current=a[:self._params.pi_batch_size],
                              extra=extra)

        self._policy_trainer.train(batch)

    def _discriminator_training(self, s_curr_expert, a_curr_expert, s_next_expert,
                                s_curr_agent, a_curr_agent, s_next_agent):
        extra = {}
        extra['s_current_agent'] = s_curr_agent[:self._params.discriminator_batch_size]
        extra['a_current_agent'] = a_curr_agent[:self._params.discriminator_batch_size]
        extra['s_next_agent'] = s_next_agent[:self._params.discriminator_batch_size]
        extra['s_current_expert'] = s_curr_expert[:self._params.discriminator_batch_size]
        extra['a_current_expert'] = a_curr_expert[:self._params.discriminator_batch_size]
        extra['s_next_expert'] = s_next_expert[:self._params.discriminator_batch_size]

        batch = TrainingBatch(batch_size=self._params.discriminator_batch_size,
                              extra=extra)

        self._discriminator_trainer.train(batch)

    def _compute_action(self, s, act_deterministic=False):
        s = np.expand_dims(s, axis=0)
        if not hasattr(self, '_eval_state_var'):
            self._eval_state_var = nn.Variable(s.shape)
            self._eval_a_distribution = self._policy.pi(self._eval_state_var)

        if act_deterministic:
            eval_a = self._deterministic_action()
        else:
            eval_a = self._probabilistic_action()

        self._eval_state_var.d = s
        eval_a.forward()
        return np.squeeze(eval_a.d, axis=0), {}

    def _deterministic_action(self):
        if not hasattr(self, '_eval_deterministic_a'):
            self._eval_deterministic_a = self._eval_a_distribution.choose_probable()
        return self._eval_deterministic_a

    def _probabilistic_action(self):
        if not hasattr(self, '_eval_probabilistic_a'):
            self._eval_probabilistic_a = self._eval_a_distribution.sample()
        return self._eval_probabilistic_a

    def _models(self):
        models = {}
        models[self._policy.scope_name] = self._policy
        models[self._v_function.scope_name] = self._v_function
        models[self._discriminator.scope_name] = self._discriminator
        if self._params.preprocess_state and isinstance(self._r_state_preprocessor, Model):
            models[self._r_state_preprocessor.scope_name] = self._r_state_preprocessor
        if self._params.preprocess_state and isinstance(self._pi_v_state_preprocessor, Model):
            models[self._pi_v_state_preprocessor.scope_name] = self._pi_v_state_preprocessor
        return models

    def _solvers(self):
        solvers = {}
        solvers[self._v_function.scope_name] = self._v_function_solver
        solvers[self._discriminator.scope_name] = self._discriminator_solver
        return solvers

    @property
    def latest_iteration_state(self):
        latest_iteration_state = super(GAIL, self).latest_iteration_state
        return latest_iteration_state
