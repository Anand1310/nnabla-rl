import nnabla as nn
import nnabla.functions as NF

import numpy as np

import nnabla_rl.functions as RF
from nnabla_rl.distributions import Distribution
from nnabla_rl.distributions import common_utils


class SquashedGaussian(Distribution):
    '''
    Gaussian distribution which squashed the output sampled from N(mu, var) with tanh.
    '''

    def __init__(self, mean, ln_var):
        super(SquashedGaussian, self).__init__()
        if not isinstance(mean, nn.Variable):
            mean = nn.Variable.from_numpy_array(mean)
        if not isinstance(ln_var, nn.Variable):
            ln_var = nn.Variable.from_numpy_array(ln_var)

        self._mean = mean
        self._var = NF.exp(ln_var)
        self._ln_var = ln_var

    def sample(self, noise_clip=None):
        x = RF.sample_gaussian(mean=self._mean,
                               ln_var=self._ln_var,
                               noise_clip=noise_clip)
        return NF.tanh(x)

    def sample_multiple(self, num_samples, noise_clip=None):
        x = RF.sample_gaussian_multiple(self._mean,
                                        self._ln_var,
                                        num_samples,
                                        noise_clip=noise_clip)
        return NF.tanh(x)

    def sample_and_compute_log_prob(self, noise_clip=None):
        '''
        NOTE: In order to avoid sampling different random values for sample and log_prob,
        you'll need to use nnabla.forward_all(sample, log_prob)
        If you forward the two variables independently, you'll get a log_prob for different sample,
        since different random variables are sampled internally.
        '''
        x = RF.sample_gaussian(
            mean=self._mean, ln_var=self._ln_var, noise_clip=noise_clip)
        log_prob = self._log_prob_internal(
            x, self._mean, self._var, self._ln_var)
        return NF.tanh(x), log_prob

    def sample_multiple_and_compute_log_prob(self, num_samples, noise_clip=None):
        '''
        NOTE: In order to avoid sampling different random values for sample and log_prob,
        you'll need to use nnabla.forward_all(sample, log_prob)
        If you forward the two variables independently, you'll get a log_prob for different sample,
        since different random variables are sampled internally.
        '''
        x = RF.sample_gaussian_multiple(self._mean,
                                        self._ln_var,
                                        num_samples=num_samples,
                                        noise_clip=noise_clip)
        mean = RF.expand_dims(self._mean, axis=1)
        var = RF.expand_dims(self._var, axis=1)
        ln_var = RF.expand_dims(self._ln_var, axis=1)

        assert mean.shape == (x.shape[0], 1, x.shape[-1])
        assert var.shape == mean.shape
        assert ln_var.shape == mean.shape

        log_prob = self._log_prob_internal(x, mean, var, ln_var)
        return NF.tanh(x), log_prob

    def choose_probable(self):
        return NF.tanh(self._mean)

    def log_prob(self, x):
        x = NF.atanh(x)
        return self._log_prob_internal(x, self._mean, self._var, self._ln_var)

    def _log_prob_internal(self, x, mean, var, ln_var):
        axis = len(x.shape) - 1
        gaussian_part = common_utils.gaussian_log_prob(x, mean, var, ln_var)
        adjust_part = NF.sum(self._log_determinant_jacobian(x), axis=axis, keepdims=True)
        return gaussian_part - adjust_part

    def _log_determinant_jacobian(self, x):
        # arctanh(y)' = 1/(1 - y^2) (y=tanh(x))
        # Below computes log(1 - tanh(x)^2)
        # For derivation see:
        # https://github.com/tensorflow/probability/blob/master/tensorflow_probability/python/bijectors/tanh.py
        return 2.0 * (np.log(2.0) - x - NF.softplus(-2.0 * x))
