import nnabla as nn
import nnabla.functions as NF

import nnabla_rl.random as rl_random


def sample_gaussian(mean, ln_var, noise_clip=None):
    assert isinstance(mean, nn.Variable)
    assert isinstance(ln_var, nn.Variable)
    if not (mean.shape == ln_var.shape):
        raise ValueError('mean and ln_var has different shape')

    noise = randn(shape=mean.shape)
    stddev = NF.exp(ln_var * 0.5)
    if noise_clip is not None:
        noise = NF.clip_by_value(noise, min=noise_clip[0], max=noise_clip[1])
    assert mean.shape == noise.shape
    return mean + stddev * noise


def sample_gaussian_multiple(mean, ln_var, num_samples, noise_clip=None):
    assert isinstance(mean, nn.Variable)
    assert isinstance(ln_var, nn.Variable)
    if not (mean.shape == ln_var.shape):
        raise ValueError('mean and ln_var has different shape')

    batch_size = mean.shape[0]
    data_shape = mean.shape[1:]
    mean = NF.reshape(mean, shape=(batch_size, 1, *data_shape))
    stddev = NF.reshape(NF.exp(ln_var * 0.5),
                        shape=(batch_size, 1, *data_shape))

    output_shape = (batch_size, num_samples, *data_shape)

    noise = randn(shape=output_shape)
    if noise_clip is not None:
        noise = NF.clip_by_value(noise, min=noise_clip[0], max=noise_clip[1])
    sample = mean + stddev * noise
    assert sample.shape == output_shape
    return sample


def expand_dims(x, axis):
    target_shape = (*x.shape[0:axis], 1, *x.shape[axis:])
    return NF.reshape(x, shape=target_shape, inplace=False)


def repeat(x, repeats, axis):
    # TODO: Find more efficient way
    assert isinstance(repeats, int)
    assert axis is not None
    assert axis < len(x.shape)
    reshape_size = (*x.shape[0:axis+1], 1, *x.shape[axis+1:])
    repeater_size = (*x.shape[0:axis+1], repeats, *x.shape[axis+1:])
    final_size = (*x.shape[0:axis], x.shape[axis] * repeats, *x.shape[axis+1:])
    x = NF.reshape(x=x, shape=reshape_size)
    x = NF.broadcast(x, repeater_size)
    return NF.reshape(x, final_size)


def sqrt(x):
    return NF.pow_scalar(x, 0.5)


def std(x, axis=None, keepdims=False):
    # sigma = sqrt(E[(X - E[X])^2])
    mean = NF.mean(x, axis=axis, keepdims=True)
    diff = x - mean
    variance = NF.mean(diff**2, axis=axis, keepdims=keepdims)
    return sqrt(variance)


def argmax(x, axis=None, keepdims=False):
    return NF.max(x=x, axis=axis, keepdims=keepdims, with_index=True, only_index=True)


def quantile_huber_loss(x0, x1, kappa, tau):
    ''' Quantile huber loss
    See following papers for details:
    https://arxiv.org/pdf/1710.10044.pdf
    https://arxiv.org/pdf/1806.06923.pdf
    '''
    u = x0 - x1
    # delta(u < 0)
    delta = NF.less_scalar(u, val=0.0)
    delta.need_grad = False
    assert delta.shape == u.shape

    if kappa <= 0.0:
        return u * (tau - delta)
    else:
        Lk = NF.huber_loss(x0, x1, delta=kappa) * 0.5
        assert Lk.shape == u.shape
        return NF.abs(tau - delta) * Lk / kappa


def mean_squared_error(x0, x1):
    return NF.mean(NF.squared_error(x0, x1))


def minimum_n(variables):
    if len(variables) < 1:
        raise ValueError('Variables must have at least 1 variable')
    if len(variables) == 1:
        return variables[0]
    if len(variables) == 2:
        return NF.minimum2(variables[0], variables[1])

    minimum = NF.minimum2(variables[0], variables[1])
    for variable in variables[2:]:
        minimum = NF.minimum2(minimum, variable)
    return minimum


def rand(low=0, high=1, shape=[], n_outputs=-1, outputs=None):
    '''Wrapper function of rand to fix the seed
    '''
    seed = _sample_seed()
    return NF.rand(low=low, high=high, shape=shape, seed=seed, n_outputs=n_outputs, outputs=outputs)


def randn(mu=0, sigma=1, shape=[], n_outputs=-1, outputs=None):
    '''Wrapper function of randn to fix the seed
    '''
    seed = _sample_seed()
    return NF.randn(mu=mu, sigma=sigma, shape=shape, seed=seed, n_outputs=n_outputs, outputs=outputs)


def random_choice(x, w, shape=[], replace=True, n_outputs=- 1, outputs=None):
    '''Wrapper function random_choice to fix the seed
    '''
    seed = _sample_seed()
    return NF.random_choice(x=x, w=w, shape=shape, seed=seed, replace=replace, n_outputs=n_outputs, outputs=outputs)


def _sample_seed():
    max_32bit_int = 2**31 - 1
    return rl_random.prng.randint(max_32bit_int)
