from abc import ABCMeta, abstractmethod

from typing import Optional, Iterable, Union, Dict

from dataclasses import dataclass

import nnabla as nn

from nnabla_rl.parameter import Parameter
from nnabla_rl.environments.environment_info import EnvironmentInfo
from nnabla_rl.models import Model
from nnabla_rl.utils.data import convert_to_list_if_not_iterable


@dataclass
class TrainerParam(Parameter):
    def __post_init__(self):
        super(TrainerParam, self).__post_init__()


@dataclass
class TrainingVariables():
    s_current: Optional[nn.Variable] = None
    a_current: Optional[nn.Variable] = None
    reward: Optional[nn.Variable] = None
    gamma: Optional[nn.Variable] = None
    non_terminal: Optional[nn.Variable] = None
    s_next: Optional[nn.Variable] = None

    @property
    def batch_size(self):
        return self.s_current.shape[0]


class ModelTrainer(metaclass=ABCMeta):
    def __init__(self, env_info: EnvironmentInfo, params: TrainerParam):
        self._env_info = env_info
        self._params = params

        self._models: Iterable[Model] = None
        self._solvers: Dict[str, nn.solver.Solver] = None
        self._train_count: int = 0
        self._training: 'Training' = None
        self._training_variables: TrainingVariables = None

    def train(self, experience, **kwargs) -> Dict:
        if self._models is None:
            raise RuntimeError('Call setup_training() first. Model is not set!')
        self._train_count += 1

        experience = self._training.setup_experience(experience)
        (s, *_) = experience

        new_batch_size = s.shape[0]
        prev_batch_size = self._training_variables.batch_size
        if new_batch_size != prev_batch_size:
            self._training_variables = self._setup_training_variables(new_batch_size)
            self._build_training_graph(self._models, self._training, self._training_variables)

        self._training.before_update(self._train_count)
        error_info = self._update_model(self._models, self._solvers, experience, self._training_variables, **kwargs)
        self._training.after_update(self._train_count)

        return error_info

    def setup_training(self,
                       models: Union[Model, Iterable[Model]],
                       solvers: Dict[str, nn.solver.Solver],
                       training: 'Training'):
        self._models = convert_to_list_if_not_iterable(models)
        self._solvers = solvers
        self._training = training

        # Initially create traning variables with batch_size 1.
        # The batch_size will be updated later depending on the given experience data
        self._training_variables = self._setup_training_variables(1)

        self._build_training_graph(self._models, self._training, self._training_variables)

        self._setup_solver()

    def set_learning_rate(self, new_learning_rate):
        for solver in self._solvers.values():
            solver.set_learning_rate(new_learning_rate)

    @abstractmethod
    def _update_model(self,
                      models: Iterable[Model],
                      solvers: Dict[str, nn.solver.Solver],
                      experience,
                      training_variables: TrainingVariables,
                      **kwargs) -> Dict:
        raise NotImplementedError

    @abstractmethod
    def _build_training_graph(self,
                              models: Iterable[Model],
                              training: 'Training',
                              training_variables: TrainingVariables):
        raise NotImplementedError

    @abstractmethod
    def _setup_training_variables(self, batch_size) -> TrainingVariables:
        raise NotImplementedError

    def _setup_solver(self):
        for model in self._models:
            if model.scope_name in self._solvers.keys():
                solver = self._solvers[model.scope_name]
                # Set retain_state = True and prevent overwriting loaded state (If it is loaded)
                solver.set_parameters(model.get_parameters(), reset=False, retain_state=True)


class Training():
    def __init__(self):
        pass

    def setup_experience(self, experience):
        return experience

    def before_update(self, train_count: int):
        pass

    def after_update(self, train_count: int):
        pass

    def compute_target(self, training_variables: TrainingVariables, **kwargs) -> nn.Variable:
        pass


class TrainingExtension(Training):
    def __init__(self, training):
        self._training = training

    def setup_experience(self, experience):
        return self._training.setup_experience(experience)

    def before_update(self, train_count: int):
        self._training.before_update(train_count)

    def after_update(self, train_count: int):
        self._training.after_update(train_count)

    def compute_target(self, training_variables: TrainingVariables, **kwargs) -> nn.Variable:
        return self._training.compute_target(training_variables, **kwargs)
