import copy

import nnabla as nn

from nnabla_rl.logger import logger

import pathlib


class Model(object):
    def __init__(self, scope_name):
        self._scope_name = scope_name

    @property
    def scope_name(self):
        '''scope_name
        Get scope name of this model.

        Returns:
            scope_name (str): scope name of the model
        '''
        return self._scope_name

    def get_parameters(self, grad_only=True):
        '''get_parameters
        Retrive parameters associated with this model

        Args:
            grad_only (bool): Retrive parameters only with need_grad = True. Defaults to True.

        Returns:
            parameters (OrderedDict): Parameter map.
        '''
        with nn.parameter_scope(self.scope_name):
            return nn.get_parameters(grad_only=grad_only)

    def save_parameters(self, filepath):
        '''save_parameters
        Save model parameters to given filepath.

        Args:
            filepath (str or pathlib.Path): paramter file path
        '''
        if isinstance(filepath, pathlib.Path):
            filepath = str(filepath)
        with nn.parameter_scope(self.scope_name):
            nn.save_parameters(path=filepath)

    def load_parameters(self, filepath):
        '''load_parameters
        Load model parameters from given filepath.

        Args:
            filepath (str or pathlib.Path): paramter file path
        '''
        if isinstance(filepath, pathlib.Path):
            filepath = str(filepath)
        with nn.parameter_scope(self.scope_name):
            nn.load_parameters(path=filepath)

    def deepcopy(self, new_scope_name):
        '''deepcopy
        Create a copy of the model. All the model parameter's (if exist) associated with will be copied.

        Args:
            new_scope_name (str): scope_name of parameters for newly created model

        Returns:
            Model: copied model

        Raises:
            ValueError: Given scope name is same as the model or already exists.
        '''

        if new_scope_name == self._scope_name:
            raise ValueError('Can not use same scope_name!')
        copied = copy.deepcopy(self)
        copied._scope_name = new_scope_name
        # copy current parameter if is already created
        params = self.get_parameters(grad_only=False)
        with nn.parameter_scope(new_scope_name):
            for param_name, param in params.items():
                if nn.parameter.get_parameter(param_name) is not None:
                    raise RuntimeError(f'Model with scope_name: {new_scope_name} already exists!!')
                logger.info(
                    f'copying param with name: {self.scope_name}/{param_name} ---> {new_scope_name}/{param_name}')
                nn.parameter.get_parameter_or_create(param_name, shape=param.shape, initializer=param.d)
        return copied
